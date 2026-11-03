"""主图冒烟测试：图能编译、能分流、五个出口都在。

**故意不调模型、不查向量库**——这里验证的是"接线对不对"。
检索质量由 `scripts/eval_retrieval.py` 覆盖，生成质量由 `scripts/eval_knowledge.py`。

当前实现进度：五个出口全部是真实实现（Ch04 完成）。
"""
import re

import pytest

from app.graph.build import EXIT_NODES, build_graph
from app.graph.routing import INTENT_TO_ROUTE

# Ch04 之后五个出口全部实现，占位集合为空。
REAL_EXITS = frozenset(EXIT_NODES)
PLACEHOLDER_EXITS = frozenset(EXIT_NODES) - REAL_EXITS

STRONG_EVIDENCE = [
    {
        "id": "returns#退货运费由谁承担",
        "source": "returns.md",
        "section": "退货运费由谁承担",
        "text": "质量问题由商家承担。",
        "similarity": 0.74,
    }
]


@pytest.fixture
def offline(monkeypatch):
    """把分类、检索、生成全部钉死，让图测试离线且确定。"""

    def _set(intent: str, confidence: float = 0.9) -> None:
        async def fake_classify(query: str) -> tuple[str, float, bool]:
            return intent, confidence, False

        async def fake_search(query: str, *, top_k: int = 5, strategy=None):
            return STRONG_EVIDENCE

        async def fake_chat(messages, **kwargs) -> str:
            return "质量问题由商家承担[1]。"

        async def fake_record(**kwargs) -> int:
            return 1

        # B 出口：缺参数时会调这两个，打桩保证不碰真实数据库
        async def fake_skus(**kwargs):
            from app.tools.orders import ToolResult

            return ToolResult(False, "not_found")

        async def fake_orders(*, user_id: str):
            return []

        monkeypatch.setattr("app.graph.nodes.classify", fake_classify)
        monkeypatch.setattr("app.graph.knowledge.search", fake_search)
        monkeypatch.setattr("app.core.llm.chat", fake_chat)
        monkeypatch.setattr("app.graph.knowledge.record_low_conf", fake_record)
        monkeypatch.setattr("app.graph.data_exit.list_stock_skus", fake_skus)
        monkeypatch.setattr("app.graph.order_select.list_user_orders", fake_orders)

    return _set


def test_graph_compiles():
    assert build_graph() is not None


async def test_smoke_turn_flows_through_graph(offline):
    """一句话走完图：消解 → 分类 → 分流 → 出口。"""
    offline("政策问答")
    result = await build_graph().ainvoke({"raw_query": "退货政策是什么"})

    assert result["resolved_query"] == "退货政策是什么"    # 本章透传
    assert result["intent"] == "政策问答"
    assert result["route"] == "knowledge"
    assert result["is_placeholder"] is False               # 知识出口已实现
    assert result["citation_ids"] == ["returns#退货运费由谁承担"]


async def test_all_five_exit_nodes_registered():
    nodes = set(build_graph().get_graph().nodes)
    assert set(EXIT_NODES) <= nodes


@pytest.mark.parametrize("intent", sorted(INTENT_TO_ROUTE))
async def test_every_intent_lands_on_its_route(offline, intent):
    """12 类意图逐个验证：state 里的 route 与图实际走的出口一致。"""
    offline(intent)
    result = await build_graph().ainvoke({"raw_query": "测试"})

    route = INTENT_TO_ROUTE[intent]
    assert result["route"] == route
    # 占位与否取决于该出口是否已实现，不是"全都占位"
    assert result["is_placeholder"] is (route in PLACEHOLDER_EXITS)


async def test_unknown_intent_falls_back(offline):
    """模型返回清单外的意图 → 兜底，不抛 KeyError。"""
    offline("火星语")
    result = await build_graph().ainvoke({"raw_query": "测试"})
    assert result["route"] == "fallback"


@pytest.mark.parametrize(
    "intent",
    sorted(i for i, route in INTENT_TO_ROUTE.items() if route != "data"),
)
async def test_text_exits_do_not_emit_numbers(offline, intent):
    """文字类出口不得凭空给金额。

    **排除 data 出口**：数据查询的回答本来就该包含价格 / 库存 / 单号，
    「有数字」不是问题；那里守的是"数字必须来自数据库"（Task 7 的模板测试）。

    知识出口这里用的是 mock 生成，所以这条只守"文案里没有数字"，
    真实的"金额是否来自证据"由 Ch06 的输出校验负责。
    """
    offline(intent)
    result = await build_graph().ainvoke({"raw_query": "这单能退多少"})
    # 先剥掉引用角标 `[1]`——那是来源编号，不是金额
    text_without_citations = re.sub(r"\[\d+\]", "", result["answer"])
    assert not any(ch.isdigit() for ch in text_without_citations), result["answer"]
