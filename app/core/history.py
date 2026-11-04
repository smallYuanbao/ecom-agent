"""消息持久化与 `db-{id}` 锚点（M1）。

**存全量**是为了回放与审计；**送窗口**是下一节（M2）的事。
这里只负责把一轮对话的两条消息落库，并在读出时带上与 DB 主键一致的锚点。

锚点约定：`id=f"db-{row.id}"`。
摘要边界、滑窗边界都用 DB id 表示，不用「第几条」——消息补录/删除时不会错位。
"""
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repo_message import append_message, list_messages

logger = logging.getLogger(__name__)


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
