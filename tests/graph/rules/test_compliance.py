"""Task 10：`compute` 算钱 + `compliance` 红线与独立复算。

compliance 有两件事，测试也分两组：

| 组 | 测什么 | 触发条件 |
|---|---|---|
| 红线 | 无论模型怎么判，这几条都不能放行 | 金额越界 / 不适用商品被放行 / 超期被放行 |
| 复算 | 模型与规则代码不一致 | 两边结论或理由码不同 |

**分歧时不选边，转人工**——以代码为准模型就白用了，采信模型又失去可审计性。
"""
from datetime import datetime

import pytest

from app.graph import rules_exit
from app.graph.rules_exit import after_compliance, compliance, compute

NOW = datetime(2026, 11, 5, 10, 0)


def snapshot(**overrides) -> dict:
    base = {
        "order_id": "1001",
        "status": "已签收",
        "amount": 269900,
        "quantity": 1,
        "category": "婴儿推车",
        "created_at": "2026-10-29T10:00:00",
        "paid_at": "2026-10-29T10:15:00",
        "shipped_at": "2026-10-30T10:00:00",
        "signed_at": "2026-11-02T10:00:00",
    }
    base.update(overrides)
    return base


def judgement(**overrides) -> dict:
    base = {"eligible": True, "reason_code": "ok", "confidence": 0.9}
    base.update(overrides)
    return base


def draft(**overrides) -> dict:
    base = {"reason_code": "no_reason", "item_count": 1, "condition_ok": True}
    base.update(overrides)
    return base


def state(**overrides) -> dict:
    base = {
        "trace_id": "t-test",
        "order_snapshot": snapshot(),
        "refund_judgement": judgement(),
        "refund_draft": draft(),
        "refund_amount": 269900,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """固定时钟 + 记录 span，避免测试依赖真实时间、也避免写库。"""
    spans: list[dict] = []

    monkeypatch.setattr(rules_exit, "utcnow", lambda: NOW)

    async def _span(**kwargs):
        spans.append(kwargs)

    monkeypatch.setattr(rules_exit, "record_span", _span)
    return spans


# ── compute ────────────────────────────────────────────────


async def test_compute_full_refund():
    assert (await compute(state()))["refund_amount"] == 269900


async def test_compute_partial_refund():
    result = await compute(
        state(order_snapshot=snapshot(quantity=2), refund_draft=draft(item_count=1))
    )
    assert result["refund_amount"] == 134950


async def test_compute_not_eligible_is_zero():
    result = await compute(
        state(refund_judgement=judgement(eligible=False, reason_code="not_intact"))
    )
    assert result["refund_amount"] == 0


async def test_compute_pending_shipment_is_full():
    result = await compute(state(order_snapshot=snapshot(status="待发货", quantity=2)))
    assert result["refund_amount"] == 269900


async def test_compute_reject_then_refund_is_full():
    result = await compute(
        state(
            order_snapshot=snapshot(status="已发货", signed_at=None, quantity=2),
            refund_judgement=judgement(reason_code="reject_then_refund"),
        )
    )
    assert result["refund_amount"] == 269900


# ── 红线 ────────────────────────────────────────────────────


async def test_red_line_amount_exceeds_paid():
    result = await compliance(state(refund_amount=999999))
    assert result["compliance_violation"] == "amount_exceeds_paid"


async def test_red_line_zero_amount_but_eligible():
    result = await compliance(state(refund_amount=0))
    assert result["compliance_violation"] == "amount_not_positive"


async def test_red_line_not_applicable_category():
    result = await compliance(
        state(order_snapshot=snapshot(category="婴儿推车（刻字定制款）"))
    )
    assert result["compliance_violation"] == "not_applicable_bypass"


async def test_red_line_opened_bottle():
    result = await compliance(
        state(
            order_snapshot=snapshot(category="奶瓶"),
            refund_draft=draft(packaging_opened=True),
        )
    )
    assert result["compliance_violation"] == "not_applicable_bypass"


async def test_red_line_window_bypass():
    result = await compliance(
        state(order_snapshot=snapshot(signed_at="2026-10-20T10:00:00"))
    )
    assert result["compliance_violation"] == "window_bypass"


async def test_quality_issue_is_not_window_bypass():
    """质量问题不受 7 天限制——红线不能把合法的情形拦掉。"""
    result = await compliance(
        state(
            order_snapshot=snapshot(signed_at="2026-10-01T10:00:00"),
            refund_draft=draft(reason_code="quality"),
            refund_judgement=judgement(eligible=True, reason_code="ok"),
        )
    )
    assert result["compliance_violation"] is None


async def test_not_eligible_skips_red_lines():
    """不可退时金额本来就是 0，不能因为 0 就判 amount_not_positive。"""
    result = await compliance(
        state(
            refund_amount=0,
            refund_judgement=judgement(eligible=False, reason_code="not_intact"),
            refund_draft=draft(condition_ok=False),
        )
    )
    assert result["compliance_violation"] is None


# ── 独立复算 ────────────────────────────────────────────────


async def test_agreement_eligible_passes():
    assert (await compliance(state()))["compliance_violation"] is None


async def test_agreement_ineligible_passes():
    result = await compliance(
        state(
            refund_amount=0,
            refund_draft=draft(condition_ok=False),
            refund_judgement=judgement(eligible=False, reason_code="not_intact"),
        )
    )
    assert result["compliance_violation"] is None


async def test_divergence_goes_human():
    """模型判可退，规则代码判不完好——分歧必须被看见。"""
    result = await compliance(
        state(
            refund_draft=draft(reason_code="no_reason", condition_ok=False),
            refund_judgement=judgement(eligible=True, reason_code="ok"),
        )
    )
    assert result["compliance_violation"].startswith("rule_divergence:")
    assert "model=ok" in result["compliance_violation"]
    assert "code=not_intact" in result["compliance_violation"]


async def test_quality_with_use_trace_does_not_diverge():
    """质量问题 + 用过：代码返回 None（分不清责任），不否决模型。

    这是 H04 的结论——异响要开机才能发现，不能拿"试过一次"判不完好。
    """
    result = await compliance(
        state(
            refund_draft=draft(reason_code="quality", condition_ok=False),
            refund_judgement=judgement(eligible=True, reason_code="ok"),
        )
    )
    assert result["compliance_violation"] is None


async def test_reason_code_mismatch_is_also_divergence():
    """都判可退，但一个是 ok、一个是 reject_then_refund——话术完全不同（寄回 vs 拒收）。"""
    result = await compliance(
        state(
            order_snapshot=snapshot(status="已发货", signed_at=None),
            refund_amount=269900,
            refund_judgement=judgement(eligible=True, reason_code="ok"),
        )
    )
    assert result["compliance_violation"].startswith("rule_divergence:")
    assert "code=reject_then_refund" in result["compliance_violation"]


async def test_code_cannot_decide_is_not_divergence():
    """`condition_ok` 为 null：代码也不知道答案，没资格否决模型。"""
    result = await compliance(
        state(
            refund_amount=0,
            refund_draft=draft(condition_ok=None),
            refund_judgement=judgement(eligible=False, reason_code="not_intact"),
        )
    )
    assert result["compliance_violation"] is None


# ── 转人工载荷与 trace ─────────────────────────────────────


async def test_violation_writes_transfer_and_span(fixed_clock):
    result = await compliance(state(refund_amount=999999))

    assert result["transfer_reason"] == "amount_exceeds_paid"
    assert result["actions"] == [{"type": "transfer_human"}]
    assert len(fixed_clock) == 1
    assert fixed_clock[0]["name"] == "compliance:violation"
    assert fixed_clock[0]["attrs"]["violation"] == "amount_exceeds_paid"


async def test_clean_case_writes_no_span(fixed_clock):
    await compliance(state())
    assert fixed_clock == []


# ── 条件边 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"compliance_violation": None}, "respond"),
        ({"compliance_violation": "amount_exceeds_paid"}, "human"),
    ],
)
def test_after_compliance_routing(state, expected):
    assert after_compliance(state) == expected
