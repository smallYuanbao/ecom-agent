"""评测集自检。

最要紧的一条：**每条 expect_id 必须能在当前语料里找到**。
语料一改（改小节名、删一节），评测集就会悄悄失效——测试不报错，只是命中率莫名掉。
"""
from collections import Counter

import pytest

from app.core.eval_set import STYLES, load_cases, load_queries
from app.kb.loader import load_chunks

CASE_COUNT = 15
QUERY_COUNT = 45


def test_case_and_query_counts():
    cases = load_cases()
    assert len(cases) == CASE_COUNT
    assert len(load_queries()) == QUERY_COUNT


def test_every_case_has_multiple_phrasings():
    """一道题至少 2 种问法，否则测不出"换个说法就召回不到"。"""
    for case in load_cases():
        assert len(case.queries) >= 2, f"{case.id} 只有一种问法"


def test_expect_ids_exist_in_corpus():
    """expect_id 必须对得上当前 chunk id——语料改了这里会先红。"""
    known = {c["id"] for c in load_chunks()}
    for case in load_cases():
        if case.expect_id is None:
            continue
        assert case.expect_id in known, f"{case.id} 的 expect_id 不在语料里：{case.expect_id}"


def test_refuse_cases_have_no_expectation():
    """该拒答的题不能同时声明"期望命中某块"——语义矛盾。"""
    for case in load_cases():
        if case.should_refuse:
            assert case.expect_id is None, f"{case.id} 既拒答又要求命中"
            assert case.expect_points == []


def test_queries_are_unique():
    """重复问法会让某条被统计两次，悄悄抬高指标。"""
    texts = [q.query for q in load_queries()]
    duplicated = [text for text, n in Counter(texts).items() if n > 1]
    assert not duplicated, f"重复问法：{duplicated}"


def test_styles_are_known():
    for query in load_queries():
        assert query.style in STYLES, f"{query.query!r} 的 style 非法：{query.style}"


def test_every_style_is_exercised():
    """formal 和 colloquial 都要有；否则报告里的分组没意义。"""
    counts = Counter(q.style for q in load_queries())
    assert counts["formal"] >= 10
    assert counts["colloquial"] >= 10


def test_buckets_cover_the_corpus_areas():
    """四个桶都要有：政策 / 规格 / 法规 / 拒答。缺一个就有一类能力没被测到。"""
    assert {c.bucket for c in load_cases()} == {"policy", "spec", "regulation", "refuse"}


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.id)
def test_expect_points_are_non_empty_for_answerable_cases(case):
    if case.should_refuse:
        return
    assert case.expect_points, f"{case.id} 没有 expect_points，生成侧无法判断答案对不对"
