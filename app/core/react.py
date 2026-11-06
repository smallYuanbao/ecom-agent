"""只读 ReAct harness（P5-9a）。

**只读**：白名单写死在代码里，模型看不到 `create_ticket` / `submit_refund`。
**4 步封顶**：最后一步不带工具，强制基于已有结果回答。
**不重试**：工具循环的下一步会自然纠偏；兜底路径上重试只会放大延迟。

固定管道：

    白名单 → 必填校验 → 丢弃 user_id / 未知参数 → 类型归一
           → asyncio.wait_for(timeout) → ToolResult
           → 结果截断 → span
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.core.format import yuan_text
from app.core.llm import ToolChatReply, tool_chat
from app.core.retrieval import search as search_kb
from app.core.tracer import record_span
from app.db.models import utcnow
from app.tools import orders as order_tools
from app.tools.orders import ToolResult

logger = logging.getLogger(__name__)

MAX_STEPS = 4
MAX_RESULT_CHARS = 2000
HISTORY_MESSAGES = 6


ToolHandler = Callable[[dict, str], Awaitable[ToolResult | dict]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    required: tuple[str, ...]
    handler: ToolHandler
    timeout: float = 5.0
    # 字段级归一规则，由执行器统一处理，不写 if/elif
    str_fields: tuple[str, ...] = ()
    upper_fields: tuple[str, ...] = ()
    # 字段 -> (默认值, 最小值, 最大值)
    int_fields: dict[str, tuple[int, int, int]] = field(default_factory=dict)


def _spec(
    name: str,
    description: str,
    properties: dict,
    required: tuple[str, ...],
    handler: ToolHandler,
    *,
    timeout: float = 5.0,
    str_fields: tuple[str, ...] = (),
    upper_fields: tuple[str, ...] = (),
    int_fields: dict[str, tuple[int, int, int]] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": list(required),
        },
        required=required,
        handler=handler,
        timeout=timeout,
        str_fields=str_fields,
        upper_fields=upper_fields,
        int_fields=int_fields or {},
    )


def _with_amount_text(data: dict) -> dict:
    """订单金额从「分」换成「元」，别让模型直接念 124900。"""
    out = dict(data)
    amount = out.pop("amount", None)
    if amount is not None:
        out["amount_text"] = yuan_text(int(amount))
    return out


def _with_price_text(data: dict) -> dict:
    """库存价格同样换成「元」；rows 逐行处理。"""
    out = dict(data)
    if isinstance(out.get("rows"), list):
        rows = []
        for row in out["rows"]:
            item = dict(row)
            price = item.pop("price", None)
            if price is not None:
                item["price_text"] = yuan_text(int(price))
            rows.append(item)
        out["rows"] = rows
        return out
    price = out.pop("price", None)
    if price is not None:
        out["price_text"] = yuan_text(int(price))
    return out


async def _query_order(args: dict, user_id: str) -> ToolResult | dict:
    result = await order_tools.query_order(args["order_id"], user_id=user_id)
    if result.ok and result.data:
        return ToolResult(True, None, _with_amount_text(result.data))
    return result


async def _query_logistics(args: dict, user_id: str) -> ToolResult | dict:
    order = await order_tools.query_order(args["order_id"], user_id=user_id)
    if not order.ok:
        return order
    tracking_no = (order.data or {}).get("tracking_no")
    if not tracking_no:
        return ToolResult(False, "not_found", {"message": "该订单暂无物流信息"})
    return await order_tools.query_logistics(tracking_no, user_id=user_id)


async def _query_stock(args: dict, user_id: str) -> ToolResult | dict:
    result = await order_tools.query_stock(args["sku_id"], args.get("spec"))
    if result.ok and result.data:
        return ToolResult(True, None, _with_price_text(result.data))
    return result


async def _search_kb(args: dict, user_id: str) -> ToolResult | dict:
    hits = await search_kb(args["query"], top_k=args["top_k"])
    return ToolResult(True, None, {"hits": hits})


TOOL_SPECS: dict[str, ToolSpec] = {
    "query_order": _spec(
        "query_order",
        "按订单号查询订单状态、金额、下单时间、物流单号。用户问某笔订单时使用。",
        {
            "order_id": {
                "type": "string",
                "description": "4 位以上订单号，例如 1001",
            }
        },
        ("order_id",),
        _query_order,
        str_fields=("order_id",),
    ),
    "query_logistics": _spec(
        "query_logistics",
        "按订单号查询物流轨迹。内部会先取物流单号再查轨迹，直接传订单号即可。",
        {
            "order_id": {
                "type": "string",
                "description": "4 位以上订单号，例如 1001",
            }
        },
        ("order_id",),
        _query_logistics,
        str_fields=("order_id",),
    ),
    "query_stock": _spec(
        "query_stock",
        "按型号查询库存、价格与货态。可再传规格缩小范围。",
        {
            "sku_id": {"type": "string", "description": "商品型号，例如 ST-20"},
            "spec": {"type": "string", "description": "规格，例如 星光灰；不确定可不传"},
        },
        ("sku_id",),
        _query_stock,
        upper_fields=("sku_id",),
        str_fields=("spec",),
    ),
    "search_kb": _spec(
        "search_kb",
        "检索售后政策与规则知识库。只有检索到证据时才依据它回答，并标注引用编号。",
        {
            "query": {"type": "string", "description": "检索关键词或问题"},
            "top_k": {
                "type": "integer",
                "description": "返回条数，1-5，默认 3",
                "minimum": 1,
                "maximum": 5,
            },
        },
        ("query",),
        _search_kb,
        str_fields=("query",),
        int_fields={"top_k": (3, 1, 5)},
    ),
}

TOOL_WHITELIST: tuple[str, ...] = tuple(TOOL_SPECS)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }
    for spec in TOOL_SPECS.values()
]

REACT_SYSTEM = """你是电商客服的兜底助手。你可以调用只读工具查真实数据，再回答用户。

规则：
1. 只读：不要尝试下单、退款、建单等写操作。
2. 工具能查到的，按工具结果回答；查不到就如实说，不要编造。
3. 最多 4 步；信息够了就回答，不要反复调工具。
4. search_kb 有证据才答，关键结论标 [1][2]；证据不足就说「暂时没有查到」。
5. 金额/价格看 `amount_text` / `price_text`，不要用分；不承诺时效、赔偿；不确定就引导人工。
6. 工具报错时，按错误说明修正参数或如实告知，不要换一个假答案。
"""


@dataclass(frozen=True)
class ReactResult:
    answer: str
    steps: int
    tool_calls: list[dict]
    citations: list[str]
    code: str                     # ok / step_limit / error


def _content(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "…(截断)"
    return text


def _assistant_message(reply: ToolChatReply) -> dict:
    message: dict = {"role": "assistant", "content": reply.content or ""}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.args, ensure_ascii=False),
                },
            }
            for call in reply.tool_calls
        ]
    return message


def _normalize(result: ToolResult | dict) -> dict:
    if isinstance(result, ToolResult):
        payload: dict = {"code": result.code or "ok"}
        if result.data is not None:
            payload["data"] = result.data
        return payload
    return result


def _normalize_args(spec: ToolSpec, args: dict) -> dict:
    """按 ToolSpec 的字段元数据做统一归一，不在执行器里写 per-tool 分支。"""
    for key in spec.str_fields:
        if args.get(key) is not None:
            args[key] = str(args[key])
    for key in spec.upper_fields:
        if args.get(key) is not None:
            args[key] = str(args[key]).upper()
    for key, (default, low, high) in spec.int_fields.items():
        try:
            value = int(args.get(key, default))
        except (TypeError, ValueError):
            value = default
        args[key] = max(low, min(value, high))
    return args


async def execute_tool(name: str, args: dict, *, user_id: str) -> dict:
    """执行一个工具调用；never-raises，错误转成 `{"code": ...}` 回灌。"""
    spec = TOOL_SPECS.get(name)
    if spec is None:
        return {"code": "unknown_tool", "message": f"未知工具 {name}"}

    # 只保留 schema 里声明的参数：模型传的 user_id 一律丢弃
    cleaned = {
        key: value
        for key, value in (args or {}).items()
        if key in spec.parameters["properties"]
    }
    missing = [key for key in spec.required if not cleaned.get(key)]
    if missing:
        return {"code": "invalid_arg", "message": f"缺少参数：{', '.join(missing)}"}

    cleaned = _normalize_args(spec, cleaned)

    try:
        result = await asyncio.wait_for(
            spec.handler(cleaned, user_id), timeout=spec.timeout
        )
    except TimeoutError:
        return {"code": "timeout", "message": "工具执行超时"}
    except Exception:
        logger.warning("工具执行失败 name=%s", name, exc_info=True)
        return {"code": "internal", "message": "工具暂时不可用"}

    return _normalize(result)


async def _span(
    trace_id: str | None,
    name: str,
    start: datetime,
    end: datetime,
    attrs: dict,
) -> None:
    if not trace_id:
        return
    await record_span(
        trace_id=trace_id, name=name, start_at=start, end_at=end, attrs=attrs
    )


async def _finish(
    result: ReactResult,
    *,
    trace_id: str | None,
    started: datetime,
) -> ReactResult:
    await _span(
        trace_id,
        "react:total",
        started,
        utcnow(),
        {
            "steps": result.steps,
            "code": result.code,
            "tool_calls": len(result.tool_calls),
            "citations": len(result.citations),
        },
    )
    return result


async def run(
    query: str,
    *,
    history: list[dict] | None = None,
    user_id: str,
    trace_id: str | None = None,
) -> ReactResult:
    """跑一次只读 ReAct，最多 `MAX_STEPS` 步。"""
    started = utcnow()
    messages: list[dict] = [{"role": "system", "content": REACT_SYSTEM}]
    for item in (history or [])[-HISTORY_MESSAGES:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": query})

    tool_calls_log: list[dict] = []
    citations: list[str] = []

    for step in range(1, MAX_STEPS + 1):
        forced = step == MAX_STEPS
        step_started = utcnow()
        try:
            reply = await tool_chat(messages, [] if forced else TOOLS)
        except Exception:
            logger.warning("ReAct 模型调用失败", exc_info=True)
            return await _finish(
                ReactResult("", step, tool_calls_log, citations, "error"),
                trace_id=trace_id,
                started=started,
            )

        if not reply.tool_calls:
            answer = (reply.content or "").strip()
            await _span(
                trace_id,
                f"react:step:{step}",
                step_started,
                utcnow(),
                {"tools": [], "codes": [], "forced": forced},
            )
            code = "step_limit" if forced else ("ok" if answer else "error")
            return await _finish(
                ReactResult(answer, step, tool_calls_log, citations, code),
                trace_id=trace_id,
                started=started,
            )

        message = _assistant_message(reply)
        messages.append(message)
        codes: list[str] = []
        for call in reply.tool_calls:
            result = await execute_tool(call.name, call.args, user_id=user_id)
            code = result.get("code") or "ok"
            codes.append(code)
            tool_calls_log.append({"name": call.name, "code": code})
            if call.name == "search_kb":
                for hit in (result.get("data") or {}).get("hits") or []:
                    cid = hit.get("id")
                    if cid and cid not in citations:
                        citations.append(cid)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": _content(result),
                }
            )

        await _span(
            trace_id,
            f"react:step:{step}",
            step_started,
            utcnow(),
            {
                "tools": [call.name for call in reply.tool_calls],
                "codes": codes,
                "forced": forced,
            },
        )

        if forced:
            # 正常不会走到：最后一步没有给工具。真出现 tool_calls 就到此为止
            return await _finish(
                ReactResult("", step, tool_calls_log, citations, "step_limit"),
                trace_id=trace_id,
                started=started,
            )

    # 理论到不了：循环最多 MAX_STEPS 次
    return await _finish(
        ReactResult("", MAX_STEPS, tool_calls_log, citations, "step_limit"),
        trace_id=trace_id,
        started=started,
    )
