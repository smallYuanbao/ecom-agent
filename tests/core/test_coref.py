"""指代消解（M3）：改写、短路、失败回落。"""
import pytest

from app.core import coref
from app.core.llm import LLMFormatError

HISTORY = [
    {"role": "user", "content": "订单1001 到哪了"},
    {"role": "assistant", "content": "快递 SF1001001 已签收。"},
]


def _fake_returning(value: str):
    async def fake(tool_name, parameters, messages):
        return {"resolved_query": value}

    return fake


async def test_rewrite_when_pronoun_needs_context(monkeypatch):
    monkeypatch.setattr(
        coref, "structured_call", _fake_returning("订单1001 的物流到哪了")
    )

    resolved, mode = await coref.resolve("那单呢", HISTORY)

    assert resolved == "订单1001 的物流到哪了"
    assert mode == "rewrite"


async def test_summary_is_included_in_prompt(monkeypatch):
    captured: dict = {}

    async def fake(tool_name, parameters, messages):
        captured["messages"] = messages
        return {"resolved_query": "订单1001 的物流到哪了"}

    monkeypatch.setattr(coref, "structured_call", fake)

    await coref.resolve("那单呢", HISTORY, summary="用户之前问过订单1001")

    user_content = captured["messages"][-1]["content"]
    assert "早前对话摘要：用户之前问过订单1001" in user_content
    assert "用户: 订单1001 到哪了" in user_content
    assert "<user_input>那单呢</user_input>" in user_content


async def test_empty_history_short_circuits(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("不应该调用模型")

    monkeypatch.setattr(coref, "structured_call", boom)

    resolved, mode = await coref.resolve("那单呢", [])

    assert resolved == "那单呢"
    assert mode == "passthrough"


async def test_explicit_order_id_short_circuits(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("不应该调用模型")

    monkeypatch.setattr(coref, "structured_call", boom)

    resolved, mode = await coref.resolve("订单1001 到哪了", HISTORY)

    assert resolved == "订单1001 到哪了"
    assert mode == "passthrough"


async def test_explicit_sku_short_circuits(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("不应该调用模型")

    monkeypatch.setattr(coref, "structured_call", boom)

    _, mode = await coref.resolve("ST-20 还有货吗", HISTORY)

    assert mode == "passthrough"


async def test_format_error_falls_back(monkeypatch):
    async def boom(*args, **kwargs):
        raise LLMFormatError("bad json")

    monkeypatch.setattr(coref, "structured_call", boom)

    resolved, mode = await coref.resolve("那单呢", HISTORY)

    assert resolved == "那单呢"
    assert mode == "passthrough_error"


async def test_unexpected_error_falls_back(monkeypatch):
    async def boom(*args, **kwargs):
        raise TimeoutError("timeout")

    monkeypatch.setattr(coref, "structured_call", boom)

    resolved, mode = await coref.resolve("那单呢", HISTORY)

    assert resolved == "那单呢"
    assert mode == "passthrough_error"


@pytest.mark.parametrize("value", ["", "  ", "嗯"])
async def test_too_short_result_falls_back(monkeypatch, value: str):
    monkeypatch.setattr(coref, "structured_call", _fake_returning(value))

    resolved, mode = await coref.resolve("那单呢", HISTORY)

    assert resolved == "那单呢"
    assert mode == "passthrough"


async def test_over_expanded_result_falls_back(monkeypatch):
    monkeypatch.setattr(
        coref,
        "structured_call",
        _fake_returning(
            "订单1001 的物流到哪了并且帮我查一下库存和价格还有优惠券以及会员权益和活动规则"
        ),
    )

    resolved, mode = await coref.resolve("那单呢", HISTORY)

    assert resolved == "那单呢"
    assert mode == "passthrough"


async def test_same_text_is_passthrough(monkeypatch):
    monkeypatch.setattr(coref, "structured_call", _fake_returning("那单呢"))

    resolved, mode = await coref.resolve("那单呢", HISTORY)

    assert resolved == "那单呢"
    assert mode == "passthrough"
