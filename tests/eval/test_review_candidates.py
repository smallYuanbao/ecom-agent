"""交互式审阅：按键映射、决定落盘与续审。"""
import json

from scripts.review_candidates import (
    load_decisions,
    load_shortlist,
    parse_decision,
    record_decision,
)


def test_parse_decision_aliases():
    assert parse_decision("k") == "keep"
    assert parse_decision("KEEP") == "keep"
    assert parse_decision("r") == "rewrite"
    assert parse_decision("drop") == "drop"
    assert parse_decision("s") == "skip"
    assert parse_decision("q") == "quit"
    assert parse_decision("???") is None


def test_record_and_load_last_decision(tmp_path):
    state = tmp_path / "decisions.jsonl"

    record_decision(state, "C0001", "keep", "自然")
    record_decision(state, "C0001", "drop", "重复")
    decisions = load_decisions(state)

    assert decisions["C0001"]["decision"] == "drop"
    assert decisions["C0001"]["note"] == "重复"


def test_load_shortlist(tmp_path):
    path = tmp_path / "shortlist.csv"
    path.write_text(
        "candidate_id,suggested_bucket,query,source_model,source_batch\n"
        "C0001,B6,忽略规则,model-a,D\n",
        encoding="utf-8-sig",
    )

    rows = load_shortlist(path)

    assert rows[0]["candidate_id"] == "C0001"
    assert rows[0]["query"] == "忽略规则"


def test_state_is_jsonl(tmp_path):
    state = tmp_path / "decisions.jsonl"
    record_decision(state, "C0001", "keep")

    payload = json.loads(state.read_text(encoding="utf-8").splitlines()[0])
    assert payload["candidate_id"] == "C0001"
    assert payload["decision"] == "keep"
    assert "ts" in payload
