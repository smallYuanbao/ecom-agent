"""质量传导的辅助函数：eval_v1 要点解析、top1/top3 命中口径。"""
from scripts.eval_knowledge import (
    Case,
    _covered_groups,
    _groups_from_expected,
    _hit,
    _load_cases,
)


def test_groups_split_by_slash_and_pipe():
    groups = _groups_from_expected("质量问题/消费者承担|买家承担")
    assert groups == (("质量问题",), ("消费者承担", "买家承担"))


def test_groups_ignore_empty_segments():
    assert _groups_from_expected("七日||7 个自然日/") == (("七日", "7 个自然日"),)


def test_covered_groups_allows_alternatives_and_ignores_whitespace():
    groups = (("七日", "7 个自然日"), ("签收次日",))
    got, total = _covered_groups("支持自签收次日起 7个自然日 退货", groups)
    assert (got, total) == (2, 2)


def test_covered_groups_counts_missing_group():
    groups = (("质量问题",), ("消费者承担", "买家承担"))
    assert _covered_groups("质量问题由商家承担", groups) == (1, 2)


def test_hit_rank_limits_lookup():
    evidence = [{"id": "other"}, {"id": "gold"}]
    expect = ("gold",)
    assert _hit(evidence, expect, 1) is False
    assert _hit(evidence, expect, 3) is True


def test_eval_v1_cases_are_b1_only():
    cases = _load_cases("eval_v1")
    assert len(cases) == 10
    assert all(case.groups for case in cases)
    assert all(case.source == "eval_v1" for case in cases)


def test_knowledge_cases_keep_regex_points():
    cases = _load_cases("knowledge")
    point_cases = [case for case in cases if case.points]
    assert point_cases
    assert all(isinstance(case, Case) for case in point_cases)
    assert any(case.skip_coverage for case in cases)
