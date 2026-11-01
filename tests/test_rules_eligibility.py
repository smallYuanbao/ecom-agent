"""规则表判定：10 条规则各一正一反。

这套代码不决定最终结论（那是 `judge` 的事），它只用于 `compliance` 的独立复算。
所以测试重点是**每条规则都真的被执行到**，以及**顺序生效**。
"""
from datetime import datetime, timedelta

import pytest

from app.rules.eligibility import (
    EligibilityVerdict,
    OrderSnapshot,
    RefundRequest,
    judge_eligibility,
)

NOW = datetime(2026, 11, 5, 10, 0)


def order(**kwargs) -> OrderSnapshot:
    """默认一个「已签收 3 天、普通商品」的可退订单，按需覆盖字段。"""
    defaults = dict(
        order_id="1001",
        status="已签收",
        amount=269900,
        quantity=1,
        category="婴儿推车",
        packaging_opened=True,
        created_at=NOW - timedelta(days=7),
        shipped_at=NOW - timedelta(days=5),
        signed_at=NOW - timedelta(days=3),
    )
    defaults.update(kwargs)
    return OrderSnapshot(**defaults)


def request(**kwargs) -> RefundRequest:
    defaults = dict(reason_code="no_reason", item_count=1, condition_ok=True)
    defaults.update(kwargs)
    return RefundRequest(**defaults)


def verdict(**kwargs) -> EligibilityVerdict | None:
    return judge_eligibility(order(), request(**kwargs), now=NOW)


# ── 基线 ────────────────────────────────────────────────────


def test_normal_order_is_eligible():
    assert verdict() == EligibilityVerdict(True, "ok")


# ── 规则 1–3：状态不允许 ────────────────────────────────────


@pytest.mark.parametrize("status", ["待付款", "退款中", "已关闭"])
def test_bad_status_is_rejected(status: str):
    result = judge_eligibility(order(status=status), request(), now=NOW)
    assert result == EligibilityVerdict(False, "wrong_status")


# ── 规则 4：待发货可取消 ────────────────────────────────────


def test_pending_shipment_can_be_cancelled():
    result = judge_eligibility(
        order(status="待发货", shipped_at=None, signed_at=None), request(), now=NOW
    )
    assert result == EligibilityVerdict(True, "ok")


# ── 规则 5：已发货未签收 → 拒收退款 ─────────────────────────


def test_shipped_not_signed_goes_reject_refund():
    result = judge_eligibility(
        order(status="已发货", signed_at=None), request(), now=NOW
    )
    assert result == EligibilityVerdict(True, "reject_then_refund")


def test_shipped_not_signed_ignores_signed_time():
    """未签收时不该去算 7 天窗口——根本没有起算点。"""
    result = judge_eligibility(
        order(status="已发货", signed_at=None, created_at=NOW - timedelta(days=60)),
        request(), now=NOW,
    )
    assert result == EligibilityVerdict(True, "reject_then_refund")


# ── 规则 6：法定不适用（第六条）─────────────────────────────


@pytest.mark.parametrize("category", ["婴儿推车（刻字定制款）", "鲜活水果", "音像制品", "报刊"])
def test_not_applicable_categories(category: str):
    result = judge_eligibility(order(category=category), request(), now=NOW)
    assert result == EligibilityVerdict(False, "not_applicable")


# ── 规则 7：第七条（已拆封入口喂养用品）─────────────────────


def test_opened_bottle_is_not_applicable():
    result = judge_eligibility(
        order(category="奶瓶", packaging_opened=True), request(), now=NOW
    )
    assert result == EligibilityVerdict(False, "not_applicable")


def test_sealed_bottle_is_eligible():
    """未拆封的奶瓶可以退——规则只管"拆封后"。"""
    result = judge_eligibility(
        order(category="奶瓶", packaging_opened=False), request(), now=NOW
    )
    assert result == EligibilityVerdict(True, "ok")


def test_bottle_with_unknown_packaging_falls_through_to_condition():
    """拆封状态未知时，不因为"是奶瓶"就拒——继续按完好程度判。"""
    result = judge_eligibility(
        order(category="奶瓶", packaging_opened=None), request(), now=NOW
    )
    assert result == EligibilityVerdict(True, "ok")


# ── 规则 8：不完好 ──────────────────────────────────────────


def test_not_intact_is_rejected():
    assert verdict(condition_ok=False) == EligibilityVerdict(False, "not_intact")


def test_unknown_condition_returns_none():
    """代码不知道完好与否时返回 None——不是"不可退"，是"判不了"。"""
    assert verdict(condition_ok=None) is None


# ── 规则 9：7 天窗口 ────────────────────────────────────────


def test_seventh_day_is_still_eligible():
    """自签收次日起算：签收日 +7 天当天仍可退。"""
    result = judge_eligibility(
        order(signed_at=NOW - timedelta(days=7)), request(), now=NOW
    )
    assert result == EligibilityVerdict(True, "ok")


def test_eighth_day_is_out_of_window():
    result = judge_eligibility(
        order(signed_at=NOW - timedelta(days=8)), request(), now=NOW
    )
    assert result == EligibilityVerdict(False, "out_of_window")


def test_quality_issue_ignores_window():
    """质量问题不受 7 天限制。"""
    result = judge_eligibility(
        order(signed_at=NOW - timedelta(days=15)), request(reason_code="quality"), now=NOW
    )
    assert result == EligibilityVerdict(True, "ok")


def test_signed_at_missing_on_signed_order_returns_none():
    """已签收却没有签收时间——数据不一致，代码判不了。"""
    result = judge_eligibility(order(signed_at=None), request(), now=NOW)
    assert result is None


# ── 顺序 ────────────────────────────────────────────────────


def test_status_wins_over_category():
    """状态不允许时先返回，不会继续去看商品类别。"""
    result = judge_eligibility(order(status="已关闭", category="刻字定制"), request(), now=NOW)
    assert result == EligibilityVerdict(False, "wrong_status")


def test_category_wins_over_window():
    """不适用商品被拒，理由码是 not_applicable 而不是 out_of_window。"""
    result = judge_eligibility(
        order(category="刻字定制", signed_at=NOW - timedelta(days=30)), request(), now=NOW
    )
    assert result == EligibilityVerdict(False, "not_applicable")
