"""消息持久化与 `db-{id}` 锚点（Ch05 M1）。

用临时库的 `session` 夹具，不碰 `data/app.db`。
"""
from app.core.history import (
    load_messages,
    save_assistant_message,
    save_user_message,
)
from app.db.models import Message
from app.db.repo_conversation import create_conversation


async def test_save_and_load_with_anchors(session):
    cid = await create_conversation("u1", session=session)

    user_id = await save_user_message(cid, "我要退款", session=session)
    assistant_id = await save_assistant_message(
        cid,
        "请提供一下订单号",
        meta={"route": "rules", "intent": "退款申请"},
        session=session,
    )

    rows = await load_messages(cid, session=session)

    assert [row["id"] for row in rows] == [f"db-{user_id}", f"db-{assistant_id}"]
    assert [row["db_id"] for row in rows] == [user_id, assistant_id]
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "我要退款"
    assert rows[1]["meta"] == {"route": "rules", "intent": "退款申请"}


async def test_duplicate_client_message_id_returns_same_id(session):
    cid = await create_conversation("u1", session=session)

    first = await save_user_message(
        cid, "你好", client_message_id="cm-1", session=session
    )
    second = await save_user_message(
        cid, "你好", client_message_id="cm-1", session=session
    )

    assert first == second
    rows = await load_messages(cid, session=session)
    assert len(rows) == 1


async def test_selected_action_without_text_is_persisted(session):
    cid = await create_conversation("u1", session=session)

    await save_user_message(
        cid,
        None,
        meta={"selected_action": {"type": "select_order", "order_id": "1001"}},
        session=session,
    )

    rows = await load_messages(cid, session=session)
    assert rows[0]["content"] == ""
    assert rows[0]["meta"]["selected_action"]["order_id"] == "1001"


async def test_error_status_is_kept(session):
    cid = await create_conversation("u1", session=session)

    await save_assistant_message(
        cid, "服务暂时不可用", meta={"status": "error"}, status="error", session=session
    )

    rows = await load_messages(cid, session=session)
    assert rows[0]["role"] == "assistant"


async def test_bad_meta_does_not_break_loading(session):
    cid = await create_conversation("u1", session=session)
    session.add(
        Message(conversation_id=cid, role="assistant", content="x", meta="{bad json")
    )
    await session.flush()

    rows = await load_messages(cid, session=session)
    assert rows[0]["meta"] is None
