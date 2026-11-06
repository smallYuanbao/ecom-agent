"""消息持久化、`db-{id}` 锚点与历史窗口（M1/M2）。

**存全量**是为了回放与审计；送模型时只取窗口，控制成本与延迟。
锚点约定：`id=f"db-{row.id}"`，摘要/滑窗边界都用 DB id 表示，不用「第几条」。
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import chat
from app.core.prompts import PROMPT_GUARD, SUMMARY_SYSTEM
from app.db.repo_conversation import set_summary
from app.db.repo_message import append_message, list_messages

logger = logging.getLogger(__name__)

# ── 窗口预算：数值可调，公式固定 ─────────────────────────────
# 自设上限远低于模型真实窗口；按块预留，避免加证据/加提示时把历史挤没。
CONTEXT_TOKENS = 8000
SYSTEM_RESERVE = 1200          # 系统提示 + 工具 schema
EVIDENCE_RESERVE = 2000        # 知识出口的检索证据
SUMMARY_RESERVE = 600          # 摘要段
OUTPUT_RESERVE = 800           # 输出预留
HISTORY_BUDGET = (
    CONTEXT_TOKENS - SYSTEM_RESERVE - EVIDENCE_RESERVE - SUMMARY_RESERVE - OUTPUT_RESERVE
)

MAX_TOOL_CHARS = 200           # 工具消息进历史前压成一行
SUMMARY_TIMEOUT = 3.0          # 摘要失败不阻断本轮
MIN_KEEP_MESSAGES = 4          # 至少保留最近 2 轮原文


@dataclass(frozen=True)
class Window:
    """送模型的历史窗口。"""

    messages: list[dict]
    summary: str | None
    token_estimate: int
    truncated: bool


def estimate_tokens(text: str) -> int:
    """中文为主，保守按 1 字符 ≈ 1 token。

    不能用 4 字符/token（英文口径）——中文会低估约 4 倍。
    英文被高估是可接受的，总比超窗口强。
    """
    return len(text or "")


def _anchor(msg_id: int) -> str:
    return f"db-{msg_id}"


def _parse_meta(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        # 坏 meta 只影响回放展示，不影响消息本身
        logger.warning("消息 meta 不是合法 JSON，已忽略")
        return None
    return value if isinstance(value, dict) else None


async def save_user_message(
    cid: int,
    text: str | None,
    *,
    client_message_id: str | None = None,
    meta: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
) -> int:
    """落用户消息，返回消息 id。

    `client_message_id` 由调用方提供时，重复调用返回首次写入的 id（K4 幂等）。
    结构化点选（`selected_action`）放在 `meta` 里，消息内容可以为空。
    """
    return await append_message(
        cid,
        "user",
        text or None,
        client_message_id=client_message_id,
        meta=meta,
        session=session,
    )


async def save_assistant_message(
    cid: int,
    text: str | None,
    *,
    meta: dict[str, Any] | None = None,
    status: str = "completed",
    session: AsyncSession | None = None,
) -> int:
    """落助手消息，返回消息 id。失败时用 `status="error"` 记一条，避免用户消息悬空。"""
    return await append_message(
        cid,
        "assistant",
        text or None,
        status=status,
        meta=meta,
        session=session,
    )


async def load_messages(
    cid: int, *, session: AsyncSession | None = None
) -> list[dict]:
    """按写入顺序取会话消息，每条带 `db-{id}` 锚点。

    形状给窗口构建与回放用；`db_id` 单独留一份，避免调用方反复解析字符串。
    """
    rows = await list_messages(cid, session=session)
    return [
        {
            "id": _anchor(row.id),
            "db_id": row.id,
            "role": row.role,
            "content": row.content or "",
            "meta": _parse_meta(row.meta),
        }
        for row in rows
    ]


def _live(messages: list[dict], upto_msg_id: int) -> list[dict]:
    return [m for m in messages if m["db_id"] > upto_msg_id]


def _dialog_text(messages: list[dict]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)


async def maybe_summarize(
    cid: int,
    messages: list[dict],
    *,
    summary: str | None,
    summary_upto_msg_id: int,
    budget: int = HISTORY_BUDGET,
    session: AsyncSession | None = None,
) -> tuple[str | None, int]:
    """超预算时把最老的一半压成摘要，返回 `(summary, 新边界)`。

    - 至少保留 `MIN_KEEP_MESSAGES` 条原文，避免把当前上下文压没；
    - 摘要失败/超时：保留旧摘要，交给 `_take_recent` 截断；
    - 边界只向前移（`set_summary` 兜底）。
    """
    live = _live(messages, summary_upto_msg_id)
    total = estimate_tokens(summary or "") + sum(
        estimate_tokens(m["content"]) for m in live
    )
    if total <= budget or len(live) <= MIN_KEEP_MESSAGES:
        return summary, summary_upto_msg_id

    cut = min(len(live) // 2, len(live) - MIN_KEEP_MESSAGES)
    if cut <= 0:
        return summary, summary_upto_msg_id

    chunk = live[:cut]
    boundary = chunk[-1]["db_id"]
    prompt = (
        f"已有摘要：{summary or '（无）'}\n\n"
        f"新增对话：\n{_dialog_text(chunk)}"
    )
    try:
        new_summary = await asyncio.wait_for(
            chat(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM + PROMPT_GUARD},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            ),
            timeout=SUMMARY_TIMEOUT,
        )
    except Exception:
        logger.warning("摘要失败，保留旧摘要并截断窗口", exc_info=True)
        return summary, summary_upto_msg_id

    new_summary = (new_summary or "").strip()
    if not new_summary:
        return summary, summary_upto_msg_id

    try:
        moved = await set_summary(cid, new_summary, boundary, session=session)
    except Exception:
        # 摘要写入失败也只影响上下文质量，不能阻断本轮
        logger.warning("摘要写库失败，保留旧摘要", exc_info=True)
        return summary, summary_upto_msg_id
    if not moved:
        return summary, summary_upto_msg_id
    return new_summary, boundary


def _model_message(message: dict) -> dict:
    """窗口消息形状：只给模型 id/role/content，工具结果超长压成一行。"""
    content = message["content"]
    if message["role"] == "tool" and len(content) > MAX_TOOL_CHARS:
        content = content[:MAX_TOOL_CHARS] + "…"
    return {"id": message["id"], "role": message["role"], "content": content}


def _take_recent(live: list[dict], budget: int) -> tuple[list[dict], bool]:
    """从最新往前取到预算；至少保留最近 2 轮。"""
    if not live:
        return [], False

    keep_min = min(MIN_KEEP_MESSAGES, len(live))
    selected: list[dict] = []
    used = 0
    for message in reversed(live):
        cost = estimate_tokens(message["content"])
        if selected and len(selected) >= keep_min and used + cost > budget:
            break
        selected.append(message)
        used += cost

    selected.reverse()
    return selected, len(selected) < len(live)


async def load_window(
    cid: int,
    *,
    summary: str | None = None,
    summary_upto_msg_id: int = 0,
    budget: int = HISTORY_BUDGET,
    session: AsyncSession | None = None,
) -> Window:
    """构建送模型的历史窗口。

    **不含本轮用户消息**：调用方应在落库本轮消息之前调用；
    本轮输入由 `raw_query` 单独传给图。
    """
    messages = await load_messages(cid, session=session)
    summary, summary_upto_msg_id = await maybe_summarize(
        cid,
        messages,
        summary=summary,
        summary_upto_msg_id=summary_upto_msg_id,
        budget=budget,
        session=session,
    )

    live = _live(messages, summary_upto_msg_id)
    # 摘要占的是同一个历史预算，不能只看 live 消息
    live_budget = max(1, budget - estimate_tokens(summary or ""))
    chosen, truncated = _take_recent(live, live_budget)
    model_messages = [_model_message(message) for message in chosen]
    token_estimate = estimate_tokens(summary or "") + sum(
        estimate_tokens(message["content"]) for message in model_messages
    )
    return Window(
        messages=model_messages,
        summary=summary,
        token_estimate=token_estimate,
        truncated=truncated,
    )
