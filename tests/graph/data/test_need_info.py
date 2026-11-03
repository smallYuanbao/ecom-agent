"""Task 5：缺参数时列候选。

三种缺法对应三种选择器：

| pending_question | 候选字段 | action |
|---|---|---|
| `order_id` | `order_candidates` | `select_order` |
| `sku_id` | `sku_candidates` | `select_sku` |
| `spec` | `spec_candidates` | `select_spec` |
"""
from datetime import datetime

from app.db.models import Order
from app.graph import data_exit, order_select
from app.graph.data_exit import build_data_graph, need_info
from app.tools.orders import ToolResult

CREATED = datetime(2026, 10, 29, 10, 0)


def fake_order(order_id: str) -> Order:
    return Order(
        id=order_id,
        user_id="u1",
        sku_id="ST-20",
        spec="石墨灰",
        status="已签收",
        amount=129900,
        quantity=1,
        created_at=CREATED,
    )


# ── 缺订单号 ───────────────────────────────────────────────


async def test_order_candidates(monkeypatch):
    async def _list(*, user_id: str):
        return [fake_order("1003"), fake_order("1002"), fake_order("1001"), fake_order("1000")]

    monkeypatch.setattr(order_select, "list_user_orders", _list)

    result = await need_info({"pending_question": "order_id", "user_id": "u1"})

    assert [c["order_id"] for c in result["order_candidates"]] == ["1003", "1002", "1001"]
    assert result["actions"][0]["type"] == "select_order"


# ── 缺型号 ─────────────────────────────────────────────────


async def test_sku_candidates(monkeypatch):
    async def _skus(**kwargs):
        return ToolResult(True, None, {"skus": ["FB-10", "ST-20"]})

    monkeypatch.setattr(data_exit, "list_stock_skus", _skus)

    result = await need_info({"pending_question": "sku_id"})

    assert result["sku_candidates"] == [{"sku_id": "FB-10"}, {"sku_id": "ST-20"}]
    assert result["actions"][0]["type"] == "select_sku"


async def test_sku_falls_back_to_text(monkeypatch):
    async def _empty(**kwargs):
        return ToolResult(False, "not_found")

    monkeypatch.setattr(data_exit, "list_stock_skus", _empty)

    result = await need_info({"pending_question": "sku_id"})

    assert result["pending_question"] == "sku_id"
    assert "sku_candidates" not in result
    assert "actions" not in result


# ── 缺规格 ─────────────────────────────────────────────────


async def test_spec_candidates(monkeypatch):
    async def _stock(sku_id: str, **kwargs):
        return ToolResult(
            True,
            None,
            {
                "sku_id": sku_id,
                "rows": [
                    {"sku_id": "ST-20", "spec": "石墨灰", "stock": 10, "price": 129900, "status": "on_sale"},
                    {"sku_id": "ST-20", "spec": "星光灰", "stock": 0, "price": 139900, "status": "sold_out"},
                ],
            },
        )

    monkeypatch.setattr(data_exit, "query_stock", _stock)

    result = await need_info({"pending_question": "spec", "sku_id": "ST-20"})

    assert [c["spec"] for c in result["spec_candidates"]] == ["石墨灰", "星光灰"]
    assert result["actions"][0]["type"] == "select_spec"


async def test_spec_falls_back_to_text(monkeypatch):
    async def _missing(sku_id: str, **kwargs):
        return ToolResult(False, "not_found")

    monkeypatch.setattr(data_exit, "query_stock", _missing)

    result = await need_info({"pending_question": "spec", "sku_id": "ST-99"})

    assert result["pending_question"] == "spec"
    assert "spec_candidates" not in result


# ── 其他 ───────────────────────────────────────────────────


async def test_unknown_pending_returns_empty():
    assert await need_info({"pending_question": "something_else"}) == {}


async def test_graph_asks_for_sku_with_candidates(monkeypatch):
    async def _skus(**kwargs):
        return ToolResult(True, None, {"skus": ["FB-10", "ST-20"]})

    monkeypatch.setattr(data_exit, "list_stock_skus", _skus)

    result = await build_data_graph().ainvoke(
        {"intent": "库存价格", "raw_query": "推车还有货吗"}
    )

    assert result["pending_question"] == "sku_id"
    assert result["sku_candidates"] == [{"sku_id": "FB-10"}, {"sku_id": "ST-20"}]
