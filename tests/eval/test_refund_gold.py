"""退款 gold set 的结构守卫。

重点不是"标得对不对"（那是人判断），而是**标完没有、格式对不对**：

- 没填 `expected` 的案例要能被统计出来（`test_all_cases_are_labeled` 会 skip 提示进度）
- 填了的必须是合法形状，`reason_code` 在封闭集合里
- 案例之间不能自相矛盾（可退的必须有金额依据、拒答的不能给理由码）

标注标准见 `docs/design/电商客服-退款评测标注规则.md`。
"""
import json
from pathlib import Path

import pytest

GOLD = Path(__file__).resolve().parents[2] / "data/eval/refund_gold_20.json"

REASON_CODES = {
    "ok",
    "out_of_window",
    "not_applicable",
    "not_intact",
    "wrong_status",
    "reject_then_refund",
    "needs_human",
}

# 可退成功时允许的理由码：普通可退、拒收退款
ELIGIBLE_CODES = {"ok", "reject_then_refund"}


def _load() -> dict:
    return json.loads(GOLD.read_text(encoding="utf-8"))


CASES = _load()["cases"]


def test_has_twenty_cases():
    assert len(CASES) == 20


def test_ids_are_unique_and_ordered():
    ids = [case["id"] for case in CASES]
    assert len(set(ids)) == len(ids)
    assert ids == [f"G{i:02d}" for i in range(1, 21)]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_shape(case):
    assert set(case) >= {"id", "order", "request", "now", "query", "note", "expected"}
    # query 是 judge 的实际输入之一，不能为空；note 只是给人看的说明
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
def test_expected_shape_when_filled(case):
    """填了的 expected 必须是三种合法形状之一。

    - `{eligible: true, reason_code: "ok"|"reject_then_refund"}`
    - `{eligible: false, reason_code: <不可退的码>}`
    - `{needs_human: true}`
    """
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


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_unpaid_or_closed_orders_cannot_be_eligible(case):
    """填了 expected 时的常识校验：这几个状态不可能判可退。"""
    if case["expected"] is None or case["expected"].get("needs_human"):
        pytest.skip("尚未标注或标为转人工")

    if case["order"]["status"] in {"待付款", "已关闭", "退款中"}:
        assert case["expected"]["eligible"] is False, (
            f"{case['id']} 状态为 {case['order']['status']}，不应判可退"
        )


def test_labeling_progress_is_visible():
    """全部标完才通过；否则 skip 并报进度。

    这条不是"测试失败"，是**提醒还有多少没填**——
    跑 `pytest` 时看到 skip 就知道 gold set 还没准备好，不会误以为评测可跑了。
    """
    filled = sum(1 for case in CASES if case["expected"] is not None)
    if filled < len(CASES):
        pytest.skip(f"gold set 标注进度 {filled}/{len(CASES)}")
    assert filled == len(CASES)
