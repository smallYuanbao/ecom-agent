"""Task 4：抽参数 + 按意图校验。

两个节点都**不查库**：只做字面提取和"缺什么"的判断。
存在性 / 归属 / 规格匹配在 Task 6 的 query 里。
"""
import pytest

from app.graph import data_exit
from app.graph.data_exit import build_data_graph, extract_params, validate
from app.tools.orders import ToolResult

# ── extract_params ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("订单 1001 到哪了", {"order_id": "1001"}),
        ("ST-20 还有货吗", {"sku_id": "ST-20"}),
        ("订单1001 的 ST-20 还有货吗", {"order_id": "1001", "sku_id": "ST-20"}),
        ("帮我查一下", {}),
        ("", {}),
    ],
)
async def test_extract_params(query, expected):
    assert await extract_params({"raw_query": query}) == expected


async def test_extract_params_prefers_resolved_query():
    result = await extract_params({"raw_query": "那单", "resolved_query": "订单1003 那单"})
    assert result == {"order_id": "1003"}


# ── validate ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"intent": "订单查询"}, {"pending_question": "order_id"}),
        ({"intent": "物流查询"}, {"pending_question": "order_id"}),
        ({"intent": "订单查询", "order_id": "1001"}, {"pending_question": None}),
        ({"intent": "物流查询", "order_id": "1001"}, {"pending_question": None}),
        ({"intent": "库存价格"}, {"pending_question": "sku_id"}),
        ({"intent": "库存价格", "sku_id": "ST-20"}, {"pending_question": None}),
        # 不在 B 出口的意图：不主动追问，交给 query 的默认分支转人工
        ({"intent": "退款申请"}, {"pending_question": None}),
    ],
)
async def test_validate(state, expected):
    assert await validate(state) == expected


# ── 图上的效果 ─────────────────────────────────────────────


async def test_graph_asks_for_order_id_when_missing():
    result = await build_data_graph().ainvoke(
        {"intent": "订单查询", "raw_query": "帮我查一下"}
    )
    assert result["pending_question"] == "order_id"


async def test_graph_asks_for_sku_when_missing(monkeypatch):
    async def _skus(**kwargs):
        return ToolResult(True, None, {"skus": ["ST-20"]})

    monkeypatch.setattr(data_exit, "list_stock_skus", _skus)

    result = await build_data_graph().ainvoke(
        {"intent": "库存价格", "raw_query": "推车还有货吗"}
    )
    assert result["pending_question"] == "sku_id"


async def test_graph_clears_pending_question_when_complete(monkeypatch):
    async def _order(order_id, *, user_id, session=None):
        return ToolResult(
            True,
            None,
            {
                "order_id": "1001",
                "status": "已签收",
                "amount": 129900,
                "created_at": "2026-10-29T10:00:00",
                "tracking_no": None,
            },
        )

    monkeypatch.setattr(data_exit, "query_order", _order)

    result = await build_data_graph().ainvoke(
        {"intent": "订单查询", "raw_query": "订单1001 到哪了", "user_id": "u1"}
    )
    assert result.get("pending_question") is None
