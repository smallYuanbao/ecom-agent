"""证据闸与兜底落池。

闸的测试重点不是"分数高就过"，而是**边界与失败模式**：
空结果、刚好等于阈值、以及那个已知会漏过去的域外问题。
"""
import json

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.core.fallback import fallback_text, record_low_conf
from app.core.gate import explain, gate_decision, is_strong
from app.db.models import LowConfQuestion


def _hit(similarity: float, chunk_id: str = "returns#退货运费由谁承担") -> dict:
    return {"id": chunk_id, "text": "", "source": "", "section": "", "similarity": similarity}


# ── 闸 ──────────────────────────────────────────────────────


def test_empty_hits_is_not_strong():
    """零召回必须判弱——fail-closed，不能因为"没有证据所以没发现问题"就放行。"""
    assert is_strong([]) is False


def test_above_threshold_passes():
    threshold = get_settings().evidence_threshold
    assert is_strong([_hit(threshold + 0.05)]) is True


def test_below_threshold_rejected():
    threshold = get_settings().evidence_threshold
    assert is_strong([_hit(threshold - 0.05)]) is False


def test_exactly_at_threshold_passes():
    """等于阈值算通过——`>=` 而不是 `>`，边界要写死并测到。"""
    threshold = get_settings().evidence_threshold
    assert is_strong([_hit(threshold)]) is True


def test_only_top1_matters():
    """只看第一条。后面再高也不影响（这是当前设计的局限，不是 bug）。"""
    threshold = get_settings().evidence_threshold
    assert is_strong([_hit(threshold - 0.1), _hit(0.99)]) is False


def test_gate_decision_three_states(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_threshold", 0.55)
    monkeypatch.setattr(settings, "evidence_semantic_band", 0.05)
    monkeypatch.setattr(settings, "evidence_semantic_enabled", True)

    assert gate_decision([]) == "weak"
    assert gate_decision([_hit(0.54)]) == "weak"
    assert gate_decision([_hit(0.55)]) == "borderline"
    assert gate_decision([_hit(0.57)]) == "borderline"
    assert gate_decision([_hit(0.60)]) == "strong"


def test_gate_decision_falls_back_when_semantic_disabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_threshold", 0.55)
    monkeypatch.setattr(settings, "evidence_semantic_band", 0.05)
    monkeypatch.setattr(settings, "evidence_semantic_enabled", False)

    assert gate_decision([_hit(0.57)]) == "strong"


def test_threshold_is_calibrated_not_default():
    """守卫：阈值是实测出来的 0.55，不是随手写的 0.45。

    0.45 会让几乎所有问题都通过——实测可答题最低 0.566、拒答题最高 0.500，
    0.45 连拒答题都拦不住，闸形同虚设。
    """
    assert get_settings().evidence_threshold == pytest.approx(0.55)


# ── 闸的判定依据 ─────────────────────────────────────────────


def test_explain_on_zero_hits_says_zero_hits():
    info = explain([])
    assert info["passed"] is False
    assert info["reason"] == "zero_hits"


def test_explain_records_top1_for_postmortem():
    """落池时要知道"当时第一条是什么、打多少分"，否则无法判断是阈值还是语料问题。"""
    info = explain([_hit(0.501, "promo#本文档不提供什么")])
    assert info["reason"] == "below_threshold"
    assert info["top1_id"] == "promo#本文档不提供什么"
    assert info["top1_similarity"] == pytest.approx(0.501)


def test_known_leak_is_documented():
    """已知漏洞：域外问题「能分期付款吗」实测 0.572，越过 0.55 的闸。

    这条测试不是在庆祝这个行为，而是把它**钉住**——哪天改了阈值或加了第二信号，
    这里会红，逼我们回来更新 gate.py 里的说明。
    """
    leaked = _hit(0.572, "member#积分规则")
    assert is_strong([leaked]) is True


# ── 兜底话术 ────────────────────────────────────────────────


def test_fallback_with_candidate_title_asks_a_follow_up():
    text, next_step = fallback_text("能分期吗", "会员积分规则")
    assert "能分期吗" in text
    assert "会员积分规则" in next_step
    assert text.endswith(next_step)


def test_fallback_without_title_offers_transfer():
    _, next_step = fallback_text("你们老板是谁", None)
    assert "转人工" in next_step


def test_fallback_never_promises_anything():
    """兜底话术不能出现"退款""赔偿""X 天内"这类承诺性表述。"""
    text, _ = fallback_text("随便问问", None)
    for word in ("退款", "赔偿", "包退"):
        assert word not in text


# ── 落池 ────────────────────────────────────────────────────


async def _count(session) -> int:
    return len((await session.scalars(select(LowConfQuestion))).all())


async def test_record_with_zero_hits_stores_empty_list(session):
    """零命中和"有召回但分低"必须能区分——一个该补语料，一个该调检索。"""
    await record_low_conf(question="你们卖不卖二手车", reason="below_threshold",
                          hits=[], session=session)
    row = (await session.scalars(select(LowConfQuestion))).one()
    assert row.retrieved == "[]"


async def test_record_with_weak_hits_stores_ids_and_scores(session):
    hits = [_hit(0.42, "promo#本文档不提供什么"), _hit(0.40, "member#积分规则")]
    await record_low_conf(question="能分期付款吗", reason="below_threshold",
                          hits=hits, session=session)

    row = (await session.scalars(select(LowConfQuestion))).one()
    payload = json.loads(row.retrieved)
    assert payload[0]["id"] == "promo#本文档不提供什么"
    assert payload[0]["similarity"] == pytest.approx(0.42)


async def test_record_without_retrieval_stores_null(session):
    """没走检索的问题，`retrieved` 留 NULL——不能写成 `[]` 冒充"知识缺口"。"""
    await record_low_conf(question="我这单能退多少", reason="rule_exit_not_implemented",
                          hits=None, session=session)
    row = (await session.scalars(select(LowConfQuestion))).one()
    assert row.retrieved is None


async def test_record_keeps_conversation_id_and_source(session):
    await record_low_conf(question="测试", reason="below_threshold", hits=[],
                          conversation_id=7, source="retrieval_low_conf", session=session)
    row = (await session.scalars(select(LowConfQuestion))).one()
    assert row.conversation_id == 7
    assert row.source == "retrieval_low_conf"
    assert row.status == "pending"
    assert row.occurrence == 1


async def test_record_is_append_only(session):
    """同一个问题问两次就记两行——归一化与合并留给飞轮审核，不在写入时做。"""
    for _ in range(2):
        await record_low_conf(question="同一个问题", reason="below_threshold",
                              hits=[], session=session)
    assert await _count(session) == 2
