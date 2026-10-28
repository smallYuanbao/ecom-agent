"""会话/消息仓库：幂等与事务复用。"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.errors import NotFound
from app.db.models import Conversation, Message
from app.db.repo_conversation import create_conversation, get_conversation, set_flow
from app.db.repo_message import append_message, list_messages


async def test_create_and_get_conversation(session):
    cid = await create_conversation("u1", session=session)
    assert cid is not None

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.user_id == "u1"


async def test_other_users_conversation_raises_not_found(session):
    """S2：会话归属也在数据层校验，不靠调用方自觉。"""
    cid = await create_conversation("u1", session=session)
    with pytest.raises(NotFound):
        await get_conversation(cid, user_id="u2", session=session)


async def test_set_flow_round_trip(session):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"reason": "尺寸不合适"},
        session=session,
    )
    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "refund"
    assert conv.pending_question == "order_id"
    assert conv.draft == '{"reason": "尺寸不合适"}'


async def test_duplicate_client_message_id_returns_same_row(session):
    """K4：重试不该写出两条消息，也不该抛错——返回首次的 id。"""
    cid = await create_conversation("u1", session=session)

    first = await append_message(cid, "user", "退货怎么弄", client_message_id="cm-1", session=session)
    second = await append_message(cid, "user", "退货怎么弄", client_message_id="cm-1", session=session)

    assert first == second
    count = (
        await session.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == cid)
        )
    ).scalar()
    assert count == 1


async def test_messages_without_idempotency_key_accumulate(session):
    cid = await create_conversation("u1", session=session)
    await append_message(cid, "user", "第一句", session=session)
    await append_message(cid, "assistant", "第二句", session=session)

    msgs = await list_messages(cid, session=session)
    assert [m.role for m in msgs] == ["user", "assistant"]


async def test_shared_session_writes_are_atomic(engine, session):
    """K3：传同一个 session 写会话 + 消息，回滚后两条都不该留下。"""
    cid = await create_conversation("u1", session=session)
    await append_message(cid, "user", "要退款", session=session)

    await session.rollback()

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as fresh:
        assert (await fresh.execute(select(func.count()).select_from(Conversation))).scalar() == 0
        assert (await fresh.execute(select(func.count()).select_from(Message))).scalar() == 0
