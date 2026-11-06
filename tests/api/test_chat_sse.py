"""`/api/chat` 的帧顺序、消息落库与错误映射。

两层：

1. `run_turn`（业务层）——注入假图，验证帧顺序、会话/消息 id、落库
2. HTTP 层——验证 SSE 编码、错误帧、以及异常不外泄

**都离线**：假图不调模型，`record_span` 被替换成记录器；消息写临时库。
"""
import asyncio
import json
from functools import partial

import pytest
from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.api.chat import ChatError, run_turn
from app.core.history import load_messages
from app.core.lock import conversation_lock as real_lock
from app.db.repo_conversation import create_conversation, get_conversation, set_flow

# ── 假图 ────────────────────────────────────────────────────


class FakeGraph:
    """只做一件事：按给定顺序吐出 update chunk，模拟 LangGraph 的 astream。"""

    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.seen_state: dict | None = None
        self.seen_mode: str | None = None

    async def astream(self, state, stream_mode="updates"):
        self.seen_state = state
        self.seen_mode = stream_mode
        for chunk in self.chunks:
            yield chunk


class BoomGraph:
    async def astream(self, state, stream_mode="updates"):
        raise RuntimeError("boom")
        yield  # pragma: no cover


@pytest.fixture(autouse=True)
def spans(monkeypatch):
    """把 trace 写入换成内存记录，避免测试碰真库。

    autouse：凡是跑 `run_turn` 的测试都会经过 `record_span`，
    漏打桩就会写到 `data/app.db`。
    """
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(chat_module, "record_span", fake_record)
    return recorded


@pytest.fixture
def frames(session, spans):
    """跑一轮并返回帧；每次调用自动建一个会话，不依赖外部数据。"""

    async def _frames(
        chunks: list[dict],
        *,
        message: str = "退货运费谁出",
        conversation_id: int | None = None,
    ) -> list[dict]:
        return [
            f
            async for f in run_turn(
                message,
                conversation_id,
                "t-1",
                graph=FakeGraph(chunks),
                session=session,
            )
        ]

    return _frames


STRONG_CHUNKS = [
    {"resolve_reference": {"resolved_query": "退货运费谁出"}},
    {"classify_intent": {"intent": "政策问答", "route": "knowledge"}},
    {
        "knowledge": {
            "answer": "质量问题由商家承担[1]。",
            "citation_ids": ["returns#退货运费由谁承担"],
            "is_placeholder": False,
        }
    },
]


# ── 帧顺序 ──────────────────────────────────────────────────


async def test_frame_order(frames):
    out = await frames(STRONG_CHUNKS)
    events = [f.get("event") or "delta" for f in out]
    assert events == ["ack", "route", "delta", "citations", "done"]


async def test_route_frame_carries_intent_and_route(frames):
    out = await frames(STRONG_CHUNKS)
    route = next(f for f in out if f.get("event") == "route")
    assert route["route"] == "knowledge"
    assert route["intent"] == "政策问答"
    assert route["resumed"] is False


async def test_route_frame_comes_before_delta(frames):
    """route 在分类完成时就发，前端可以先显示"正在查知识库"。"""
    out = await frames(STRONG_CHUNKS)
    kinds = [f.get("event") or "delta" for f in out]
    assert kinds.index("route") < kinds.index("delta")


async def test_actions_frame_only_when_present(frames):
    chunks = [
        STRONG_CHUNKS[1],
        {"human": {"answer": "已转接", "actions": [{"type": "transfer_human"}]}},
    ]
    out = await frames(chunks)
    actions = next(f for f in out if f.get("event") == "actions")
    assert actions["items"] == [{"type": "transfer_human"}]

    # 没有动作时不该发空帧
    out_without = await frames(STRONG_CHUNKS)
    assert not any(f.get("event") == "actions" for f in out_without)


async def test_no_citations_frame_when_empty(frames):
    chunks = [STRONG_CHUNKS[1], {"fallback": {"answer": "没理解", "citation_ids": []}}]
    out = await frames(chunks)
    assert not any(f.get("event") == "citations" for f in out)


# ── 会话与消息落库 ──────────────────────────────────────────


async def test_ack_and_done_carry_ids(session, spans):
    cid = await create_conversation("u1", session=session)
    out = [
        f
        async for f in run_turn(
            "你好", cid, "t-1", graph=FakeGraph(STRONG_CHUNKS), session=session
        )
    ]

    ack = out[0]
    assert ack["event"] == "ack"
    assert ack["conversation_id"] == cid
    assert isinstance(ack["user_message_id"], int)

    done = out[-1]
    assert done["event"] == "done"
    assert isinstance(done["message_id"], int)


async def test_messages_are_persisted(session, spans):
    cid = await create_conversation("u1", session=session)
    _ = [
        f
        async for f in run_turn(
            "退货运费谁出",
            cid,
            "t-1",
            graph=FakeGraph(STRONG_CHUNKS),
            session=session,
        )
    ]

    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "退货运费谁出"
    assert rows[1]["meta"]["route"] == "knowledge"
    assert rows[1]["meta"]["citation_ids"] == ["returns#退货运费由谁承担"]


async def test_selected_action_allows_empty_message(session, spans):
    cid = await create_conversation("u1", session=session)
    _ = [
        f
        async for f in run_turn(
            "",
            cid,
            "t-1",
            selected_action={"type": "select_order", "order_id": "1001"},
            graph=FakeGraph(STRONG_CHUNKS),
            session=session,
        )
    ]

    rows = await load_messages(cid, session=session)
    assert rows[0]["content"] == ""
    assert rows[0]["meta"]["selected_action"]["order_id"] == "1001"


async def test_unknown_conversation_is_not_found(session, spans):
    with pytest.raises(ChatError) as exc:
        _ = [
            f
            async for f in run_turn(
                "你好", 9999, "t-1", graph=FakeGraph([]), session=session
            )
        ]
    assert exc.value.code == "not_found"


async def test_flow_state_persisted_parked_and_cleared(session, spans):
    cid = await create_conversation("u1", session=session)
    pending_chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        {
            "rules": {
                "intent": "退款申请",
                "pending_question": "order_id",
                "answer": "请提供订单号",
                "refund_draft": {"reason_code": "no_reason"},
            }
        },
    ]

    _ = [
        f
        async for f in run_turn(
            "我要退款", cid, "t-1", graph=FakeGraph(pending_chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "refund"
    assert conv.pending_question == "order_id"
    assert json.loads(conv.draft)["refund"] == {"reason_code": "no_reason"}

    # 下一轮完成别的话题 → park：保留旧流程 + 提示 + resume_flow 动作
    done_chunks = [
        {"classify_intent": {"intent": "政策问答", "route": "knowledge"}},
        {"knowledge": {"answer": "质量问题由商家承担[1]。"}},
    ]
    out = [
        f
        async for f in run_turn(
            "退货政策", cid, "t-1", graph=FakeGraph(done_chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow == "refund"
    assert conv.pending_question == "order_id"
    delta = next(f["delta"] for f in out if "delta" in f)
    assert "未完成的退款申请" in delta
    actions = next(f for f in out if f.get("event") == "actions")
    assert {"type": "resume_flow", "flow": "refund"} in actions["items"]

    # 转人工 → 清空，且不再提示 park
    human_chunks = [
        {"classify_intent": {"intent": "投诉", "route": "human"}},
        {"human": {"answer": "已为您转接人工。"}},
    ]
    out = [
        f
        async for f in run_turn(
            "我要投诉", cid, "t-1", graph=FakeGraph(human_chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow is None
    assert conv.pending_question is None
    assert not any("未完成的退款申请" in f.get("delta", "") for f in out)


async def test_capture_refund_draft_when_order_missing(session, spans, monkeypatch):
    """第一轮没有订单号时 C 出口不走 extract；轮末要把原因补进 draft。"""
    cid = await create_conversation("u1", session=session)
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        {
            "rules": {
                "intent": "退款申请",
                "pending_question": "order_id",
                "answer": "请提供订单号",
            }
        },
    ]

    async def fake_extract(text):
        return {"reason_code": "no_reason"}

    monkeypatch.setattr(chat_module, "extract_draft", fake_extract)

    _ = [
        f
        async for f in run_turn(
            "我要退款，尺寸不合适", cid, "t-1", graph=FakeGraph(chunks), session=session
        )
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert json.loads(conv.draft)["refund"] == {"reason_code": "no_reason"}


async def test_resume_refund_from_text(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={
            "intent": "退款申请",
            "refund": {"reason_code": "no_reason"},
            "validate_retry": 1,
        },
        session=session,
    )
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules", "resumed": True}},
        {
            "rules": {
                "refund_draft": {"reason_code": "no_reason"},
                "refund_amount": 124900,
                "answer": "可退 1249.00 元。",
            }
        },
    ]
    fake = FakeGraph(chunks)

    out = [
        f
        async for f in run_turn("1001", cid, "t-1", graph=fake, session=session)
    ]

    route_frame = next(f for f in out if f.get("event") == "route")
    assert route_frame["resumed"] is True
    assert fake.seen_state["resume_route"] == "rules"
    assert fake.seen_state["intent"] == "退款申请"
    assert fake.seen_state["order_id"] == "1001"
    assert fake.seen_state["refund_draft"] == {"reason_code": "no_reason"}
    assert fake.seen_state["validate_retry"] == 0      # 有进展，重试计数重置
    assert fake.seen_state["skip_extract"] is True


async def test_resume_with_extra_info_still_extracts(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请", "refund": {"reason_code": "no_reason"}},
        session=session,
    )
    fake = FakeGraph(
        [{"classify_intent": {"intent": "退款申请", "route": "rules"}}]
    )

    _ = [
        f
        async for f in run_turn(
            "1001，尺寸不合适", cid, "t-1", graph=fake, session=session
        )
    ]

    assert fake.seen_state["order_id"] == "1001"
    assert "skip_extract" not in fake.seen_state


async def test_resume_from_selected_action(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请", "refund": {"reason_code": "no_reason"}},
        session=session,
    )
    fake = FakeGraph(
        [{"classify_intent": {"intent": "退款申请", "route": "rules"}}]
    )

    _ = [
        f
        async for f in run_turn(
            "",
            cid,
            "t-1",
            selected_action={"type": "select_order", "order_id": "1001"},
            graph=fake,
            session=session,
        )
    ]

    assert fake.seen_state["resume_route"] == "rules"
    assert fake.seen_state["order_id"] == "1001"
    assert fake.seen_state["skip_extract"] is True


async def test_cancel_clears_flow(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="order_id",
        draft={"intent": "退款申请"},
        session=session,
    )
    fake = FakeGraph(
        [
            {"classify_intent": {"intent": "政策问答", "route": "knowledge"}},
            {"knowledge": {"answer": "质量问题由商家承担[1]。"}},
        ]
    )

    _ = [
        f
        async for f in run_turn("算了", cid, "t-1", graph=fake, session=session)
    ]

    conv = await get_conversation(cid, user_id="u1", session=session)
    assert conv.active_flow is None
    assert fake.seen_state["resume_route"] is None


async def test_resume_data_flow(session, spans):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="data",
        pending_question="order_id",
        draft={"intent": "订单查询"},
        session=session,
    )
    fake = FakeGraph(
        [{"classify_intent": {"intent": "订单查询", "route": "data"}}]
    )

    _ = [
        f
        async for f in run_turn("1001", cid, "t-1", graph=fake, session=session)
    ]

    assert fake.seen_state["resume_route"] == "data"
    assert fake.seen_state["order_id"] == "1001"
    assert "skip_extract" not in fake.seen_state


async def test_resume_refund_reason_skips_second_extract(
    session, spans, monkeypatch
):
    cid = await create_conversation("u1", session=session)
    await set_flow(
        cid,
        active_flow="refund",
        pending_question="reason_code",
        draft={"intent": "退款申请", "refund": {"item_count": 1}},
        session=session,
    )

    async def fake_extract(text):
        return {"reason_code": "quality"}

    monkeypatch.setattr("app.core.slot_fill.extract_draft", fake_extract)

    fake = FakeGraph(
        [{"classify_intent": {"intent": "退款申请", "route": "rules"}}]
    )
    _ = [
        f
        async for f in run_turn(
            "有质量问题", cid, "t-1", graph=fake, session=session
        )
    ]

    assert fake.seen_state["refund_draft"] == {
        "item_count": 1,
        "reason_code": "quality",
    }
    assert fake.seen_state["skip_extract"] is True


async def test_busy_conversation_raises_busy(session, spans, monkeypatch):
    cid = await create_conversation("u1", session=session)
    entered = asyncio.Event()

    async def hold():
        async with real_lock(cid):
            entered.set()
            await asyncio.sleep(0.2)

    task = asyncio.create_task(hold())
    await entered.wait()
    monkeypatch.setattr(
        chat_module,
        "conversation_lock",
        partial(real_lock, timeout=0.01),
    )

    with pytest.raises(ChatError) as exc:
        _ = [
            f
            async for f in run_turn(
                "你好", cid, "t-1", graph=FakeGraph([]), session=session
            )
        ]

    assert exc.value.code == "busy"
    await task


async def test_graph_error_persists_error_message(session, spans):
    cid = await create_conversation("u1", session=session)
    with pytest.raises(RuntimeError):
        _ = [
            f
            async for f in run_turn(
                "你好", cid, "t-1", graph=BoomGraph(), session=session
            )
        ]

    rows = await load_messages(cid, session=session)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[1]["meta"] == {"status": "error"}


# ── 输出检查 ────────────────────────────────────────────────


async def test_output_guard_rewrites_before_delta_and_persist(session, spans):
    """金额/措辞违规在 delta 之前整段替换，落库的也是替换后的回答。"""
    cid = await create_conversation("u1", session=session)
    chunks = [
        {"classify_intent": {"intent": "退款申请", "route": "rules"}},
        {"validate": {"order_snapshot": {"order_id": "1001", "amount": 124900}}},
        {"judge": {"refund_judgement": {"eligible": True, "reason_code": "size"}}},
        {"compute": {"refund_amount": 50000}},
        {
            "respond": {
                "answer": "可以全额退款，本次退 500.00 元，另补偿 200.00 元。",
                "is_placeholder": False,
            }
        },
    ]

    out = [
        f
        async for f in run_turn(
            "尺寸不合适", cid, "t-1", graph=FakeGraph(chunks), session=session
        )
    ]

    delta = next(f["delta"] for f in out if "delta" in f)
    assert delta == "本次应退金额：500.00 元，具体以平台售后规则为准。"
    assert "全额" not in delta
    assert "200.00" not in delta

    total = next(s for s in spans if s["name"] == "turn:total")
    assert set(total["attrs"]["output_violations"]) == {"amount", "full_refund"}

    rows = await load_messages(cid, session=session)
    assert rows[1]["content"] == delta


# ── trace ──────────────────────────────────────────────────


async def test_one_span_per_node_plus_total(spans, frames):
    await frames(STRONG_CHUNKS)
    names = [s["name"] for s in spans]
    assert names == [
        "node:resolve_reference",
        "node:classify_intent",
        "node:knowledge",
        "turn:total",
    ]


async def test_spans_share_trace_id(spans, frames):
    await frames(STRONG_CHUNKS)
    assert {s["trace_id"] for s in spans} == {"t-1"}


async def test_span_has_duration_and_keys(spans, frames):
    await frames(STRONG_CHUNKS)
    node_span = next(s for s in spans if s["name"] == "node:classify_intent")
    assert "duration_ms" in node_span["attrs"]
    assert node_span["attrs"]["route"] == "knowledge"


# ── 入参校验 ────────────────────────────────────────────────


@pytest.mark.parametrize("message", ["", "   "])
async def test_empty_message_is_invalid_arg(message: str):
    with pytest.raises(ChatError) as exc:
        _ = [f async for f in run_turn(message, None, "t-1", graph=FakeGraph([]))]
    assert exc.value.code == "invalid_arg"


async def test_too_long_message_is_invalid_arg():
    with pytest.raises(ChatError) as exc:
        _ = [
            f
            async for f in run_turn(
                "啊" * 501, None, "t-1", graph=FakeGraph([])
            )
        ]
    assert exc.value.code == "invalid_arg"


async def test_state_passed_to_graph(session, spans):
    cid = await create_conversation("u1", session=session)
    fake = FakeGraph(STRONG_CHUNKS)
    _ = [
        f
        async for f in run_turn(
            "你好", cid, "t-9", graph=fake, session=session
        )
    ]
    assert fake.seen_state == {
        "raw_query": "你好",
        "conversation_id": cid,
        "trace_id": "t-9",
        "user_id": "u1",       # 演示身份，Ch06 换成真实登录用户
        "history": [],         # 新会话没有历史
        "summary": None,
        "resume_route": None,
        "resumed": False,
    }
    assert fake.seen_mode == "updates"


# ── HTTP 层 ────────────────────────────────────────────────


def _sse_frames(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)          # 不用 with：跳过 lifespan，避免校验密钥


def test_http_streams_sse_frames(client, spans, monkeypatch):
    async def fake_run_turn(message, conversation_id, trace_id, **kwargs):
        for frame in [{"event": "ack"}, {"event": "done"}]:
            yield frame

    monkeypatch.setattr("app.main.run_turn", fake_run_turn)

    response = client.post("/api/chat", json={"message": "你好"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [f["event"] for f in _sse_frames(response.text)] == ["ack", "done"]


def test_http_other_conversation_returns_404(client, spans, monkeypatch):
    async def deny(*args, **kwargs):
        raise ChatError("not_found", "会话不存在")

    monkeypatch.setattr("app.main.conversation_access", deny)

    response = client.post(
        "/api/chat", json={"message": "你好", "conversation_id": 999}
    )

    assert response.status_code == 404


def test_http_error_frame_hides_internal_detail(client, spans, monkeypatch):
    """内部异常只回一句标准话术，不能把堆栈或原始错误信息透给用户。"""

    async def boom(message, conversation_id, trace_id, **kwargs):
        raise RuntimeError("数据库连接串 postgres://secret")
        yield  # pragma: no cover

    monkeypatch.setattr("app.main.run_turn", boom)

    frames = _sse_frames(client.post("/api/chat", json={"message": "你好"}).text)
    assert frames[0]["event"] == "error"
    assert frames[0]["code"] == "internal_error"
    assert "secret" not in frames[0]["message"]


def test_http_invalid_arg_keeps_readable_message(client, spans, monkeypatch):
    async def bad(message, conversation_id, trace_id, **kwargs):
        raise ChatError("invalid_arg", "消息不能为空")
        yield  # pragma: no cover

    monkeypatch.setattr("app.main.run_turn", bad)

    frames = _sse_frames(client.post("/api/chat", json={"message": "x"}).text)
    assert frames[0]["code"] == "invalid_arg"
    assert frames[0]["message"] == "消息不能为空"
