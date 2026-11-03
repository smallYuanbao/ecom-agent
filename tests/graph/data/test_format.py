"""Task 7：模板化回答。

模板里的数字必须逐字来自 `data_result`：
金额用 `yuan_text`，库存用状态映射，**不做二次计算**。
"""
import pytest

from app.graph.data_exit import format as format_answer

ORDER = {
    "order_id": "1001",
    "status": "已签收",
    "amount": 129900,
    "created_at": "2026-10-29T10:00:00",
    "tracking_no": "SF1001",
}

TRACKING = {
    "tracking_no": "SF1001",
    "latest_status": "运输中",
    "nodes": [
        {"time": "10-26 09:12", "status": "已揽收", "desc": "杭州转运中心已收件"},
        {"time": "10-27 18:40", "status": "运输中", "desc": "已到达上海分拨中心"},
    ],
}

STOCK = {
    "sku_id": "ST-20",
    "spec": "石墨灰",
    "stock": 10,
    "price": 129900,
    "status": "on_sale",
}


def state(intent: str, **data) -> dict:
    return {"intent": intent, "data_result": data}


# ── 订单 ───────────────────────────────────────────────────


async def test_order_template():
    result = await format_answer(state("订单查询", **{**ORDER, "tracking_no": None}))

    assert result["answer"] == (
        "订单 1001：状态 已签收；实付 1299.00 元；下单时间 2026-10-29 10:00。"
    )
    assert result["is_placeholder"] is False


async def test_order_with_tracking_appends_number():
    result = await format_answer(state("订单查询", **ORDER))

    assert result["answer"].endswith("物流单号 SF1001。")


# ── 物流 ───────────────────────────────────────────────────


async def test_logistics_template():
    result = await format_answer(
        state("物流查询", order=ORDER, tracking=TRACKING)
    )

    assert result["answer"] == (
        "快递 SF1001：最新状态 运输中。"
        "最近轨迹：10-27 18:40 运输中 已到达上海分拨中心；"
        "10-26 09:12 已揽收 杭州转运中心已收件。"
    )


async def test_logistics_without_tracking():
    result = await format_answer(
        state(
            "物流查询",
            order={"order_id": "1002", "status": "待发货"},
            tracking=None,
        )
    )

    assert result["answer"] == "订单 1002 当前状态：待发货，暂无物流信息。"


async def test_logistics_without_nodes_skips_trajectory():
    tracking = {"tracking_no": "SF1001", "latest_status": "运输中", "nodes": []}
    result = await format_answer(state("物流查询", order=ORDER, tracking=tracking))

    assert result["answer"] == "快递 SF1001：最新状态 运输中。"
    assert "最近轨迹" not in result["answer"]


async def test_logistics_keeps_only_latest_three_nodes_newest_first():
    nodes = [
        {"time": f"10-0{i}", "status": f"s{i}", "desc": f"d{i}"}
        for i in range(1, 6)
    ]
    tracking = {"tracking_no": "SF1001", "latest_status": "运输中", "nodes": nodes}

    result = await format_answer(state("物流查询", order=ORDER, tracking=tracking))

    assert "d5" in result["answer"] and "d4" in result["answer"] and "d3" in result["answer"]
    assert "d2" not in result["answer"] and "d1" not in result["answer"]
    assert result["answer"].index("d5") < result["answer"].index("d4") < result["answer"].index("d3")


# ── 库存 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stock", "status", "expected"),
    [
        (10, "on_sale", "有货（10 件）"),
        (0, "on_sale", "暂时无货"),
        (0, "sold_out", "已售罄"),
        (0, "presale", "预售中"),
    ],
)
async def test_stock_status_text(stock: int, status: str, expected: str):
    result = await format_answer(
        state("库存价格", **{**STOCK, "stock": stock, "status": status})
    )
    assert result["answer"] == f"ST-20 石墨灰：{expected}，售价 1299.00 元。"


# ── 兜底 ───────────────────────────────────────────────────


async def test_unsupported_intent_transfers():
    result = await format_answer(state("退款申请"))
    assert result["transfer_reason"] == "unsupported_intent"
