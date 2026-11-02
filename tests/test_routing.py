"""路由表与占位出口的单元测试。

这一层**不打图、不调模型**：`route_by_intent` 是纯函数，`*_exit` 是纯函数，
直接调用即可。图层面的接线由 `test_graph_smoke.py` 覆盖。

守三类不变量（都是"以后改一处忘了改另一处"的地方）：

1. 路由表的**取值**必须与 `EXIT_NODES` 一致——否则条件边找不到目标节点，运行时报错
2. `PLACEHOLDER_TEXT` 的**键**必须与出口名一致——否则 `_placeholder()` 会 KeyError
3. **每个出口说自己的话**——五个函数是复制粘贴来的，最容易串行
"""
import re

import pytest

from app.graph.build import EXIT_NODES
from app.graph.nodes import PLACEHOLDER_TEXT, data_exit
from app.graph.routing import FALLBACK_ROUTE, INTENT_TO_ROUTE, route_by_intent

# **仍处于占位状态的出口** → 节点函数。
# knowledge / rules 是子图，human / fallback 已实现，行为由各自的测试覆盖。
PLACEHOLDER_FUNCS = {
    "data": data_exit,
}


# ── 第一类：路由表与出口集合的一致性 ────────────────────────────────


def test_route_table_covers_exactly_the_exit_nodes():
    """路由表的所有取值 = 图里注册的五个出口。多一个少一个都是 bug。"""
    assert set(INTENT_TO_ROUTE.values()) == set(EXIT_NODES)


def test_placeholder_text_matches_placeholder_impls():
    """占位文案的键 = 仍占位的出口，否则 `_placeholder()` 查表会 KeyError。"""
    assert set(PLACEHOLDER_TEXT) == set(PLACEHOLDER_FUNCS)
    assert set(PLACEHOLDER_TEXT) <= set(EXIT_NODES)


def test_twelve_intents_are_covered():
    """12 类意图一个不能少——少一个就会被路由兜底到 fallback，静默降级。"""
    assert len(INTENT_TO_ROUTE) == 12


def test_every_exit_has_a_node_function():
    """五个出口都得有实现（占位也算），否则图上会缺节点。"""
    from app.graph import build

    graph_nodes = set(build.build_graph().get_graph().nodes)
    assert set(EXIT_NODES) <= graph_nodes


# ── 第二类：分流逻辑 ────────────────────────────────────────────


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


# ── 第三类：占位出口的行为 ──────────────────────────────────────


@pytest.mark.parametrize("route", sorted(PLACEHOLDER_FUNCS))
async def test_each_placeholder_speaks_its_own_line(route: str):
    """占位出口最怕串行——`rules` 回了 `data` 的话术。"""
    result = await PLACEHOLDER_FUNCS[route]({})
    assert result["answer"] == PLACEHOLDER_TEXT[route]


def test_placeholder_answers_are_pairwise_distinct():
    """两条占位话术不能相同，否则说明有人复制完忘了改。"""
    answers = list(PLACEHOLDER_TEXT.values())
    assert len(set(answers)) == len(answers)


@pytest.mark.parametrize("route", sorted(PLACEHOLDER_FUNCS))
async def test_placeholders_mark_and_offer_transfer(route: str):
    """占位必须带两个标记：`is_placeholder`（给统计/前端）和转人工动作（给出路）。"""
    result = await PLACEHOLDER_FUNCS[route]({})
    assert result["is_placeholder"] is True
    assert {"type": "transfer_human"} in result["actions"]


@pytest.mark.parametrize("route", sorted(PLACEHOLDER_FUNCS))
async def test_placeholder_answers_contain_no_digits(route: str):
    """占位文案里一个数字都不许有。

    验收要求是"不得出现任何金额"，但比"不出现 元"更严的写法是**不含任何数字**——
    用户看到"退 5 天到账""需 20 元运费"这类占位文案，会当成真实承诺。
    """
    answer = (await PLACEHOLDER_FUNCS[route]({}))["answer"]
    assert not re.search(r"\d", answer), f"{route} 的占位文案含数字：{answer}"
