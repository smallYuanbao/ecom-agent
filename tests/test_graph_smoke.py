"""主图冒烟测试：图能编译、能分流、五个出口都还是占位。

**故意不调模型**——这里要验证的是"接线对不对"，不是"分类准不准"。
意图分类的准确率由 `tests/test_intent.py` 用真实模型覆盖。
"""
import pytest

from app.graph.build import EXIT_NODES, build_graph
from app.graph.routing import INTENT_TO_ROUTE


@pytest.fixture
def force_intent(monkeypatch):
    """把分类结果钉死，让测试离线且确定。

    做法与 `test_intent.py` 里的 mock 相同：把 `app.graph.nodes` 模块里的
    `classify` 换成假函数。**patch 的是「使用处」**——`classify_intent` 调
    `classify` 时去的就是这个模块的全局字典。

    `monkeypatch` 是 pytest 内置夹具，测试结束自动还原，不会污染后续用例。
    """

    def _set(intent: str, confidence: float = 0.9) -> None:
        async def fake(query: str) -> tuple[str, float, bool]:
            """假分类：不看 query，直接返回调用方指定的意图（三元组，含 multi_intent）。"""
            return intent, confidence, False

        monkeypatch.setattr("app.graph.nodes.classify", fake)

    return _set


def test_graph_compiles():
    assert build_graph() is not None


async def test_smoke_turn_flows_through_graph(force_intent):
    """一句话走完图：消解 → 分类 → 分流 → 出口。"""
    force_intent("政策问答")
    result = await build_graph().ainvoke({"raw_query": "退货政策是什么"})

    assert result["resolved_query"] == "退货政策是什么"    # 本章透传
    assert result["intent"] == "政策问答"
    assert result["route"] == "knowledge"
    assert result["is_placeholder"] is True
    assert result["answer"]                                # 占位也必须给话术


async def test_all_five_exit_nodes_registered():
    nodes = set(build_graph().get_graph().nodes)
    assert set(EXIT_NODES) <= nodes


@pytest.mark.parametrize("intent", sorted(INTENT_TO_ROUTE))
async def test_every_intent_lands_on_its_route(force_intent, intent):
    """12 类意图逐个验证：state 里的 route 与图实际走的出口一致。"""
    force_intent(intent)
    result = await build_graph().ainvoke({"raw_query": "测试"})
    assert result["route"] == INTENT_TO_ROUTE[intent]
    assert result["is_placeholder"] is True


async def test_unknown_intent_falls_back(force_intent):
    """模型返回清单外的意图 → 兜底，不抛 KeyError。"""
    force_intent("火星语")
    result = await build_graph().ainvoke({"raw_query": "测试"})
    assert result["route"] == "fallback"


async def test_placeholder_answers_contain_no_amount(force_intent):
    """占位响应不得出现金额——避免用户把"开发中"当成真实报价。"""
    for intent in sorted(INTENT_TO_ROUTE):
        force_intent(intent)
        result = await build_graph().ainvoke({"raw_query": "这单能退多少"})
        assert "元" not in result["answer"], f"{intent} 的占位文案出现金额"
