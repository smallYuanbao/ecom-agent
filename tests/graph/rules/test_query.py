"""Task 8：`query_data` —— 订单快照 + 退款政策。

两个失败分支的语义完全不同，这是本节的重点：

| 失败 | 谁的错 | 去向 |
|---|---|---|
| 订单查不到 / 越权 | 用户（可能报错单号） | `need_info` 追问 |
| 政策检索为空 / 检索失败 | 系统（知识库缺内容或向量库挂了） | **转人工** |

把系统问题当成用户问题去追问，用户永远答不上来。
"""
from datetime import datetime, timedelta

import pytest

from app.db.errors import NotFound
from app.db.models import Order
from app.graph import rules_exit
from app.graph.rules_exit import after_query_data, query_data

CREATED = datetime(2026, 10, 29, 10, 0)


def make_order(**overrides) -> Order:
    defaults = {
        "id": "1002",
        "user_id": "u1",
        "sku_id": "ST-20",
        "spec": "石墨灰",
        "category": "婴儿推车",
        "status": "已签收",
        "amount": 129900,
        "quantity": 1,
        "created_at": CREATED,
        "paid_at": CREATED,
        "shipped_at": CREATED + timedelta(days=1),
        "signed_at": CREATED + timedelta(days=3),
        "tracking_no": "SF1001002",
    }
    defaults.update(overrides)
    return Order(**defaults)


def mock_order(monkeypatch, order: Order | None = None, error: Exception | None = None):
    calls: list[tuple[str, str]] = []

    async def _get(order_id: str, *, user_id: str):
        calls.append((order_id, user_id))
        if error is not None:
            raise error
        return order

    monkeypatch.setattr(rules_exit, "get_order", _get)
    return calls


def mock_search(monkeypatch, hits: list[dict] | None = None, error: Exception | None = None):
    calls: list[tuple[str, int]] = []

    async def _search(query: str, *, top_k: int = 5, strategy=None):
        calls.append((query, top_k))
        if error is not None:
            raise error
        return hits or []

    monkeypatch.setattr(rules_exit, "search", _search)
    return calls


HITS = [{"id": "returns#退款时效", "text": "自签收次日起 7 日内", "similarity": 0.8}]


def base_state(**overrides) -> dict:
    state = {
        "user_id": "u1",
        "order_id": "1002",
        "order_snapshot": {
            "order_id": "1002",
            "status": "已签收",
            "category": "婴儿推车",
        },
        "refund_draft": {"reason_code": "quality"},
    }
    state.update(overrides)
    return state


# ── 订单：复用快照 ─────────────────────────────────────────


async def test_reuses_snapshot_from_validate(monkeypatch):
    """validate 已经查过库，这里再查一次就是浪费。"""
    order_calls = mock_order(monkeypatch, make_order())
    mock_search(monkeypatch, HITS)

    result = await query_data(base_state())

    assert order_calls == []
    assert result["evidence"] == HITS


async def test_loads_order_when_snapshot_missing(monkeypatch):
    order_calls = mock_order(monkeypatch, make_order())
    mock_search(monkeypatch, HITS)

    result = await query_data(base_state(order_snapshot=None))

    assert order_calls == [("1002", "u1")]
    assert result["order_snapshot"]["category"] == "婴儿推车"


async def test_not_found_returns_nothing(monkeypatch):
    """不在本节点写错误码：`after_query_data` 看快照为空就会送去追问。"""
    mock_order(monkeypatch, error=NotFound("order not found"))
    mock_search(monkeypatch, HITS)

    result = await query_data(base_state(order_snapshot=None))

    assert result == {}
    assert after_query_data({**base_state(order_snapshot=None), **result}) == "need_info"


async def test_missing_order_id_returns_nothing(monkeypatch):
    mock_order(monkeypatch, make_order())
    mock_search(monkeypatch, HITS)

    assert await query_data(base_state(order_snapshot=None, order_id=None)) == {}


# ── 政策：查询串 ───────────────────────────────────────────


async def test_query_uses_chinese_reason_and_status(monkeypatch):
    """`reason_code` 是英文枚举，直接拼进中文查询等于噪声。"""
    mock_order(monkeypatch)
    calls = mock_search(monkeypatch, HITS)

    await query_data(base_state(refund_draft={"reason_code": "quality"}))

    assert calls == [("退款 质量问题 已签收", 5)]


async def test_query_falls_back_when_reason_missing(monkeypatch):
    mock_order(monkeypatch)
    calls = mock_search(monkeypatch, HITS)

    await query_data(base_state(refund_draft={}))

    assert calls[0][0] == "退款 退款 已签收"


# ── 政策：两个失败分支 ─────────────────────────────────────


async def test_empty_policy_transfers_to_human(monkeypatch):
    """知识库缺内容是系统问题，追问用户没有意义。"""
    mock_order(monkeypatch)
    mock_search(monkeypatch, hits=[])

    result = await query_data(base_state())

    assert result["evidence"] == []
    assert result["transfer_reason"] == "policy_retrieval_empty"
    assert result["actions"] == [{"type": "transfer_human"}]
    assert after_query_data({**base_state(), **result}) == "human"


async def test_search_failure_transfers_instead_of_crashing(monkeypatch):
    """向量库挂掉不该让整轮 500——转人工是可执行的结果。"""
    mock_order(monkeypatch)
    mock_search(monkeypatch, error=ConnectionError("chroma down"))

    result = await query_data(base_state())

    assert result["evidence"] == []
    assert result["transfer_reason"] == "policy_retrieval_empty"


# ── 条件边 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"order_snapshot": {"order_id": "1"}, "evidence": [{"id": "a"}]}, "judge"),
        ({"order_snapshot": None, "evidence": [{"id": "a"}]}, "need_info"),
        ({"order_snapshot": {"order_id": "1"}, "evidence": []}, "human"),
    ],
)
def test_after_query_data_routing(state, expected):
    assert after_query_data(state) == expected
