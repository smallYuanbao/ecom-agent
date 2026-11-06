"""resume 路由：意图短路、抽取跳过。"""
from app.graph import nodes
from app.graph.rules_exit import extract


async def test_classify_intent_resume_short_circuits(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("resume 轮不应调分类模型")

    monkeypatch.setattr(nodes, "classify", boom)

    result = await nodes.classify_intent(
        {"resume_route": "rules", "intent": "退款申请", "raw_query": "1001"}
    )

    assert result == {
        "intent": "退款申请",
        "intent_confidence": 1.0,
        "route": "rules",
        "resumed": True,
    }


async def test_classify_intent_unknown_resume_falls_through(monkeypatch):
    async def fake_classify(query: str):
        return "闲聊", 0.9, False

    monkeypatch.setattr(nodes, "classify", fake_classify)

    result = await nodes.classify_intent(
        {"resume_route": "不存在的出口", "raw_query": "你好"}
    )

    assert result["route"] == "fallback"
    assert result["intent"] == "闲聊"


async def test_extract_skip_keeps_draft(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("skip_extract 时不应调抽取模型")

    monkeypatch.setattr("app.graph.rules_exit.extract_draft", boom)

    result = await extract(
        {"skip_extract": True, "refund_draft": {"reason_code": "no_reason"}}
    )

    assert result["refund_draft"] == {"reason_code": "no_reason"}
