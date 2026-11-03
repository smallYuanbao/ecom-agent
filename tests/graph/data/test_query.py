"""Task 6：查询链 + 结果校验。

重点不是"查得到"，是**每类失败走哪条路**：

| 工具结果 | 去向 |
|---|---|
| `ok` 且字段完整 | `format` |
| `not_found` / `invalid_arg` | `need_info`（用户还能补） |
| `timeout` / `internal` / 字段缺失 | 转人工 |
"""
import pytest

from app.graph import data_exit
from app.graph.data_exit import match_spec, query
from app.tools.orders import ToolResult

ORDER = {
    "order_id": "1001",
    "status": "已签收",
    "amount": 129900,
    "quantity": 1,
    "created_at": "2026-10-29T10:00:00",
    "tracking_no": "SF1001",
}
TRACKING = {
    "tracking_no": "SF1001",
    "latest_status": "运输中",
    "nodes": [{"time": "2026-10-30 10:00", "status": "运输中", "desc": "已揽收"}],
}
STOCK_ROWS = {
    "sku_id": "ST-20",
    "rows": [
        {"sku_id": "ST-20", "spec": "石墨灰", "stock": 10, "price": 129900, "status": "on_sale"},
        {"sku_id": "ST-20", "spec": "星光灰", "stock": 0, "price": 139900, "status": "sold_out"},
    ],
}


def patch_order(monkeypatch, result: ToolResult):
    async def _call(order_id, *, user_id, session=None):
        return result

    monkeypatch.setattr(data_exit, "query_order", _call)


def patch_tracking(monkeypatch, result: ToolResult):
    async def _call(tracking_no, *, user_id, session=None):
        return result

    monkeypatch.setattr(data_exit, "query_logistics", _call)


def patch_stock(monkeypatch, result: ToolResult):
    async def _call(sku_id, spec=None, *, session=None):
        return result

    monkeypatch.setattr(data_exit, "query_stock", _call)


def state(**overrides) -> dict:
    base = {"intent": "订单查询", "user_id": "u1", "raw_query": "订单1001"}
    base.update(overrides)
    return base


# ── match_spec ─────────────────────────────────────────────


def test_match_spec_prefers_longest_hit():
    rows = [{"spec": "灰"}, {"spec": "星光灰"}]
    assert match_spec("我要星光灰", rows) == "星光灰"


def test_match_spec_single_row_falls_back():
    assert match_spec("随便看看", [{"spec": "石墨灰"}]) == "石墨灰"


def test_match_spec_ambiguous_returns_none():
    rows = [{"spec": "石墨灰"}, {"spec": "星光灰"}]
    assert match_spec("还有货吗", rows) is None


# ── 订单 ───────────────────────────────────────────────────


async def test_order_ok(monkeypatch):
    patch_order(monkeypatch, ToolResult(True, None, ORDER))
    result = await query(state(order_id="1001"))

    assert result == {"tool_code": "ok", "data_result": ORDER}


async def test_order_not_found_asks_again(monkeypatch):
    patch_order(monkeypatch, ToolResult(False, "not_found"))
    result = await query(state(order_id="9999"))

    assert result == {"tool_code": "not_found", "pending_question": "order_id"}


async def test_order_timeout_transfers(monkeypatch):
    patch_order(monkeypatch, ToolResult(False, "timeout"))
    result = await query(state(order_id="1001"))

    assert result["transfer_reason"] == "tool_timeout"
    assert "tool_code" not in result


async def test_order_incomplete_transfers(monkeypatch):
    incomplete = {k: v for k, v in ORDER.items() if k != "status"}
    patch_order(monkeypatch, ToolResult(True, None, incomplete))
    result = await query(state(order_id="1001"))

    assert result["transfer_reason"] == "data_incomplete"


# ── 物流 ───────────────────────────────────────────────────


async def test_logistics_two_step_chain(monkeypatch):
    patch_order(monkeypatch, ToolResult(True, None, ORDER))
    patch_tracking(monkeypatch, ToolResult(True, None, TRACKING))

    result = await query(state(intent="物流查询", order_id="1001"))

    assert result["tool_code"] == "ok"
    assert result["data_result"]["tracking"] == TRACKING


async def test_logistics_without_tracking_is_ok(monkeypatch):
    no_tracking = {**ORDER, "tracking_no": None}
    patch_order(monkeypatch, ToolResult(True, None, no_tracking))

    result = await query(state(intent="物流查询", order_id="1001"))

    assert result["tool_code"] == "ok"
    assert result["data_result"]["tracking"] is None


async def test_logistics_order_not_found_asks_again(monkeypatch):
    patch_order(monkeypatch, ToolResult(False, "not_found"))
    result = await query(state(intent="物流查询", order_id="9999"))

    assert result == {"tool_code": "not_found", "pending_question": "order_id"}


# ── 库存 ───────────────────────────────────────────────────


async def test_stock_matches_spec_from_query(monkeypatch):
    patch_stock(monkeypatch, ToolResult(True, None, STOCK_ROWS))

    result = await query(
        state(intent="库存价格", sku_id="ST-20", raw_query="ST-20 石墨灰还有货吗")
    )

    assert result["tool_code"] == "ok"
    assert result["data_result"]["spec"] == "石墨灰"


async def test_stock_ambiguous_spec_asks(monkeypatch):
    patch_stock(monkeypatch, ToolResult(True, None, STOCK_ROWS))

    result = await query(state(intent="库存价格", sku_id="ST-20", raw_query="ST-20 还有货吗"))

    assert result == {"tool_code": "not_found", "pending_question": "spec"}


async def test_stock_single_spec_auto_selected(monkeypatch):
    single = {"sku_id": "ST-20", "rows": [STOCK_ROWS["rows"][0]]}
    patch_stock(monkeypatch, ToolResult(True, None, single))

    result = await query(state(intent="库存价格", sku_id="ST-20", raw_query="还有货吗"))

    assert result["tool_code"] == "ok"
    assert result["data_result"]["spec"] == "石墨灰"


async def test_stock_not_found_asks_for_sku(monkeypatch):
    patch_stock(monkeypatch, ToolResult(False, "not_found"))

    result = await query(state(intent="库存价格", sku_id="ST-99"))

    assert result == {"tool_code": "not_found", "pending_question": "sku_id"}


# ── 兜底 ───────────────────────────────────────────────────


async def test_unsupported_intent_transfers():
    result = await query(state(intent="退款申请"))
    assert result["transfer_reason"] == "unsupported_intent"


@pytest.mark.parametrize("code", ["timeout", "internal"])
async def test_other_codes_transfer(monkeypatch, code):
    patch_order(monkeypatch, ToolResult(False, code))
    result = await query(state(order_id="1001"))
    assert result["transfer_reason"] == f"tool_{code}"
