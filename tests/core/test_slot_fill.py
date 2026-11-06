"""槽位填充（M6）：取消 / 恢复 / 点选 / 正则 / 退款抽取 / 未命中。"""
from app.core import slot_fill


async def test_order_id_from_text():
    result = await slot_fill.fill("order_id", "1001")
    assert result.filled is True
    assert result.slot == "order_id"
    assert result.value == "1001"


async def test_order_id_fullwidth_digits():
    result = await slot_fill.fill("order_id", "１００１")
    assert result.value == "1001"


async def test_order_id_not_in_unrelated_question():
    result = await slot_fill.fill("order_id", "你们几点上班")
    assert result.filled is False


async def test_sku_is_uppercased():
    result = await slot_fill.fill("sku_id", "st-20")
    assert result.filled is True
    assert result.value == "ST-20"


async def test_spec_is_passthrough():
    result = await slot_fill.fill("spec", "星光灰")
    assert result.filled is True
    assert result.slot == "spec"
    assert result.value == "星光灰"


async def test_cancel():
    result = await slot_fill.fill("order_id", "算了，不退了")
    assert result.cancel is True
    assert result.filled is False


async def test_resume():
    result = await slot_fill.fill("order_id", "继续退费")
    assert result.resume is True
    assert result.filled is False


async def test_selected_action_fills_matching_slot():
    result = await slot_fill.fill(
        "order_id",
        "",
        selected={"type": "select_order", "order_id": "1001"},
    )
    assert result.filled is True
    assert result.value == "1001"


async def test_selected_action_with_value_field():
    result = await slot_fill.fill(
        "sku_id", "", selected={"type": "select_sku", "value": "st-60"}
    )
    assert result.value == "ST-60"


async def test_selected_order_id_is_stringified():
    result = await slot_fill.fill(
        "order_id", "", selected={"type": "select_order", "order_id": 1001}
    )
    assert result.value == "1001"


async def test_selected_action_type_mismatch_is_ignored():
    result = await slot_fill.fill(
        "order_id", "", selected={"type": "select_sku", "sku_id": "ST-20"}
    )
    assert result.filled is False


async def test_refund_extract_merges_into_draft(monkeypatch):
    async def fake_extract(text):
        return {"item_count": 1, "condition_ok": False}

    monkeypatch.setattr(slot_fill, "extract_draft", fake_extract)

    draft = {"refund": {"reason_code": "no_reason", "condition_ok": None}}
    result = await slot_fill.fill("reason_code", "就一件，包装拆了", draft=draft)

    assert result.filled is True
    assert result.value == {
        "reason_code": "no_reason",
        "condition_ok": False,
        "item_count": 1,
    }


async def test_refund_extract_returns_nothing(monkeypatch):
    async def fake_extract(text):
        return {}

    monkeypatch.setattr(slot_fill, "extract_draft", fake_extract)

    result = await slot_fill.fill("reason_code", "嗯……", draft={})
    assert result.filled is False


async def test_refund_extract_error_is_not_filled(monkeypatch):
    async def boom(text):
        raise TimeoutError("timeout")

    monkeypatch.setattr(slot_fill, "extract_draft", boom)

    result = await slot_fill.fill("reason_code", "质量问题", draft={})
    assert result.filled is False


async def test_no_pending_question(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("没有槽位不应调模型")

    monkeypatch.setattr(slot_fill, "extract_draft", boom)

    result = await slot_fill.fill(None, "1001")
    assert result.filled is False


async def test_empty_text_without_selection():
    result = await slot_fill.fill("order_id", "   ")
    assert result.filled is False


async def test_cancel_without_pending_question_is_not_a_signal():
    result = await slot_fill.fill(None, "算了")
    assert result.cancel is False
    assert result.filled is False


async def test_resume_without_pending_question_is_not_a_signal():
    result = await slot_fill.fill(None, "继续退费")
    assert result.resume is False
    assert result.filled is False
