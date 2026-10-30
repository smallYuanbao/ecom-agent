"""主图冒烟测试：图能编译、能分流、五个出口都在。

**故意不调模型、不查向量库**——这里验证的是"接线对不对"。
检索质量由 `scripts/eval_retrieval.py` 覆盖，生成质量由 `scripts/eval_knowledge.py`。

当前实现进度：`knowledge`（子图）、`human`（转接话术）、`fallback`（三脚本）
已实现；`data`（Ch04）、`rules`（Ch03）仍是占位。
"""
import re

import pytest

from app.graph.build import EXIT_NODES, build_graph
from app.graph.routing import INTENT_TO_ROUTE

# 已实现的出口：不是占位。其余仍是占位。
# knowledge（Task 7）、human / fallback（Task 8）已实现。
REAL_EXITS = frozenset({"knowledge", "human", "fallback"})
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

        monkeypatch.setattr("app.graph.nodes.classify", fake_classify)
        monkeypatch.setattr("app.graph.knowledge.search", fake_search)
        monkeypatch.setattr("app.core.llm.chat", fake_chat)
        monkeypatch.setattr("app.graph.knowledge.record_low_conf", fake_record)

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


@pytest.mark.parametrize("intent", sorted(INTENT_TO_ROUTE))
async def test_no_exit_emits_amounts(offline, intent):
    """占位与已实现出口都不得凭空给金额。

    知识出口这里用的是 mock 生成，所以这条只守"文案里没有数字"，
    真实的"金额是否来自证据"由 Ch06 的输出校验负责。
    """
    offline(intent)
    result = await build_graph().ainvoke({"raw_query": "这单能退多少"})
    # 先剥掉引用角标 `[1]`——那是来源编号，不是金额
    text_without_citations = re.sub(r"\[\d+\]", "", result["answer"])
    assert not any(ch.isdigit() for ch in text_without_citations), result["answer"]
