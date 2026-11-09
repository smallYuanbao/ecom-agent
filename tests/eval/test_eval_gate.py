"""阈值扫描的纯函数：边界、比率、eval_v1 接入。

不调模型、不连 Chroma——扫描逻辑本身必须是可单测的，否则报告里的数字
只能靠手算复核。
"""
import pytest

from scripts.eval_gate import (
    Sample,
    _load_positives,
    _passes,
    curve_row,
    scan_thresholds,
)


def _sample(kind: str, similarity: float, query: str = "q") -> Sample:
    return Sample(kind, query, similarity, "chunk", "test")


def test_scan_range_and_step():
    thresholds = scan_thresholds()
    assert thresholds[0] == pytest.approx(0.40)
    assert thresholds[-1] == pytest.approx(0.75)
    assert len(thresholds) == 36
    assert thresholds[1] - thresholds[0] == pytest.approx(0.01)


def test_mechanical_pass_at_exact_threshold():
    sample = _sample("可答题", 0.55)
    assert _passes(sample, 0.55, 0.0, {}) is True


def test_borderline_uses_verdict_not_similarity():
    sample = _sample("可答题", 0.57, "边界题")
    assert _passes(sample, 0.55, 0.05, {"边界题": True}) is True
    assert _passes(sample, 0.55, 0.05, {"边界题": False}) is False


def test_borderline_missing_verdict_fails_closed():
    sample = _sample("可答题", 0.57, "没跑自评")
    assert _passes(sample, 0.55, 0.05, {}) is False


def test_above_band_is_strong_without_verdict():
    sample = _sample("可答题", 0.60)
    assert _passes(sample, 0.55, 0.05, {}) is True


def test_curve_row_rates():
    samples = [
        _sample("可答题", 0.70),
        _sample("可答题", 0.50),
        _sample("负样本", 0.56),
        _sample("负样本", 0.30),
    ]
    row = curve_row(samples, 0.55, band=0.0, verdicts={}, semantic=False)
    assert row["pos_pass"] == 1
    assert row["pos_miss_rate"] == pytest.approx(0.5)
    assert row["neg_block"] == 1
    assert row["neg_leak_rate"] == pytest.approx(0.5)


def test_eval_v1_positives_are_added_and_deduped():
    all_queries = [query for query, _ in _load_positives("all")]
    eval_queries = [query for query, _ in _load_positives("eval_v1")]
    assert len(eval_queries) == 10
    assert set(eval_queries) <= set(all_queries)
    assert len(all_queries) == len(set(all_queries))
