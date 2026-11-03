"""路由表与出口接线的单元测试。

这一层**不打图、不调模型**：`route_by_intent` 是纯函数，直接调用即可。
图层面的接线由 `test_graph_smoke.py` 覆盖。

Ch04 之后五个出口全部是真实实现，占位机制已删除——这个文件只守路由本身。
"""
import pytest

from app.graph.build import EXIT_NODES
from app.graph.routing import FALLBACK_ROUTE, INTENT_TO_ROUTE, route_by_intent


def test_route_table_covers_exactly_the_exit_nodes():
    """路由表的所有取值 = 图里注册的五个出口。多一个少一个都是 bug。"""
    assert set(INTENT_TO_ROUTE.values()) == set(EXIT_NODES)


def test_twelve_intents_are_covered():
    """12 类意图一个不能少——少一个就会被路由兜底到 fallback，静默降级。"""
    assert len(INTENT_TO_ROUTE) == 12


def test_every_exit_has_a_node():
    """五个出口都得在图里（子图也是节点），否则条件边找不到目标。"""
    from app.graph import build

    graph_nodes = set(build.build_graph().get_graph().nodes)
    assert set(EXIT_NODES) <= graph_nodes


@pytest.mark.parametrize(("intent", "expected_route"), sorted(INTENT_TO_ROUTE.items()))
def test_intent_routes_to_its_exit(intent: str, expected_route: str):
    assert route_by_intent({"intent": intent}) == expected_route


@pytest.mark.parametrize(
    "state",
    [
        {},                      # 完全空
        {"intent": ""},          # 空字符串
        {"intent": "退款"},       # 模型返回了清单外的词
        {"intent": "退款退货"},   # 别的项目的意图名
        {"intent": None},        # 模型返回 null
    ],
    ids=["empty-state", "empty-intent", "out-of-vocab", "other-project", "null"],
)
def test_unknown_intent_falls_back_instead_of_raising(state):
    """越界一律兜底，不抛 KeyError——这是参考实现踩过的坑，也是我们的选择。"""
    assert route_by_intent(state) == FALLBACK_ROUTE
