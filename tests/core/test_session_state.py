"""跨轮流程状态（M4/M7）：读写、draft 形状、30 分钟失效、批量清理。"""
import json
from datetime import timedelta

import pytest

from app.core.session_state import (
    FlowState,
    clear_flow,
    decide_flow,
    draft_from_state,
    is_expired,
    load_flow,
    save_flow_from_state,
)
from app.db.models import utcnow
from app.db.repo_conversation import create_conversation, get_conversation, set_flow
from scripts.cleanup import cleanup_expired


async def test_load_flow_returns_saved_state(session):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )

    flow = await load_flow(cid, user_id="u1", session=session)

    assert flow.active_flow == "refund"
    assert flow.pending_question == "order_id"
    assert flow.draft == {"intent": "退款申请"}
    assert flow.expired is False


async def test_load_flow_reuses_given_conversation(session, monkeypatch):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )
    conv = await get_conversation(cid, user_id="u1", session=session)

    async def boom(*args, **kwargs):
        raise AssertionError("传了 conversation 就不该再查一次")

    monkeypatch.setattr("app.core.session_state.get_conversation", boom)

    flow = await load_flow(cid, conversation=conv, session=session)

    assert flow.active_flow == "refund"


async def test_load_flow_requires_conversation_or_user(session):
    with pytest.raises(ValueError):
        await load_flow(1, session=session)


async def test_expired_flow_is_cleared(session):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )
    conv = await get_conversation(cid, user_id="u1", session=session)
    conv.updated_at = utcnow() - timedelta(minutes=31)
    await session.flush()

    flow = await load_flow(cid, user_id="u1", session=session)

    assert flow.expired is True
    assert flow.active_flow is None
    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow is None
    assert conv.pending_question is None
    assert conv.draft is None


async def test_save_rules_flow_uses_nested_draft(session):
    cid = await create_conversation("u1", session=session)
    state = {
        "route": "rules",
        "intent": "退款申请",
        "pending_question": "order_id",
        "order_id": "1001",
        "validate_retry": 2,
        "refund_draft": {"reason_code": "no_reason", "condition_ok": None},
    }

    await save_flow_from_state(cid, state, session=session)

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "refund"
    assert conv.pending_question == "order_id"
    assert json.loads(conv.draft) == {
        "intent": "退款申请",
        "order_id": "1001",
        "validate_retry": 2,
        "refund": {"reason_code": "no_reason", "condition_ok": None},
    }


async def test_save_data_flow(session):
    cid = await create_conversation("u1", session=session)
    state = {
        "route": "data",
        "intent": "订单查询",
        "pending_question": "order_id",
        "order_id": "1001",
    }

    await save_flow_from_state(cid, state, session=session)

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "data"
    assert json.loads(conv.draft) == {"intent": "订单查询", "order_id": "1001"}


async def test_completed_or_knowledge_clears_flow(session):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )

    await save_flow_from_state(
        cid, {"route": "knowledge", "intent": "政策问答"}, session=session
    )

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow is None
    assert conv.pending_question is None
    assert conv.draft is None


async def test_clear_flow(session):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )

    await clear_flow(cid, session=session)

    flow = await load_flow(cid, user_id="u1", session=session)
    assert flow.active_flow is None


def test_draft_from_state_omits_missing_fields():
    assert draft_from_state({"route": "data", "intent": "订单查询"}) == {
        "intent": "订单查询"
    }


def test_is_expired_boundary():
    now = utcnow()
    assert is_expired(now - timedelta(minutes=29)) is False
    assert is_expired(now - timedelta(minutes=31)) is True


async def test_cleanup_only_clears_expired(session):
    old = await create_conversation("u1", session=session)
    fresh = await create_conversation("u1", session=session)
    for cid in (old, fresh):
        await set_flow(
            cid,
            active_flow="refund",
            pending_question="order_id",
            draft={"intent": "退款申请"},
            session=session,
        )

    conv = await get_conversation(old, user_id="u1", session=session)
    conv.updated_at = utcnow() - timedelta(minutes=31)
    await session.flush()

    assert await cleanup_expired(session=session) == 1

    old_conv = await get_conversation(old, user_id="u1", session=session)
    fresh_conv = await get_conversation(fresh, user_id="u1", session=session)
    assert old_conv.active_flow is None
    assert fresh_conv.active_flow == "refund"


PARKED = FlowState(
    active_flow="refund",
    pending_question="order_id",
    draft={"intent": "退款申请", "refund": {"reason_code": "no_reason"}},
    expired=False,
)


def test_decide_flow_keeps_parked_flow_on_new_topic():
    decision = decide_flow({"route": "knowledge"}, parked=PARKED)

    assert decision.action == "keep"
    assert decision.active_flow == "refund"
    assert decision.pending_question == "order_id"
    assert decision.parked_flow == "refund"


def test_decide_flow_clears_on_human():
    decision = decide_flow({"route": "human"}, parked=PARKED)
    assert decision.action == "clear"


def test_decide_flow_clears_when_resumed_completes():
    decision = decide_flow({"route": "rules"}, parked=PARKED, resumed=True)
    assert decision.action == "clear"


def test_decide_flow_saves_new_and_marks_replaced():
    decision = decide_flow(
        {
            "route": "data",
            "intent": "订单查询",
            "pending_question": "order_id",
            "order_id": "1001",
        },
        parked=PARKED,
    )

    assert decision.action == "save"
    assert decision.active_flow == "data"
    assert decision.replaced_flow == "refund"


def test_decide_flow_saves_without_parked():
    decision = decide_flow(
        {"route": "rules", "intent": "退款申请", "pending_question": "order_id"}
    )

    assert decision.action == "save"
    assert decision.replaced_flow is None
