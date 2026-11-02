"""困难组 gold set（10 条）的结构与"难度"守卫。

这一组的用途和 `refund_gold_20.json` 不同：

- 原有 20 条：规则的覆盖范围内，测的是**模型有没有退化**
- 这 10 条：规则判不了、或有理由怀疑规则判错，测的是**模型的增量价值**

所以除了格式，还要守住一个性质：**A 组的规则实现必须返回 `None`**。
如果哪天规则被改得能判 A 组了，这组就不再是"规则判不了"的题，
指标会变得没有意义——那说明要重新挑案例，而不是改测试。
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.rules.eligibility import OrderSnapshot, RefundRequest, judge_eligibility

HARD = Path(__file__).resolve().parents[2] / "data/eval/refund_gold_hard_10.json"

REASON_CODES = {
    "ok",
    "out_of_window",
    "not_applicable",
    "not_intact",
    "wrong_status",
    "reject_then_refund",
    "needs_human",
}

ELIGIBLE_CODES = {"ok", "reject_then_refund"}

CASES = json.loads(HARD.read_text(encoding="utf-8"))["cases"]


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _rule_verdict(case: dict):
    order = case["order"]
    request = case["request"]
    return judge_eligibility(
        OrderSnapshot(
            order_id=order["order_id"],
            status=order["status"],
            amount=order["amount"],
            quantity=order["quantity"],
            category=order["category"],
            packaging_opened=order.get("packaging_opened"),
            created_at=_ts(order["created_at"]),
            shipped_at=_ts(order.get("shipped_at")),
            signed_at=_ts(order.get("signed_at")),
        ),
        RefundRequest(
            reason_code=request["reason_code"],
            item_count=request["item_count"],
            condition_ok=request["condition_ok"],
        ),
        now=datetime.fromisoformat(case["now"]),
    )


def test_has_ten_cases():
    assert len(CASES) == 10


def test_ids_are_unique_and_ordered():
    ids = [case["id"] for case in CASES]
    assert len(set(ids)) == len(ids)
    assert ids == [f"H{i:02d}" for i in range(1, 11)]


def test_group_split():
    """A 组 = 规则判不了，B 组 = 规则判得出但结论错。

    H04 原本在 B 组（质量缺陷导致的使用痕迹被规则判成不完好）；
    Ch03 Task 10 修了规则 8 之后，代码改为返回 `None`，于是它归入 A 组。
    **分组跟着规则走**——否则指标会描述一个已经不存在的规则行为。
    """
    groups = [case["group"] for case in CASES]
    assert groups.count("A") == 4
    assert groups.count("B") == 6


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_shape(case):
    assert set(case) >= {
        "id", "group", "order", "request", "now", "query", "note", "expected"
    }
    assert case["group"] in {"A", "B"}
    assert case["query"].strip(), f"{case['id']} 缺用户原话"
    assert case["note"].strip()

    order, request = case["order"], case["request"]
    assert order["status"] in {
        "待付款", "待发货", "已发货", "已签收", "退款中", "已关闭"
    }
    assert isinstance(order["amount"], int) and order["amount"] > 0
    assert isinstance(order["quantity"], int) and order["quantity"] >= 1
    assert request["reason_code"] in {"quality", "no_reason", "other"}
    assert 1 <= request["item_count"] <= order["quantity"]
    assert request["condition_ok"] in (True, False, None)


def test_group_a_is_undecidable_by_rules():
    """A 组的定义性质：规则实现返回 None。

    这条一旦失败，说明 A 组混进了规则能判的题——
    那它就不再能用来衡量"模型补规则的空"。
    """
    group_a = [case for case in CASES if case["group"] == "A"]
    for case in group_a:
        assert _rule_verdict(case) is None, (
            f"{case['id']} 规则本应判不了，实际给出 {_rule_verdict(case)}"
        )


def test_group_b_is_decided_by_rules():
    """B 组的定义性质：规则给出结论（对错不论）。

    只有规则先给出一个答案，才能对比"模型是否更接近人工标注"。
    """
    group_b = [case for case in CASES if case["group"] == "B"]
    for case in group_b:
        assert _rule_verdict(case) is not None, (
            f"{case['id']} 规则判不了，应归入 A 组"
        )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_expected_shape_when_filled(case):
    expected = case["expected"]
    if expected is None:
        pytest.skip("尚未标注")

    if expected.get("needs_human"):
        assert expected == {"needs_human": True}
        return

    assert set(expected) == {"eligible", "reason_code"}
    assert isinstance(expected["eligible"], bool)
    assert expected["reason_code"] in REASON_CODES

    if expected["eligible"]:
        assert expected["reason_code"] in ELIGIBLE_CODES
    else:
        assert expected["reason_code"] not in ELIGIBLE_CODES


def test_labeling_progress_is_visible():
    filled = sum(1 for case in CASES if case["expected"] is not None)
    if filled < len(CASES):
        pytest.skip(f"困难组标注进度 {filled}/{len(CASES)}")
    assert filled == len(CASES)
