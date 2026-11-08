"""候选短名单：桶上限、排除标记与来源轮转。"""
import pytest

from scripts.build_candidate_review import Candidate
from scripts.shortlist_candidates import parse_limits, select_shortlist


def _candidate(
    cid: str,
    *,
    bucket: str,
    model: str = "model-a",
    batch: str = "D",
    flags: list[str] | None = None,
) -> Candidate:
    return Candidate(
        id=cid,
        query=f"问题{cid}",
        style="formal",
        scenario="",
        model=model,
        source_batch=batch,
        source_file="",
        suggested_bucket=bucket,
        flags=flags or [],
    )


def test_excludes_pii_artifact_and_near_duplicate():
    candidates = [
        _candidate("C1", bucket="B6", flags=["injection"]),
        _candidate("C2", bucket="B6", flags=["injection", "pii"]),
        _candidate("C3", bucket="B6", flags=["injection", "prompt_artifact"]),
        _candidate("C4", bucket="B6", flags=["injection", "near_duplicate"]),
    ]

    selected = select_shortlist(candidates, {"B6": 10})

    assert [item.id for item in selected] == ["C1"]


def test_respects_bucket_limit():
    candidates = [
        _candidate(f"C{i}", bucket="B6", model=f"m{i}", flags=["injection"])
        for i in range(10)
    ]

    selected = select_shortlist(candidates, {"B6": 3})

    assert len(selected) == 3


def test_round_robins_models():
    candidates = [
        _candidate(f"A{i}", bucket="B6", model="model-a", flags=["injection"])
        for i in range(5)
    ] + [
        _candidate(f"B{i}", bucket="B6", model="model-b", flags=["injection"])
        for i in range(5)
    ]

    selected = select_shortlist(candidates, {"B6": 4})

    models = [item.model for item in selected]
    assert models.count("model-a") == 2
    assert models.count("model-b") == 2


def test_diversifies_topics_before_repeating():
    candidates = [
        _candidate("C1", bucket="B1", model="a", batch="A", flags=["knowledge"]),
        _candidate("C2", bucket="B1", model="b", batch="A", flags=["knowledge"]),
        _candidate("C3", bucket="B1", model="c", batch="A", flags=["knowledge"]),
    ]
    candidates[0].query = "七天无理由从哪天开始算"
    candidates[1].query = "无理由退货期限从哪天算"
    candidates[2].query = "发票什么时候能开"

    selected = select_shortlist(candidates, {"B1": 2})
    topics = {item.query for item in selected}

    assert "发票什么时候能开" in topics
    assert len(selected) == 2


def test_prefers_bucket_flags_and_source():
    candidates = [
        _candidate("C1", bucket="B7", model="a", flags=["complaint"]),
        _candidate("C2", bucket="B7", model="b", flags=[]),
    ]

    selected = select_shortlist(candidates, {"B7": 1})

    assert selected[0].id == "C1"


def test_parse_limits():
    assert parse_limits(None)["B1"] == 12
    limits = parse_limits("B1=3,B6=5")
    assert limits["B1"] == 3
    assert limits["B6"] == 5
    with pytest.raises(SystemExit, match="未知桶"):
        parse_limits("B9=1")
