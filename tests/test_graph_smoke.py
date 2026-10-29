"""主图冒烟测试：图能编译、能跑、五个出口都在且都是占位。

本章五出口全占位，所以只验证"骨架立住了"，不验证业务正确性。
真实分类与出口逻辑由 Task 2 / Task 7 等各自的测试覆盖。
"""

from app.graph.build import EXIT_NODES, build_graph


def test_graph_compiles():
    graph = build_graph()
    assert graph is not None


async def test_smoke_turn_reaches_an_exit():
    """一句话走完图，必须落在某个出口，且带占位标记。"""
    graph = build_graph()
    result = await graph.ainvoke({"raw_query": "退货政策是什么"})

    assert result["resolved_query"] == "退货政策是什么"      # 本章透传
    assert result["route"] in EXIT_NODES                    # 落在五个出口之一
    assert result["is_placeholder"] is True                 # 五个出口都还是占位
    assert result["answer"]                                # 占位也必须给话术，不能空


async def test_all_five_exit_nodes_registered():
    graph = build_graph()
    nodes = set(graph.get_graph().nodes)
    assert set(EXIT_NODES) <= nodes


async def test_placeholder_answers_contain_no_amount():
    """占位响应不得出现金额——避免用户把"开发中"当成真实报价。"""
    graph = build_graph()
    for query in ("我这单能退多少", "优惠券能抵多少钱", "退款要退多少"):
        result = await graph.ainvoke({"raw_query": query})
        assert "元" not in result["answer"], f"占位文案出现金额：{result['answer']}"
