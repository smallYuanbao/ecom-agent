"""订单仓库：越权校验的四个面（漏参 / 越权 / 不存在 / 正常）。"""
from datetime import datetime

import pytest

from app.db.errors import NotFound
from app.db.models import Order
from app.db.repo_order import get_order


@pytest.fixture
async def orders(session):
    """u1 持有 1002；u2 持有 2001。"""
    session.add_all(
        [
            Order(
                id="1002",
                user_id="u1",
                sku_id="ST-60",
                spec="标准装",
                status="已签收",
                amount=9900,
                created_at=datetime(2026, 10, 1, 10, 0),
            ),
            Order(
                id="2001",
                user_id="u2",
                sku_id="ST-60",
                spec="标准装",
                status="已付款",
                amount=9900,
                created_at=datetime(2026, 10, 2, 10, 0),
            ),
        ]
    )
    await session.commit()
    return session


async def test_user_id_is_required(orders):
    """漏传 user_id 直接 TypeError——不能让"忘了传"变成"查到所有人的单"。"""
    with pytest.raises(TypeError):
        await get_order("1002")


async def test_other_users_order_raises_not_found(orders):
    with pytest.raises(NotFound):
        await get_order("1002", user_id="u2", session=orders)


async def test_missing_order_raises_same_not_found(orders):
    """不存在与越权必须同一种异常，否则调用方能枚举出订单是否存在。"""
    with pytest.raises(NotFound) as exc:
        await get_order("9999", user_id="u1", session=orders)
    assert exc.value.args == NotFound("order not found").args


async def test_own_order_is_returned(orders):
    order = await get_order("1002", user_id="u1", session=orders)
    assert order.id == "1002"
    assert order.amount == 9900
