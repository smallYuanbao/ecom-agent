"""E 出口：gate 分流、ReAct 成功/失败、脚本兜底、主图接线。"""
import pytest

from app.core.react import ReactResult
from app.graph import fallback_agent, nodes
from app.graph.build import build_graph
from app.graph.fallback_agent import build_fallback_graph, needs_tools
from app.graph.nodes import SOCIAL_REPLY, UNKNOWN_REPLY


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("你好", False),
        ("谢谢", False),
        ("这单能退多少", False),
        ("订单1001 到哪了", True),
        ("ST-20 还有货吗", True),
        ("多少钱", True),
        ("物流到哪了", True),
    ],
)
def test_needs_tools(text: str, expected: bool):
    assert needs_tools(text) is expected


def test_subgraph_has_three_nodes():
    graph = build_fallback_graph()
    assert {"gate", "agent", "scripted"} <= set(graph.get_graph().nodes)


async def test_gate_false_does_not_call_react(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("闲聊不应进 ReAct")

    monkeypatch.setattr(fallback_agent.react, "run", boom)

    result = await build_fallback_graph().ainvoke({"raw_query": "你好"})

    assert result["answer"] == SOCIAL_REPLY


async def test_agent_success_uses_react_answer(monkeypatch):
    async def fake_run(query, *, history, user_id, trace_id=None):
        return ReactResult(
            answer="订单 1001 已签收。",
            steps=2,
            tool_calls=[{"name": "query_order", "code": "ok"}],
            citations=[],
            code="ok",
        )

    monkeypatch.setattr(fallback_agent.react, "run", fake_run)

    result = await build_fallback_graph().ainvoke(
        {"raw_query": "订单1001 到哪了", "user_id": "u1"}
    )

    assert result["answer"] == "订单 1001 已签收。"
    assert result["citation_ids"] == []


async def test_agent_failure_falls_back_to_scripted(monkeypatch):
    async def fake_run(query, *, history, user_id, trace_id=None):
        return ReactResult("", 1, [], [], "error")

    monkeypatch.setattr(fallback_agent.react, "run", fake_run)

    result = await build_fallback_graph().ainvoke(
        {"raw_query": "订单1001 到哪了", "user_id": "u1"}
    )

    assert result["answer"] == UNKNOWN_REPLY
    assert result["actions"] == [{"type": "transfer_human"}]


async def test_main_graph_routes_fallback_to_react(monkeypatch):
    async def fake_classify(query: str):
        return "其他", 0.1, False

    async def fake_run(query, *, history, user_id, trace_id=None):
        return ReactResult("订单 1001 已签收。", 2, [], [], "ok")

    monkeypatch.setattr(nodes, "classify", fake_classify)
    monkeypatch.setattr(fallback_agent.react, "run", fake_run)

    result = await build_graph().ainvoke(
        {"raw_query": "订单1001 到哪了", "user_id": "u1"}
    )

    assert result["route"] == "fallback"
    assert result["answer"] == "订单 1001 已签收。"
