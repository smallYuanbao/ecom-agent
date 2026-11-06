"""消息持久化与 `db-{id}` 锚点（Ch05 M1）。

用临时库的 `session` 夹具，不碰 `data/app.db`。
"""
from app.core.history import (
    MAX_TOOL_CHARS,
    estimate_tokens,
    load_messages,
    load_window,
    save_assistant_message,
    save_user_message,
)
from app.db.models import Message
from app.db.repo_conversation import create_conversation, get_conversation, set_summary


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


# ── 窗口与摘要（M2）────────────────────────────────────────────


async def test_estimate_tokens_uses_char_count():
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("") == 0


async def test_window_keeps_latest_and_marks_truncated(session, monkeypatch):
    cid = await create_conversation("u1", session=session)
    for i in range(6):
        await save_user_message(cid, f"第{i}条消息" + "x" * 40, session=session)

    async def no_summary(*args, **kwargs):
        return None, 0

    # 这个用例只测裁窗，不让摘要参与
    monkeypatch.setattr("app.core.history.maybe_summarize", no_summary)

    window = await load_window(cid, budget=120, session=session)

    assert window.truncated is True
    assert len(window.messages) == 4          # 至少保留最近 2 轮
    assert window.messages[-1]["content"].startswith("第5条")


async def test_window_compresses_tool_message(session):
    cid = await create_conversation("u1", session=session)
    session.add(
        Message(conversation_id=cid, role="tool", content="x" * (MAX_TOOL_CHARS + 100))
    )
    await session.flush()

    window = await load_window(cid, budget=1000, session=session)

    assert window.messages[0]["role"] == "tool"
    assert len(window.messages[0]["content"]) == MAX_TOOL_CHARS + 1
    assert window.messages[0]["content"].endswith("…")


async def test_summary_moves_boundary_and_replaces_old_messages(session, monkeypatch):
    cid = await create_conversation("u1", session=session)
    for i in range(8):
        await save_user_message(cid, f"第{i}条" + "x" * 57, session=session)

    async def fake_chat(messages, *, temperature=0.2):
        return "用户要退款，订单 1001。"

    monkeypatch.setattr("app.core.history.chat", fake_chat)

    window = await load_window(cid, budget=150, session=session)

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert window.summary == "用户要退款，订单 1001。"
    assert conv.summary == window.summary
    assert conv.summary_upto_msg_id is not None
    first_db_id = int(window.messages[0]["id"].removeprefix("db-"))
    assert first_db_id > conv.summary_upto_msg_id


async def test_summary_failure_keeps_old_and_truncates(session, monkeypatch):
    cid = await create_conversation("u1", session=session)
    for i in range(8):
        await save_user_message(cid, f"第{i}条" + "x" * 57, session=session)

    async def boom(messages, *, temperature=0.2):
        raise TimeoutError("summary timeout")

    monkeypatch.setattr("app.core.history.chat", boom)

    window = await load_window(cid, budget=150, session=session)

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert window.summary is None
    assert conv.summary is None
    assert window.truncated is True


async def test_summary_boundary_never_moves_backward(session):
    cid = await create_conversation("u1", session=session)

    assert await set_summary(cid, "新摘要", 5, session=session) is True
    assert await set_summary(cid, "旧摘要", 3, session=session) is False

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.summary == "新摘要"
    assert conv.summary_upto_msg_id == 5
