"""Task 7：`validate` —— 三个守卫，每条一正一反。

| 守卫 | 拦什么 | 拦下后 |
|---|---|---|
| 要素齐全 | 缺订单号 / 缺原因 | 追问（用户能补） |
| 订单属于本人 | 不存在或越权（共用 `NotFound`） | 追问，不泄露"这单存在" |
| 日期不矛盾 | 签收早于发货 | **直接转人工**（用户改不了） |

`condition_ok` 缺失**不在这里拦**：它是判定材料，不是申请门槛。
"""
from datetime import datetime, timedelta

import pytest

from app.db.errors import NotFound
from app.db.models import Order
from app.graph import rules_exit
from app.graph.rules_exit import after_validate, need_info, order_snapshot, validate

CREATED = datetime(2026, 10, 29, 10, 0)


def make_order(**overrides) -> Order:
    defaults = {
        "id": "1002",
        "user_id": "u1",
        "sku_id": "ST-20",
        "spec": "石墨灰",
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


def valid_state(**overrides) -> dict:
    state = {
        "user_id": "u1",
        "order_id": "1002",
        "refund_draft": {"reason_code": "no_reason"},
    }
    state.update(overrides)
    return state


# ── 守卫 1：要素齐全 ────────────────────────────────────────


async def test_valid_input_passes(monkeypatch):
    mock_order(monkeypatch, make_order())
    result = await validate(valid_state())
    assert result["validate_errors"] == []
    assert "order_snapshot" in result


async def test_missing_order_id(monkeypatch):
    calls = mock_order(monkeypatch, make_order())
    result = await validate(valid_state(order_id=None))
    assert result["validate_errors"] == ["missing_order_id"]
    assert "order_snapshot" not in result
    assert calls == [], "缺订单号时不该打库"


async def test_missing_reason(monkeypatch):
    mock_order(monkeypatch, make_order())
    result = await validate(valid_state(refund_draft={}))
    assert result["validate_errors"] == ["missing_reason_code"]


async def test_both_missing(monkeypatch):
    mock_order(monkeypatch, make_order())
    result = await validate(valid_state(order_id=None, refund_draft={}))
    assert set(result["validate_errors"]) == {"missing_order_id", "missing_reason_code"}


# ── 守卫 2：订单属于本人 ────────────────────────────────────


async def test_order_not_found_or_not_owned(monkeypatch):
    """不存在与越权共用 `NotFound`——不能泄露"这单确实存在"。"""
    mock_order(monkeypatch, error=NotFound("order not found"))
    result = await validate(valid_state())
    assert result["validate_errors"] == ["order_not_found"]
    assert "order_snapshot" not in result


async def test_passes_user_id_to_repo(monkeypatch):
    calls = mock_order(monkeypatch, make_order())
    await validate(valid_state(user_id="u7"))
    assert calls == [("1002", "u7")]


# ── 守卫 3：日期不矛盾 ──────────────────────────────────────


async def test_signed_before_shipped_is_anomaly(monkeypatch):
    order = make_order(
        shipped_at=CREATED + timedelta(days=5),
        signed_at=CREATED + timedelta(days=3),
    )
    mock_order(monkeypatch, order)
    result = await validate(valid_state())

    assert result["validate_errors"] == ["date_anomaly"]
    assert result["transfer_reason"] == "date_anomaly"
    assert result["actions"] == [{"type": "transfer_human"}]


async def test_not_signed_yet_is_not_anomaly(monkeypatch):
    """未签收是"还没有"，不是"有但错了"。"""
    mock_order(monkeypatch, make_order(status="已发货", signed_at=None))
    result = await validate(valid_state())
    assert result["validate_errors"] == []


async def test_same_instant_is_not_anomaly(monkeypatch):
    same = CREATED + timedelta(days=1)
    mock_order(monkeypatch, make_order(shipped_at=same, signed_at=same))
    result = await validate(valid_state())
    assert result["validate_errors"] == []


# ── 追问次数用尽 ────────────────────────────────────────────


async def test_retry_exhausted_transfers_to_human(monkeypatch):
    mock_order(monkeypatch, make_order())
    result = await validate(
        valid_state(refund_draft={}, validate_retry=2)
    )
    assert result["validate_errors"] == ["missing_reason_code"]
    assert result["transfer_reason"] == "validate_retry_exhausted"


# ── 快照 ────────────────────────────────────────────────────


def test_snapshot_is_plain_dict_with_iso_times():
    snap = order_snapshot(make_order())
    assert snap["order_id"] == "1002"
    assert snap["amount"] == 129900
    assert snap["quantity"] == 1
    assert snap["shipped_at"] == "2026-10-30T10:00:00"
    assert snap["signed_at"] == "2026-11-01T10:00:00"
    # 未发生的环节是 None，不是空字符串
    assert order_snapshot(make_order(signed_at=None))["signed_at"] is None


# ── need_info 按错误码追问 ─────────────────────────────────


async def test_need_info_re_asks_order_when_not_found(monkeypatch):
    async def _list(*, user_id: str):
        return [make_order()]

    monkeypatch.setattr(rules_exit, "list_user_orders", _list)
    result = await need_info(
        {
            "user_id": "u1",
            "order_id": "9999",
            "validate_errors": ["order_not_found"],
            "refund_draft": {"reason_code": "quality"},
        }
    )
    assert result["pending_question"] == "order_id"
    assert result["validate_retry"] == 1


async def test_need_info_asks_reason_when_missing():
    result = await need_info(
        {
            "user_id": "u1",
            "order_id": "1002",
            "validate_errors": ["missing_reason_code"],
            "refund_draft": {},
        }
    )
    assert result["pending_question"] == "reason_code"
    assert result["validate_retry"] == 1


async def test_need_info_increments_retry():
    result = await need_info(
        {
            "user_id": "u1",
            "order_id": "1002",
            "validate_errors": ["missing_reason_code"],
            "refund_draft": {},
            "validate_retry": 1,
        }
    )
    assert result["validate_retry"] == 2


# ── 条件边 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"validate_errors": []}, "query_data"),
        ({"validate_errors": ["missing_reason_code"]}, "need_info"),
        (
            {"validate_errors": ["missing_reason_code"], "validate_retry": 1},
            "need_info",
        ),
        (
            {"validate_errors": ["missing_reason_code"], "validate_retry": 2},
            "human",
        ),
        # 用户改不了的矛盾：第 0 轮就转人工，不给"再问一次"的机会
        ({"validate_errors": ["date_anomaly"]}, "human"),
    ],
)
def test_after_validate_routing(state, expected):
    assert after_validate(state) == expected
