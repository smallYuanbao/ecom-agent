"""意图分类：12 类各一例（真实模型）+ 降级路径（mock，不走网络）。

验收标准：12 例中 ≥11 命中（允许 1 例失败并记录）。
没有 `CHAT_API_KEY` 时整体跳过——不要让缺密钥的机器上跑出一堆红。
"""
import pytest

from app.config import get_settings
from app.core.llm import LLMFormatError
from app.graph.nodes import classify, classify_intent
from app.graph.routing import DEFAULT_INTENT, FALLBACK_ROUTE, INTENT_TO_ROUTE

requires_api_key = pytest.mark.skipif(
    not get_settings().chat_api_key, reason="需要 CHAT_API_KEY"
)

# 每类一句典型问法
CASES: list[tuple[str, str]] = [
    ("你们的退货政策是什么？", "政策问答"),
    ("ST-60 有多重，折叠后多大？", "商品规格"),
    ("满 300 减 50 的券能跟会员折扣一起用吗？", "优惠券活动"),
    ("会员积分怎么算，可以抵现吗？", "会员权益"),
    ("帮我看看订单 1001 的状态", "订单查询"),
    ("我的快递到哪了？", "物流查询"),
    ("ST-20 还有货吗？多少钱？", "库存价格"),
    ("订单 1001 我要退货", "退款申请"),
    ("我上周买的推车降价了，能补差价吗？", "价保申请"),
    ("订单 1001 能改一下收货地址吗？", "订单变更"),
    ("你们这什么破服务，等了三天都没人管", "投诉"),
    ("在吗", "闲聊"),
]


@requires_api_key
@pytest.mark.parametrize(("query", "expected"), CASES)
async def test_intent_classification(query: str, expected: str):
    intent, confidence = await classify(query)
    assert intent == expected, f"「{query}」判成 {intent}（期望 {expected}）"
    assert 0.0 <= confidence <= 1.0


@requires_api_key
async def test_node_writes_route_consistent_with_routing_table():
    """节点写的 route 必须与路由表一致，否则 state 与图的分支会打架。"""
    result = await classify_intent({"resolved_query": "订单 1001 我要退货"})
    assert result["route"] == INTENT_TO_ROUTE.get(result["intent"], FALLBACK_ROUTE)
    assert result["route"] == "rules"


async def test_format_error_degrades_to_default_intent(monkeypatch):
    """模型没按格式返回 → 降级成「其他」，不抛异常。"""

    async def boom(*args, **kwargs):
        raise LLMFormatError("模型未返回 tool_calls")

    monkeypatch.setattr("app.graph.nodes.structured_call", boom)
    intent, confidence = await classify("随便问点什么")
    assert (intent, confidence) == (DEFAULT_INTENT, 0.0)


async def test_out_of_vocabulary_intent_degrades(monkeypatch):
    """模型返回了清单外的意图词 → 也降级成「其他」，不让 KeyError 冒出来。"""

    async def weird(*args, **kwargs):
        return {"intent": "退款", "confidence": 0.9}   # 「退款」不在 12 类里

    monkeypatch.setattr("app.graph.nodes.structured_call", weird)
    intent, _ = await classify("我要退款")
    assert intent == DEFAULT_INTENT


async def test_node_falls_back_to_raw_query_when_resolved_empty(monkeypatch):
    """`resolved_query` 为空时回落原话，不能把空串送去分类。"""
    seen: list[str] = []

    async def capture(query: str):
        seen.append(query)
        return "闲聊", 0.9

    monkeypatch.setattr("app.graph.nodes.classify", capture)
    result = await classify_intent({"raw_query": "在吗", "resolved_query": ""})
    assert seen == ["在吗"]
    assert result["route"] == "fallback"
