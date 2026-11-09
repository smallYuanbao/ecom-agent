"""LLM 调用封装：结构化输出只走 function_calling。

设计依据 T5 = openai 官方 SDK。三条经验来自参考实现的实测踩坑：

1. **结构化只用 function_calling，不用 json_schema**——模型多吐思维链会把 JSON 带崩。
2. **偶发失败原地重试一次，不降级到更差的通路**——两次都失败就抛给调用点，
   由调用点决定降级成什么安全默认值（参考实现原话：「别在这里降级到一条更差的通路上」）。
3. **中转通道优先流式**——非流式中转层可能把 `tool_calls.arguments` 拼坏。
   本模块做的是短输出结构化抽取，暂用非流式；流式生成在 Ch02 Task 7 单独实现。

**异常边界**（刻意区分，不要一律吞掉）：

| 异常 | 含义 | 处理 |
|---|---|---|
| `LLMFormatError` | 模型没按格式返回（可恢复，重试或降级） | 调用点降级成安全默认值 |
| 网络/鉴权异常 | 密钥错、超时、限流（不可恢复） | 直接上抛，由 API 层回 `error` 帧 |
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

# 单次结构化调用的超时（秒）。没有它，一次卡住会拖垮整轮 30s 预算（C5）
TIMEOUT_SECONDS = 30.0

# 结构化输出总尝试次数（首次 + 重试一次）
MAX_ATTEMPTS = 2


class LLMFormatError(Exception):
    """模型返回了，但不符合约定格式：没有 tool_calls、JSON 解析失败、缺必填字段。"""


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.chat_api_key,
        base_url=settings.chat_base_url or None,
        timeout=TIMEOUT_SECONDS,
    )


async def structured_call(
    tool_name: str,
    parameters: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    timeout: float | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, Any]:
    """强制模型以工具调用的形式返回结构化结果。

    `tool_choice` 指定具体函数 → 模型必须调用它，不会改回自然语言作答。
    temperature 默认 0：分类这类任务要可复现，抖动没有价值。

    `timeout` / `max_attempts` 供短路径覆盖：语义闸在用户等待路径上，
    必须 3s 内放弃且不重试，不能沿用分类任务的 30s + 重试。
    """
    tools = [
        {
            "type": "function",
            "function": {"name": tool_name, "parameters": parameters},
        }
    ]
    client = get_client()
    model = get_settings().chat_model

    last_error: Exception | None = None
    for _ in range(max(1, max_attempts)):
        request = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": tool_name}},
            temperature=temperature,
        )
        response = (
            await asyncio.wait_for(request, timeout=timeout)
            if timeout is not None
            else await request
        )
        message = response.choices[0].message
        if not message.tool_calls:
            last_error = LLMFormatError("模型未返回 tool_calls")
            continue
        try:
            return json.loads(message.tool_calls[0].function.arguments)
        except json.JSONDecodeError as exc:  # 模型偶尔会插 markdown 或多余字符
            last_error = LLMFormatError(f"arguments 不是合法 JSON: {exc}")
            continue

    raise last_error or LLMFormatError("结构化调用失败")


async def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
) -> str:
    """普通文本生成（不强制结构化）。返回模型输出的正文。

    与 `structured_call` 的区别：这里要的是一段自然语言回答，不是 JSON。
    **不重试**——生成失败通常是服务问题（超时/限流/鉴权），重试只是把
    同一个错误延迟一次；由 API 层统一回 `error` 帧更清楚。
    """
    client = get_client()
    response = await client.chat.completions.create(
        model=get_settings().chat_model,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


# ── ReAct 工具循环的一步（Ch05 Task 9）────────────────────────


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolChatReply:
    content: str
    tool_calls: list[ToolCall]


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        # 模型偶尔会吐不合法 JSON；当成「没给参数」，
        # 由 ReAct 的必填校验回灌错误，让它下一轮修正。
        logger.warning("tool_calls.arguments 不是合法 JSON，按空参数处理")
        return {}
    return value if isinstance(value, dict) else {}


async def tool_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
) -> ToolChatReply:
    """多轮工具调用的一步：返回文本或 tool_calls。

    - `tools` 为空时不传 `tools` 字段，强制模型直接回答（用于步数用尽时收敛）；
    - **不重试**：工具循环的下一步会自然纠偏；网络/鉴权错误照常上抛。
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": get_settings().chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    response = await client.chat.completions.create(**kwargs)
    message = response.choices[0].message
    calls = [
        ToolCall(
            id=call.id,
            name=call.function.name,
            args=_parse_arguments(call.function.arguments),
        )
        for call in (message.tool_calls or [])
    ]
    return ToolChatReply(content=message.content or "", tool_calls=calls)
