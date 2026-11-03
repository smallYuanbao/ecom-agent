"""Task 5：`intake` 抽订单号 + `need_info` 列候选。

这三条链路各自对应一类真实输入：

| 输入 | 期望 |
|---|---|
| 「订单 1001 我要退款」 | `intake` 抽出 1001，直接往下走 |
| 「我要退款」 | 抽不到 → `need_info` 列最近 3 单让用户点 |
| 用户点了「1002」 | 下一轮 `intake` 抽到 1002，流程接着走 |

**为什么订单号用正则而不是模型**：纯数字，规则可枚举，省一次 LLM 调用。
模型只做它擅长的（抽原因、判资格、组织语言）。
"""
from datetime import datetime

import pytest

from app.db.models import Order
from app.graph import order_select, rules_exit
from app.graph.params import extract_order_id
from app.graph.rules_exit import intake, need_info

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


@pytest.fixture
def orders(monkeypatch):
    """默认给四单，用来验证"只列最近 3 单"。"""
    rows = [
        fake_order("1004"),
        fake_order("1003"),
        fake_order("1002"),
        fake_order("1001"),
    ]

    async def _list(*, user_id: str):
        assert user_id == "u1"
        return rows

    monkeypatch.setattr(order_select, "list_user_orders", _list)
    return rows


# ── 正则 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("订单 1001 我要退款", "1001"),
        ("1001", "1001"),
        ("我的单号是1002，想退", "1002"),
        ("abc1003xyz", "1003"),
        ("12345678", "12345678"),
        ("我要退款", None),
        ("123", None),          # 不足 4 位
        ("", None),
        (None, None),
    ],
)
def test_extract_order_id(text, expected):
    assert extract_order_id(text) == expected


def test_does_not_slice_a_longer_number():
    """`(?<!\\d)` / `(?!\\d)` 的作用：不能从长数字里抠出前 4 位。"""
    assert extract_order_id("123456") == "123456"


# ── intake ──────────────────────────────────────────────────


async def test_intake_writes_order_id():
    result = await intake({"raw_query": "订单1001我要退款"})
    assert result["order_id"] == "1001"


async def test_intake_clears_pending_question():
    """用户已经补充了订单号，上一轮的追问标记要清掉。"""
    result = await intake(
        {"raw_query": "1002", "pending_question": "order_id"}
    )
    assert result["pending_question"] is None


async def test_intake_without_digits_writes_nothing():
    assert await intake({"raw_query": "我要退款"}) == {}


async def test_intake_prefers_resolved_query():
    """指代消解后的句子优先——`raw_query` 只用于审计与回放。"""
    result = await intake({"raw_query": "那单", "resolved_query": "订单1003 那单"})
    assert result["order_id"] == "1003"


# ── need_info：列候选 ──────────────────────────────────────


async def test_lists_at_most_three_candidates(orders):
    result = await need_info({"user_id": "u1", "raw_query": "我要退款"})

    assert result["pending_question"] == "order_id"
    assert [c["order_id"] for c in result["order_candidates"]] == ["1004", "1003", "1002"]
    assert result["actions"][0]["type"] == "select_order"
    assert len(result["actions"][0]["candidates"]) == 3


async def test_candidate_shape(orders):
    result = await need_info({"user_id": "u1", "raw_query": "我要退款"})
    candidate = result["order_candidates"][0]

    assert candidate == {
        "order_id": "1004",
        "title": "ST-20 石墨灰",
        "status": "已签收",
        "amount": 129900,
        "amount_text": "1299.00 元",
        "created_at": "2026-10-29",
    }


async def test_without_user_id_falls_back_to_text_question(orders):
    """拿不到身份就不查库（也不能查——`user_id` 是必填参数）。"""
    result = await need_info({"raw_query": "我要退款"})

    assert result["pending_question"] == "order_id"
    assert "order_candidates" not in result
    assert "actions" not in result


async def test_user_without_orders_gets_text_question(monkeypatch):
    async def _empty(*, user_id: str):
        return []

    monkeypatch.setattr(order_select, "list_user_orders", _empty)
    result = await need_info({"user_id": "u9", "raw_query": "我要退款"})

    assert result["pending_question"] == "order_id"
    assert "order_candidates" not in result


# ── need_info：要原因 ──────────────────────────────────────


async def test_asks_for_reason_when_order_is_known(orders):
    result = await need_info(
        {"user_id": "u1", "order_id": "1002", "raw_query": "退货"}
    )
    assert result["pending_question"] == "reason_code"


async def test_returns_nothing_when_all_fields_are_present(orders):
    """什么都不缺 = 放行回 validate（守卫看到 pending_question 为空就会这么做）。"""
    result = await need_info(
        {
            "user_id": "u1",
            "order_id": "1002",
            "refund_draft": {"reason_code": "quality"},
        }
    )
    assert result == {}


# ── resume 回填 ────────────────────────────────────────────


async def test_resume_after_selection(orders):
    """第一轮列候选，第二轮用户点「1002」，订单号被回填。

    这就是"跨轮恢复"的实际形态：图不保存状态，靠上一轮的追问 +
    这一轮的用户输入重新走一遍。
    """
    first = await rules_exit.build_rules_graph().ainvoke(
        {"user_id": "u1", "raw_query": "我要退款"}
    )
    assert first["pending_question"] == "order_id"
    assert {c["order_id"] for c in first["order_candidates"]} == {"1004", "1003", "1002"}

    second = await rules_exit.build_rules_graph().ainvoke(
        {"user_id": "u1", "raw_query": "1002"}
    )
    assert second["order_id"] == "1002"
