"""C 出口子图骨架：9 节点 / 17 转移 / 6 个守卫。

Task 4 的验收不是"退款工作正常"——节点全是空实现。
它验收的是**形状**：节点齐、边对、守卫只会返回映射里有的键。

形状为什么值得单独测：后面每个 Task 都会替换节点函数体，
但**节点名与边不能改**（改了要同步 spec 的转移表）。
这些断言就是那条"不许悄悄改形状"的线。
"""
import pytest

from app.graph.rules_exit import (
    COMPLIANCE_NEXT,
    INTAKE_NEXT,
    JUDGE_NEXT,
    NEED_INFO_NEXT,
    NODE_NAMES,
    QUERY_DATA_NEXT,
    VALIDATE_NEXT,
    after_compliance,
    after_intake,
    after_judge,
    after_need_info,
    after_query_data,
    after_validate,
    build_rules_graph,
)

# spec 的 17 条业务转移 + 入口边。END 在 langgraph 里是 "__end__"
EXPECTED_EDGES = {
    ("__start__", "intake"),
    ("intake", "extract"),
    ("intake", "need_info"),
    ("extract", "validate"),
    ("validate", "query_data"),
    ("validate", "need_info"),
    ("validate", "__end__"),
    ("need_info", "validate"),
    ("need_info", "__end__"),
    ("query_data", "judge"),
    ("query_data", "need_info"),
    ("query_data", "__end__"),
    ("judge", "compute"),
    ("judge", "__end__"),
    ("compute", "compliance"),
    ("compliance", "respond"),
    ("compliance", "__end__"),
    ("respond", "__end__"),
}


def graph():
    return build_rules_graph().get_graph()


# ── 形状 ────────────────────────────────────────────────────


def test_compiles():
    assert build_rules_graph() is not None


def test_has_nine_nodes():
    names = {n for n in graph().nodes if not n.startswith("__")}
    assert names == set(NODE_NAMES)
    assert len(NODE_NAMES) == 9


def test_edge_table_matches_spec():
    actual = {(edge.source, edge.target) for edge in graph().edges}
    assert actual == EXPECTED_EDGES


def test_all_nine_nodes_reachable_from_entry():
    """9 个状态都能从入口走到——不能有孤岛节点。

    只看边表不够：一个节点可能被注册了却没有任何入边。
    """
    edges = {(edge.source, edge.target) for edge in graph().edges}
    reached = {"intake"}
    frontier = ["intake"]
    while frontier:
        node = frontier.pop()
        for source, target in edges:
            if source == node and target != "__end__" and target not in reached:
                reached.add(target)
                frontier.append(target)
    assert reached == set(NODE_NAMES)


def test_seventeen_business_transitions():
    """spec 说 17 条转移，这里从映射表反推，防止有人加了边却忘了改文档。"""
    conditional = (
        len(INTAKE_NEXT)
        + len(NEED_INFO_NEXT)
        + len(VALIDATE_NEXT)
        + len(QUERY_DATA_NEXT)
        + len(JUDGE_NEXT)
        + len(COMPLIANCE_NEXT)
    )
    unconditional = 3  # extract→validate, compute→compliance, respond→END
    assert conditional + unconditional == 17


# ── 守卫：返回值必须落在映射里 ──────────────────────────────


@pytest.mark.parametrize(
    ("guard", "mapping", "state"),
    [
        (after_intake, INTAKE_NEXT, {}),
        (after_intake, INTAKE_NEXT, {"order_id": "1001"}),
        (after_need_info, NEED_INFO_NEXT, {}),
        (after_need_info, NEED_INFO_NEXT, {"pending_question": "请提供订单号"}),
        (after_validate, VALIDATE_NEXT, {}),
        (after_validate, VALIDATE_NEXT, {"validate_errors": ["缺原因"]}),
        (
            after_validate,
            VALIDATE_NEXT,
            {"validate_errors": ["缺原因"], "validate_retry": 2},
        ),
        (after_query_data, QUERY_DATA_NEXT, {}),
        (
            after_query_data,
            QUERY_DATA_NEXT,
            {"order_snapshot": {"id": "1001"}, "evidence": [{"id": "a"}]},
        ),
        (
            after_query_data,
            QUERY_DATA_NEXT,
            {"order_snapshot": {"id": "1001"}, "evidence": []},
        ),
        (after_judge, JUDGE_NEXT, {}),
        (after_judge, JUDGE_NEXT, {"refund_judgement": {"confidence": 0.9}}),
        (after_compliance, COMPLIANCE_NEXT, {}),
        (after_compliance, COMPLIANCE_NEXT, {"compliance_violation": "触红线"}),
    ],
)
def test_guard_returns_mapped_key(guard, mapping, state):
    assert guard(state) in mapping


# ── 守卫：分支语义 ──────────────────────────────────────────


def test_intake_without_order_id_asks_for_it():
    assert after_intake({}) == "need_info"
    assert after_intake({"order_id": "1001"}) == "extract"


def test_need_info_waits_then_returns_to_validate():
    assert after_need_info({"pending_question": "请提供订单号"}) == "end"
    assert after_need_info({"pending_question": None}) == "validate"
    assert after_need_info({}) == "validate"


def test_validate_passes_without_errors():
    assert after_validate({}) == "query_data"


def test_validate_retries_then_goes_human():
    assert after_validate({"validate_errors": ["缺原因"]}) == "need_info"
    assert (
        after_validate({"validate_errors": ["缺原因"], "validate_retry": 1})
        == "need_info"
    )
    assert (
        after_validate({"validate_errors": ["缺原因"], "validate_retry": 2}) == "human"
    )


def test_query_data_distinguishes_user_error_from_system_error():
    """查不到订单是用户的错（追问）；政策为空是系统的错（转人工）。"""
    assert after_query_data({}) == "need_info"
    assert (
        after_query_data({"order_snapshot": {"id": "1001"}, "evidence": []}) == "human"
    )
    assert (
        after_query_data(
            {"order_snapshot": {"id": "1001"}, "evidence": [{"id": "a"}]}
        )
        == "judge"
    )


def test_judge_low_confidence_goes_human():
    assert after_judge({"refund_judgement": {"confidence": 0.7}}) == "compute"
    assert after_judge({"refund_judgement": {"confidence": 0.69}}) == "human"
    assert after_judge({}) == "human"  # 没给结论 = 拿不准


def test_compliance_violation_skips_respond():
    assert after_compliance({"compliance_violation": "复算不一致"}) == "human"
    assert after_compliance({"compliance_violation": None}) == "respond"


# ── 骨架能终止 ──────────────────────────────────────────────


async def test_empty_walk_terminates_at_need_info():
    """空状态进来：抽不到订单号 → 追问 → 本轮结束。

    这条防的是 `need_info → validate → query_data → need_info` 死循环：
    `need_info` 必须写 `pending_question`，守卫才会把它接去 END 而不是 validate。
    """
    result = await build_rules_graph().ainvoke({})
    assert result["pending_question"] == "order_id"
