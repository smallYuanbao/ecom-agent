"""缺订单号时的候选列表（B / C 共用）。

不查真实库：`list_user_orders` 打桩，只验证载荷形状与降级路径。
"""
from datetime import datetime

import pytest

from app.db.models import Order
from app.graph import order_select
from app.graph.order_select import candidate

CREATED = datetime(2026, 10, 29, 10, 0)


def fake_order(order_id: str, *, amount: int = 129900, status: str = "已签收") -> Order:
    return Order(
        id=order_id,
        user_id="u1",
        sku_id="ST-20",
        spec="石墨灰",
        status=status,
        amount=amount,
        quantity=1,
        created_at=CREATED,
    )


def test_candidate_shape():
    assert candidate(fake_order("1002")) == {
        "order_id": "1002",
        "title": "ST-20 石墨灰",
        "status": "已签收",
        "amount": 129900,
        "amount_text": "1299.00 元",
        "created_at": "2026-10-29",
    }


@pytest.fixture
def orders(monkeypatch):
    rows = [fake_order("1004"), fake_order("1003"), fake_order("1002"), fake_order("1001")]

    async def _list(*, user_id: str):
        assert user_id == "u1"
        return rows

    monkeypatch.setattr(order_select, "list_user_orders", _list)
    return rows


async def test_lists_at_most_three(orders):
    result = await order_select.ask_order({"user_id": "u1"})

    assert [c["order_id"] for c in result["order_candidates"]] == ["1004", "1003", "1002"]
    assert result["pending_question"] == "order_id"
    assert result["actions"][0]["type"] == "select_order"
    assert result["is_placeholder"] is False


async def test_does_not_write_validate_retry(orders):
    """`validate_retry` 是 C 出口自己的计数，B 不关心。"""
    result = await order_select.ask_order({"user_id": "u1"})
    assert "validate_retry" not in result


async def test_without_user_id_falls_back_to_text():
    result = await order_select.ask_order({})

    assert result["pending_question"] == "order_id"
    assert "order_candidates" not in result
    assert "actions" not in result


async def test_user_without_orders_falls_back_to_text(monkeypatch):
    async def _empty(*, user_id: str):
        return []

    monkeypatch.setattr(order_select, "list_user_orders", _empty)
    result = await order_select.ask_order({"user_id": "u9"})

    assert result["pending_question"] == "order_id"
    assert "order_candidates" not in result
