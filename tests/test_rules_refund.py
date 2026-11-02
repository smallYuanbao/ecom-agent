"""退款金额纯函数：金额公式 + 纯函数约束。

金额规则的唯一依据是 `docs/design/电商客服-规则表.md` §4.1。
这里不测"该不该退"（那是 `judge` 的事），只测"退多少"。

**为什么金额要单独测**：它是唯一能和用户复述的数字。
判错资格是体验问题，算错金额是**资金问题**——所以逐条对拍，不留模糊。
"""
import inspect
from datetime import datetime

import pytest

from app.rules.eligibility import OrderSnapshot, RefundRequest
from app.rules.refund import Judgement, compute_amount

CREATED = datetime(2026, 10, 29, 10, 0)  # 快照字段，compute_amount 不该读它


def order(**kwargs) -> OrderSnapshot:
    """默认：已签收、实付 2699 元、1 件。"""
    defaults = {
        "order_id": "1001",
        "status": "已签收",
        "amount": 269900,
        "quantity": 1,
        "category": "婴儿推车",
        "packaging_opened": True,
        "created_at": CREATED,
        "shipped_at": None,
        "signed_at": None,
    }
    defaults.update(kwargs)
    return OrderSnapshot(**defaults)


def request(**kwargs) -> RefundRequest:
    defaults = {"reason_code": "no_reason", "item_count": 1, "condition_ok": True}
    defaults.update(kwargs)
    return RefundRequest(**defaults)


def judgement(**kwargs) -> Judgement:
    defaults = {"eligible": True, "reason_code": "ok"}
    defaults.update(kwargs)
    return Judgement(**defaults)


# ── 不可退 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason_code", ["out_of_window", "not_applicable", "not_intact", "wrong_status"]
)
def test_ineligible_returns_zero(reason_code: str):
    assert compute_amount(order(), judgement(eligible=False, reason_code=reason_code), request()) == 0


# ── 整单退 ──────────────────────────────────────────────────


def test_full_return_single_item():
    assert compute_amount(order(), judgement(), request(item_count=1)) == 269900


def test_full_return_multi_item():
    result = compute_amount(
        order(quantity=2), judgement(), request(item_count=2)
    )
    assert result == 269900


# ── 部分退 ──────────────────────────────────────────────────


def test_partial_return_two_of_three():
    """1000 分买 3 件退 1 件 → 333 分（向下取整，不四舍五入）。"""
    result = compute_amount(
        order(amount=1000, quantity=3), judgement(), request(item_count=1)
    )
    assert result == 333


def test_partial_return_is_floor_not_round():
    """999 / 3 = 333 恰好整除；换成 1001 / 3 = 333.67，必须取 333。"""
    result = compute_amount(
        order(amount=1001, quantity=3), judgement(), request(item_count=1)
    )
    assert result == 333


def test_partial_return_two_items_half():
    assert compute_amount(
        order(amount=189900, quantity=2), judgement(), request(item_count=1)
    ) == 94950


# ── 越界 ────────────────────────────────────────────────────


def test_item_count_over_quantity_is_capped():
    """件数大于总件数时按总件数封顶——不能退超过实付的钱。"""
    result = compute_amount(
        order(amount=1000, quantity=2), judgement(), request(item_count=5)
    )
    assert result == 1000


def test_zero_item_count_returns_zero():
    assert compute_amount(order(), judgement(), request(item_count=0)) == 0


def test_negative_item_count_returns_zero():
    assert compute_amount(order(), judgement(), request(item_count=-1)) == 0


# ── 整单退的两种特殊情形 ────────────────────────────────────


def test_pending_shipment_refunds_full_amount_even_for_one_item():
    """待发货是整单取消，不按件均摊——买 2 件退 1 件也退全款。"""
    result = compute_amount(
        order(status="待发货", amount=189900, quantity=2),
        judgement(),
        request(item_count=1),
    )
    assert result == 189900


def test_reject_then_refund_refunds_full_amount():
    """已发货未签收 → 拒收退款。用户没见过货，没有"退几件"的概念。"""
    result = compute_amount(
        order(status="已发货", amount=189900, quantity=2),
        judgement(reason_code="reject_then_refund"),
        request(item_count=1),
    )
    assert result == 189900


# ── 数据异常 ────────────────────────────────────────────────


def test_zero_quantity_returns_zero_instead_of_dividing_by_zero():
    assert compute_amount(
        order(quantity=0), judgement(), request(item_count=1)
    ) == 0


# ── 金额边界 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "quantity", "item_count"),
    [
        (269900, 1, 1),
        (1000, 3, 1),
        (189900, 2, 1),
        (999, 7, 5),
        (1, 3, 1),
    ],
)
def test_refund_never_exceeds_paid_amount(amount: int, quantity: int, item_count: int):
    result = compute_amount(
        order(amount=amount, quantity=quantity), judgement(), request(item_count=item_count)
    )
    assert 0 <= result <= amount


def test_result_is_integer_cents():
    """金额单位是分，不能出现小数分。"""
    result = compute_amount(
        order(amount=1000, quantity=3), judgement(), request(item_count=1)
    )
    assert isinstance(result, int)
    assert result == 333


# ── 纯函数约束 ──────────────────────────────────────────────


def test_is_not_a_coroutine():
    """有 `await` 就是 IO，金额就没法逐条对拍。"""
    assert not inspect.iscoroutinefunction(compute_amount)


def test_signature_has_no_time_knob():
    """不传 `now`：同一条数据今天算和明天算必须一样。"""
    params = list(inspect.signature(compute_amount).parameters)
    assert params == ["order", "judgement", "request"]


def test_is_deterministic():
    args = (order(amount=1000, quantity=3), judgement(), request(item_count=1))
    assert compute_amount(*args) == compute_amount(*args) == 333
