"""退款 blind test（15 条）的结构与难度守卫。

这组和 `refund_gold_hard_10.json`（dev）分开：

- dev 已经参与过 prompt / 规则调整；
- test 定稿后只跑不改，测的是**没见过的困难样本**。

除了格式，还要守住「它仍然是困难组」：每个类型至少 2 条是规则判不了
或规则判错的。如果哪天规则把这些题都覆盖了，指标会变成规则的自测，
需要重新挑案例，而不是放宽测试。
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.rules.eligibility import OrderSnapshot, RefundRequest, judge_eligibility
from scripts.eval_refund import wilson_interval

EVAL_DIR = Path(__file__).resolve().parents[2] / "data/eval"
TEST = EVAL_DIR / "refund_hard_test.json"
DEV = EVAL_DIR / "refund_gold_hard_10.json"
SIMPLE = EVAL_DIR / "refund_gold_20.json"

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
GROUPS = {
    "rule_order",
    "field_granularity",
    "data_gap",
    "semantic_ambiguity",
    "multi_turn",
}

CASES = json.loads(TEST.read_text(encoding="utf-8"))["cases"]


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _rule_verdict(case: dict) -> dict | None:
    order = case["order"]
    request = case["request"]
    verdict = judge_eligibility(
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
    if verdict is None:
        return None
    return {"eligible": verdict.eligible, "reason_code": verdict.reason_code}


def test_has_fifteen_cases_and_five_groups():
    assert len(CASES) == 15
    counts = {group: 0 for group in GROUPS}
    for case in CASES:
        counts[case["group"]] += 1
    assert counts == {group: 3 for group in GROUPS}


def test_ids_and_order_ids_are_unique_across_gold_sets():
    ids = [case["id"] for case in CASES]
    assert ids == [f"T{i:02d}" for i in range(1, 16)]

    other = json.loads(DEV.read_text(encoding="utf-8"))["cases"]
    other += json.loads(SIMPLE.read_text(encoding="utf-8"))["cases"]
    other_orders = {case["order"]["order_id"] for case in other}
    test_orders = {case["order"]["order_id"] for case in CASES}
    assert not (test_orders & other_orders)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_shape(case):
    assert set(case) >= {
        "id", "group", "order", "request", "now", "query", "note", "expected"
    }
    assert case["group"] in GROUPS
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


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_expected_shape(case):
    expected = case["expected"]
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


def test_each_non_reference_group_keeps_at_least_two_rule_failures():
    """难度守卫：除规则顺序参照组外，每组最多 1 条能被规则直接判对。"""
    for group in GROUPS - {"rule_order"}:
        cases = [case for case in CASES if case["group"] == group]
        hard = [
            case for case in cases
            if (verdict := _rule_verdict(case)) is None or verdict != case["expected"]
        ]
        assert len(hard) >= 2, f"{group} 只剩 {len(hard)} 条规则失败，需补新题"


def test_rule_order_group_matches_rule_reference():
    """规则顺序组是「参照组」：gold 必须与规则表一致。

    它测的是模型是否遵循既定优先级，不承担「规则失败」的难度指标。
    如果这里不一致，说明 T01/T03 又回到了 B07 那种 gold 冲突。
    """
    cases = [case for case in CASES if case["group"] == "rule_order"]
    for case in cases:
        assert _rule_verdict(case) == case["expected"], (
            f"{case['id']} 的 gold 与规则表不一致；先确认以哪边为准"
        )


def test_wilson_interval_is_reported():
    low, high = wilson_interval(13, 15)
    assert 0.0 <= low < 13 / 15 < high <= 1.0
