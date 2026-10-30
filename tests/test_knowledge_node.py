"""知识出口子图：引用解析、闸的分流、弱证据落池。

全部离线：检索与模型都被 monkeypatch 掉。
真实检索质量由 `scripts/eval_retrieval.py` 覆盖，真实生成质量由
`scripts/eval_knowledge.py` 覆盖。
"""
import pytest

from app.graph import knowledge
from app.graph.knowledge import _topic, build_knowledge_graph, parse_citations

EVIDENCE = [
    {"id": "returns#退货运费由谁承担", "source": "returns.md", "section": "退货运费由谁承担",
     "text": "质量问题商家承担。", "similarity": 0.74},
    {"id": "shipping#运费险", "source": "shipping.md", "section": "运费险",
     "text": "单笔上限 12 元。", "similarity": 0.69},
    {"id": "returns#本文档不提供什么", "source": "returns.md", "section": "本文档不提供什么",
     "text": "不计算退款金额。", "similarity": 0.60},
]


# ── 引用解析 ────────────────────────────────────────────────


def test_parse_single_and_multiple_citations():
    answer = "商家承担运费[1]。运费险上限 12 元[2]。两者都写在政策里[1][2]。"
    assert parse_citations(answer, EVIDENCE) == [
        "returns#退货运费由谁承担",
        "shipping#运费险",
    ]


def test_parse_deduplicates_and_keeps_order():
    """同一条资料引用两次只留一个 id，顺序按首次出现。"""
    assert parse_citations("[2] 然后 [1] 再 [2]", EVIDENCE) == [
        "shipping#运费险",
        "returns#退货运费由谁承担",
    ]


def test_parse_ignores_out_of_range():
    """模型可能引用不存在的编号——丢弃，不能抛异常。"""
    assert parse_citations("瞎编[9]但有依据[1]", EVIDENCE) == ["returns#退货运费由谁承担"]


def test_parse_returns_empty_when_no_citation():
    """引用失败不该让整轮失败：给空列表，前端不展示引用区而已。"""
    assert parse_citations("这是一段没有任何角标的回答。", EVIDENCE) == []


def test_parse_handles_absent_evidence():
    assert parse_citations("[1][2]", []) == []


# ── 落池话术里的 topic ────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"resolved_query": "能分期付款吗"}, "能分期付款吗"),
        ({"raw_query": "在吗"}, "在吗"),
        ({}, "您的问题"),
    ],
)
def test_topic_falls_back_gracefully(state, expected):
    assert _topic(state) == expected


def test_topic_is_truncated():
    """太长的原话要截断，否则兜底话术会变成一大段复读。"""
    assert len(_topic({"raw_query": "啊" * 100})) == knowledge.TOPIC_MAX_CHARS


# ── 子图分流 ────────────────────────────────────────────────


async def _invoke_with(monkeypatch, *, evidence, answer="基于证据的回答[1]"):
    recorded: dict = {}

    async def fake_search(query, *, top_k=5, strategy=None):
        return evidence

    async def fake_chat(messages, **kwargs):
        return answer

    async def fake_record(**kwargs):
        recorded.update(kwargs)
        return 1

    monkeypatch.setattr("app.graph.knowledge.search", fake_search)
    monkeypatch.setattr("app.core.llm.chat", fake_chat)
    monkeypatch.setattr("app.graph.knowledge.record_low_conf", fake_record)

    result = await build_knowledge_graph().ainvoke(
        {"raw_query": "退货运费谁出", "resolved_query": "退货运费谁出"}
    )
    return result, recorded


async def test_strong_evidence_generates_with_citations(monkeypatch):
    result, recorded = await _invoke_with(monkeypatch, evidence=EVIDENCE)

    assert result["answer"] == "基于证据的回答[1]"
    assert result["citation_ids"] == ["returns#退货运费由谁承担"]
    assert result["is_placeholder"] is False
    assert recorded == {}                       # 强证据不落池


async def test_weak_evidence_goes_to_fallback_and_records(monkeypatch):
    weak = [{**EVIDENCE[0], "similarity": 0.30}]
    result, recorded = await _invoke_with(monkeypatch, evidence=weak)

    assert "没有找到确切的依据" in result["answer"]
    assert result["citation_ids"] == []
    assert {"type": "transfer_human"} in result["actions"]
    # 弱证据必须落池，且带上当时的召回——那是"为什么判它弱"的证据
    assert recorded["hits"] == weak
    assert recorded["reason"] == "retrieval_low_conf"


async def test_zero_hits_goes_to_fallback_without_candidate_title(monkeypatch):
    """零召回时不能反问「您是不是想问 X」——没有 X。"""
    result, recorded = await _invoke_with(monkeypatch, evidence=[])

    assert "转人工" in result["answer"]
    assert "您是不是想问" not in result["answer"]
    assert recorded["hits"] == []
