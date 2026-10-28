"""消息仓库。

幂等（K4）：客户端重试同一条消息时会复用 `client_message_id`。
这里的选择是**返回已有消息的 id，而不是抛唯一约束**——
对调用方来说"重试"不是错误，重放一遍不该变成 500。
"""
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.db.session import session_scope


async def append_message(
    cid: int,
    role: str,
    content: str | None,
    *,
    status: str | None = None,
    client_message_id: str | None = None,
    meta: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
) -> int:
    """追加一条消息，返回消息 id。带幂等键时重复调用返回首次写入的 id。"""
    async with session_scope(session) as s:
        if client_message_id is not None:
            existing = await _find_by_client_id(s, cid, client_message_id)
            if existing is not None:
                return existing

        msg = Message(
            conversation_id=cid,
            role=role,
            content=content,
            status=status,
            client_message_id=client_message_id,
            meta=None if meta is None else json.dumps(meta, ensure_ascii=False),
        )
        try:
            # 用 SAVEPOINT 包住插入：撞唯一索引时只回滚这一步，
            # 不连累调用方的事务（K3 跨表事务可能还写了别的表）
            async with s.begin_nested():
                s.add(msg)
                await s.flush()
        except IntegrityError:
            # 并发重试：另一条请求刚好先写进去了，按幂等键取回它的 id
            if client_message_id is None:
                raise
            existing = await _find_by_client_id(s, cid, client_message_id)
            if existing is None:
                raise
            return existing
        return msg.id


async def list_messages(
    cid: int, *, session: AsyncSession | None = None
) -> list[Message]:
    """按写入顺序取整个会话的消息（窗口构建在 Ch05 做，这里给全量）。"""
    async with session_scope(session) as s:
        stmt = (
            select(Message)
            .where(Message.conversation_id == cid)
            .order_by(Message.id.asc())
        )
        return list((await s.scalars(stmt)).all())


async def _find_by_client_id(
    s: AsyncSession, cid: int, client_message_id: str
) -> int | None:
    stmt = select(Message.id).where(
        Message.conversation_id == cid,
        Message.client_message_id == client_message_id,
    )
    return (await s.execute(stmt)).scalar_one_or_none()
