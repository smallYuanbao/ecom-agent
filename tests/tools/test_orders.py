"""B 出口的只读工具：错误码与返回形状。

用临时库（`session` 夹具），不碰 `data/app.db`。
重点不是"能查到"，是**失败时返回哪个码**——图要靠它分流。
"""
import json
from datetime import datetime

import pytest

from app.db.models import Logistics, Order, Stock
from app.tools import orders as tools

CREATED = datetime(2026, 10, 29, 10, 0)

ORDER_DEFAULTS = {
    "sku_id": "ST-20",
    "spec": "石墨灰",
    "category": "婴儿推车",
    "status": "已签收",
    "amount": 129900,
    "quantity": 1,
    "created_at": CREATED,
    "paid_at": CREATED,
    "shipped_at": CREATED,
    "signed_at": CREATED,
    "tracking_no": "SF1001",
}


async def add_order(session, order_id="1001", user_id="u1", **overrides):
    fields = {**ORDER_DEFAULTS, **overrides}
    order = Order(id=order_id, user_id=user_id, **fields)
    session.add(order)
    await session.commit()
    return order


async def add_logistics(session, order_id="1001", nodes=None, latest="运输中"):
    row = Logistics(
        tracking_no="SF1001",
        order_id=order_id,
        latest_status=latest,
        nodes=json.dumps(nodes, ensure_ascii=False) if nodes is not None else None,
        updated_at=CREATED,
    )
    session.add(row)
    await session.commit()
    return row


async def add_stock(session, sku_id="ST-20", spec="石墨灰", stock=10, price=129900, status="on_sale"):
    row = Stock(sku_id=sku_id, spec=spec, stock=stock, price=price, status=status)
    session.add(row)
    await session.commit()
    return row


# ── query_order ────────────────────────────────────────────


async def test_query_order_ok(session):
    await add_order(session)
    result = await tools.query_order("1001", user_id="u1", session=session)

    assert result.ok is True
    assert result.data["order_id"] == "1001"
    assert result.data["status"] == "已签收"
    assert result.data["amount"] == 129900
    assert result.data["tracking_no"] == "SF1001"


async def test_query_order_other_user_is_not_found(session):
    """越权与不存在返回同一个码，不给调用方区分的余地。"""
    await add_order(session, user_id="u2")
    result = await tools.query_order("1001", user_id="u1", session=session)

    assert result == tools.ToolResult(False, "not_found")


async def test_query_order_missing_is_not_found(session):
    result = await tools.query_order("9999", user_id="u1", session=session)
    assert result == tools.ToolResult(False, "not_found")


async def test_query_order_empty_id_is_invalid_arg(session):
    result = await tools.query_order("", user_id="u1", session=session)
    assert result == tools.ToolResult(False, "invalid_arg")


async def test_query_order_internal_error_is_wrapped(monkeypatch, session):
    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(tools, "get_order", _boom)
    result = await tools.query_order("1001", user_id="u1", session=session)

    assert result == tools.ToolResult(False, "internal")


# ── query_logistics ────────────────────────────────────────


async def test_query_logistics_ok(session):
    await add_order(session)
    nodes = [
        {"time": "2026-10-30 10:00", "status": "运输中", "desc": "已揽收"},
        {"time": "2026-10-31 08:00", "status": "派送中", "desc": "派送员已出发"},
    ]
    await add_logistics(session, nodes=nodes)

    result = await tools.query_logistics("SF1001", user_id="u1", session=session)

    assert result.ok is True
    assert result.data["latest_status"] == "运输中"
    assert result.data["nodes"] == nodes


async def test_query_logistics_bad_json_degrades_to_empty(session):
    await add_order(session)
    await add_logistics(session, nodes=None)
    # 手动写坏 JSON
    from sqlalchemy import text

    await session.execute(text("UPDATE logistics SET nodes = 'not-json'"))
    await session.commit()

    result = await tools.query_logistics("SF1001", user_id="u1", session=session)

    assert result.ok is True
    assert result.data["nodes"] == []


async def test_query_logistics_missing_is_not_found(session):
    await add_order(session)
    result = await tools.query_logistics("SF-NONE", user_id="u1", session=session)
    assert result == tools.ToolResult(False, "not_found")


async def test_query_logistics_empty_no_is_invalid_arg(session):
    result = await tools.query_logistics("", user_id="u1", session=session)
    assert result == tools.ToolResult(False, "invalid_arg")


# ── query_stock ────────────────────────────────────────────


async def test_query_stock_with_spec(session):
    await add_stock(session)
    result = await tools.query_stock("ST-20", "石墨灰", session=session)

    assert result.ok is True
    assert result.data == {
        "sku_id": "ST-20",
        "spec": "石墨灰",
        "stock": 10,
        "price": 129900,
        "status": "on_sale",
    }


async def test_query_stock_all_specs_sorted_by_price(session):
    await add_stock(session, spec="星光灰", price=2000)
    await add_stock(session, spec="石墨灰", price=1000)

    result = await tools.query_stock("ST-20", session=session)

    assert result.ok is True
    assert [r["spec"] for r in result.data["rows"]] == ["石墨灰", "星光灰"]


async def test_query_stock_missing_is_not_found(session):
    result = await tools.query_stock("ST-99", "任意", session=session)
    assert result == tools.ToolResult(False, "not_found")


async def test_query_stock_empty_sku_is_invalid_arg(session):
    result = await tools.query_stock("", session=session)
    assert result == tools.ToolResult(False, "invalid_arg")


# ── list_stock_skus ────────────────────────────────────────


async def test_list_stock_skus_is_sorted_and_unique(session):
    await add_stock(session, sku_id="ST-20", spec="石墨灰")
    await add_stock(session, sku_id="ST-20", spec="星光灰")
    await add_stock(session, sku_id="FB-10", spec="奶油白")

    result = await tools.list_stock_skus(session=session)

    assert result.ok is True
    assert result.data["skus"] == ["FB-10", "ST-20"]


async def test_list_stock_skus_empty_is_not_found(session):
    result = await tools.list_stock_skus(session=session)
    assert result == tools.ToolResult(False, "not_found")


# ── 错误码封闭集合 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "code", ["not_found", "invalid_arg", "timeout", "internal"]
)
def test_error_codes_are_the_closed_set(code):
    """错误码只有四个；图里的分支依赖这个集合。"""
    assert tools.ToolResult(False, code).code == code
