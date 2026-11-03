"""B 出口子图骨架：5 节点 / 8 转移 / 3 个守卫。

Task 3 的验收不是"能查数据"——节点全是空实现。
它验收的是**形状**：节点齐、边对、守卫只返回映射里有的键、空状态能终止。
"""
import pytest

from app.graph import tracing
from app.graph.data_exit import (
    NEED_INFO_NEXT,
    NODE_NAMES,
    QUERY_NEXT,
    VALIDATE_NEXT,
    after_need_info,
    after_query,
    after_validate,
    build_data_graph,
)

# spec 的 8 条业务转移 + 入口边。END 在 langgraph 里是 "__end__"
EXPECTED_EDGES = {
    ("__start__", "extract_params"),
    ("extract_params", "validate"),
    ("validate", "query"),
    ("validate", "need_info"),
    ("query", "format"),
    ("query", "need_info"),
    ("query", "__end__"),
    ("need_info", "__end__"),
    ("format", "__end__"),
}


def graph():
    return build_data_graph().get_graph()


# ── 形状 ────────────────────────────────────────────────────


def test_compiles():
    assert build_data_graph() is not None


def test_has_five_nodes():
    names = {n for n in graph().nodes if not n.startswith("__")}
    assert names == set(NODE_NAMES)
    assert len(NODE_NAMES) == 5


def test_edge_table_matches_spec():
    actual = {(edge.source, edge.target) for edge in graph().edges}
    assert actual == EXPECTED_EDGES


def test_eight_business_transitions():
    conditional = len(VALIDATE_NEXT) + len(QUERY_NEXT) + len(NEED_INFO_NEXT)
    unconditional = 2  # extract→validate、format→END
    assert conditional + unconditional == 8  # 入口边不计入业务转移


def test_all_nodes_reachable_from_entry():
    edges = {(edge.source, edge.target) for edge in graph().edges}
    reached = {"extract_params"}
    frontier = ["extract_params"]
    while frontier:
        node = frontier.pop()
        for source, target in edges:
            if source == node and target != "__end__" and target not in reached:
                reached.add(target)
                frontier.append(target)
    assert reached == set(NODE_NAMES)


# ── 守卫 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("guard", "mapping", "state"),
    [
        (after_validate, VALIDATE_NEXT, {}),
        (after_validate, VALIDATE_NEXT, {"pending_question": "order_id"}),
        (after_query, QUERY_NEXT, {"tool_code": "ok"}),
        (after_query, QUERY_NEXT, {"tool_code": "not_found"}),
        (after_query, QUERY_NEXT, {"tool_code": "invalid_arg"}),
        (after_query, QUERY_NEXT, {"tool_code": "timeout"}),
        (after_query, QUERY_NEXT, {}),
        (after_need_info, NEED_INFO_NEXT, {}),
    ],
)
def test_guard_returns_mapped_key(guard, mapping, state):
    assert guard(state) in mapping


def test_validate_routes_by_pending_question():
    assert after_validate({}) == "query"
    assert after_validate({"pending_question": "order_id"}) == "need_info"


def test_query_routes_by_tool_code():
    assert after_query({"tool_code": "ok"}) == "format"
    assert after_query({"tool_code": "not_found"}) == "need_info"
    assert after_query({"tool_code": "invalid_arg"}) == "need_info"
    assert after_query({"tool_code": "timeout"}) == "human"
    assert after_query({"tool_code": "internal"}) == "human"
    assert after_query({}) == "human"          # 没有结果码 = fail-closed


# ── 骨架能终止 ──────────────────────────────────────────────


async def test_empty_walk_terminates():
    """空状态：extract → validate → query → 没有 tool_code → 转人工 → END。

    骨架阶段只要求"能终止"，不要求走对分支。
    """
    # 全部 stub 不写任何字段，最终状态可能是 None 或空 dict；
    # 这条只验证"能走完、不抛异常、不死循环"。
    result = await build_data_graph().ainvoke({})
    assert result is None or result == {}


async def test_each_node_writes_a_data_span(monkeypatch):
    spans: list[dict] = []

    async def _span(**kwargs):
        spans.append(kwargs)

    monkeypatch.setattr(tracing, "record_span", _span)

    await build_data_graph().ainvoke({"trace_id": "t1"})

    names = [s["name"] for s in spans]
    assert "data:extract_params" in names
    assert "data:validate" in names
    assert "data:query" in names
    assert all(s["trace_id"] == "t1" for s in spans)
