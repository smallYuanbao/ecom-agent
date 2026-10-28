"""种子数据自检：覆盖度够不够，决定后面几章的测试有没有素材。

不测"能不能写进库"（那是 init_db/seed 脚本的事），只测**数据本身的形状**：
状态齐不齐、每人几单、有没有过期券、有没有超期单。
"""
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.seed import (
    COUPONS,
    LOGISTICS,
    NOW,
    ORDERS,
    STOCK,
    _coupon_rows,
    _order_rows,
)

ALL_STATUSES = {"待付款", "待发货", "已发货", "已签收", "退款中", "已关闭"}


def test_six_skus_with_one_sold_out_and_one_presale():
    assert len(STOCK) == 6
    statuses = Counter(s[4] for s in STOCK)
    assert statuses["sold_out"] == 1
    assert statuses["presale"] == 1


def test_twenty_orders_cover_all_six_statuses():
    assert len(ORDERS) == 20
    assert {o[4] for o in ORDERS} == ALL_STATUSES


def test_each_user_owns_three_to_five_orders():
    per_user = Counter(o[0] for o in ORDERS)
    assert len(per_user) == 5
    assert all(3 <= n <= 5 for n in per_user.values())


def test_order_id_prefix_matches_owner():
    """订单号首位即归属：1001→u1、2001→u2……写错会污染越权测试。"""
    for user_id, oid, *_ in ORDERS:
        assert user_id == f"u{oid[0]}", f"{oid} 的归属应为 u{oid[0]}，实际 {user_id}"


def test_one_order_is_older_than_seven_days():
    """退货期 / 价保都从下单日算，必须有一单落在窗口外。"""
    assert any(NOW - row.created_at > timedelta(days=7) for row in _order_rows())


def test_coupons_cover_expired_used_and_below_threshold():
    rows = _coupon_rows()
    assert len(rows) == 5
    assert any(c.expires_at < NOW for c in rows), "缺一张已过期的券"
    assert any(c.used == 1 for c in rows), "缺一张已使用的券"
    assert any(c.threshold > 100_000 for c in rows), "缺一张高门槛（未达）的券"


def test_amounts_never_exceed_list_price():
    prices = {sku: price for sku, _spec, _stock, price, _status in STOCK}
    for order in _order_rows():
        assert 0 < order.amount <= prices[order.sku_id]


def test_logistics_covers_four_shapes():
    latest = {latest for latest, _nodes in LOGISTICS.values()}
    assert {"运输中", "已签收", "异常"} <= latest


def test_coupon_codes_are_unique():
    codes = [c[1] for c in COUPONS]
    assert len(codes) == len(set(codes))
