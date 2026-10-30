"""`/api/chat` 的帧顺序与错误映射。

两层：

1. `run_turn`（业务层）——注入假图，验证帧顺序与内容
2. HTTP 层——验证 SSE 编码、错误帧、以及异常不外泄

**都离线**：假图不调模型，`record_span` 被替换成记录器。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.api.chat import ChatError, run_turn

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


@pytest.fixture
def spans(monkeypatch):
    """把 trace 写入换成内存记录，避免测试碰真库。"""
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(chat_module, "record_span", fake_record)
    return recorded


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


async def _frames(chunks: list[dict]) -> list[dict]:
    return [f async for f in run_turn("退货运费谁出", None, "t-1", graph=FakeGraph(chunks))]


# ── 帧顺序 ──────────────────────────────────────────────────


async def test_frame_order(spans):
    frames = await _frames(STRONG_CHUNKS)
    events = [f.get("event") or "delta" for f in frames]
    assert events == ["ack", "route", "delta", "citations", "done"]


async def test_route_frame_carries_intent_and_route(spans):
    frames = await _frames(STRONG_CHUNKS)
    route = next(f for f in frames if f.get("event") == "route")
    assert route["route"] == "knowledge"
    assert route["intent"] == "政策问答"


async def test_route_frame_comes_before_delta(spans):
    """route 在分类完成时就发，前端可以先显示"正在查知识库"。"""
    frames = await _frames(STRONG_CHUNKS)
    kinds = [f.get("event") or "delta" for f in frames]
    assert kinds.index("route") < kinds.index("delta")


async def test_actions_frame_only_when_present(spans):
    chunks = [STRONG_CHUNKS[1], {"human": {"answer": "已转接", "actions": [{"type": "transfer_human"}]}}]
    frames = await _frames(chunks)
    actions = next(f for f in frames if f.get("event") == "actions")
    assert actions["items"] == [{"type": "transfer_human"}]

    # 没有动作时不该发空帧
    frames_without = await _frames(STRONG_CHUNKS)
    assert not any(f.get("event") == "actions" for f in frames_without)


async def test_no_citations_frame_when_empty(spans):
    chunks = [STRONG_CHUNKS[1], {"fallback": {"answer": "没理解", "citation_ids": []}}]
    frames = await _frames(chunks)
    assert not any(f.get("event") == "citations" for f in frames)


# ── trace ──────────────────────────────────────────────────


async def test_one_span_per_node_plus_total(spans):
    await _frames(STRONG_CHUNKS)
    names = [s["name"] for s in spans]
    assert names == ["node:resolve_reference", "node:classify_intent", "node:knowledge", "turn:total"]


async def test_spans_share_trace_id(spans):
    await _frames(STRONG_CHUNKS)
    assert {s["trace_id"] for s in spans} == {"t-1"}


async def test_span_has_duration_and_keys(spans):
    await _frames(STRONG_CHUNKS)
    node_span = next(s for s in spans if s["name"] == "node:classify_intent")
    assert "duration_ms" in node_span["attrs"]
    assert node_span["attrs"]["route"] == "knowledge"


# ── 入参校验 ────────────────────────────────────────────────


@pytest.mark.parametrize("message", ["", "   "])
async def test_empty_message_is_invalid_arg(spans, message: str):
    with pytest.raises(ChatError) as exc:
        [f async for f in run_turn(message, None, "t-1", graph=FakeGraph([]))]
    assert exc.value.code == "invalid_arg"


async def test_too_long_message_is_invalid_arg(spans):
    with pytest.raises(ChatError) as exc:
        [f async for f in run_turn("啊" * 501, None, "t-1", graph=FakeGraph([]))]
    assert exc.value.code == "invalid_arg"


async def test_state_passed_to_graph(spans):
    fake = FakeGraph(STRONG_CHUNKS)
    [f async for f in run_turn("你好", 42, "t-9", graph=fake)]
    assert fake.seen_state == {"raw_query": "你好", "conversation_id": 42, "trace_id": "t-9"}
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
