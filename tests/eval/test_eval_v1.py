"""Ch07 评测集：结构、桶计数、盲测与导出。"""
import json
from dataclasses import replace

import pytest

from app.core.eval_v1 import (
    BUCKET_COUNTS,
    MIN_TEST_CASES,
    load_items,
    validate_items,
)
from scripts.export_evalflow import to_rows, write_json
from scripts.seed_eval import eval_orders


@pytest.fixture(scope="module")
def items():
    loaded = load_items()
    validate_items(loaded)
    return loaded


def test_bucket_counts(items):
    counts = {bucket: 0 for bucket in BUCKET_COUNTS}
    for item in items:
        counts[item.bucket] += 1

    assert counts == BUCKET_COUNTS
    assert len(items) == sum(BUCKET_COUNTS.values()) == 44


def test_ids_are_unique(items):
    assert len({item.id for item in items}) == len(items)


def test_blind_split_has_enough_cases(items):
    assert sum(1 for item in items if item.split == "test") >= MIN_TEST_CASES


def test_routes_are_known(items):
    assert {item.expect_route for item in items} <= {
        "knowledge",
        "data",
        "rules",
        "human",
        "fallback",
        "any",
    }


def test_expected_output_syntax(items):
    for item in items:
        assert item.expected_output
        for group in item.expected_output.split("/"):
            assert group.strip()
            assert all(alt.strip() for alt in group.split("|"))


def test_refund_amounts_are_cents(items):
    for item in items:
        if item.expect_refund is None:
            continue
        assert isinstance(item.expect_refund["eligible"], bool)
        assert isinstance(item.expect_refund["amount"], int)
        assert item.expect_refund["amount"] >= 0


def test_export_rows_match_items(items):
    rows = to_rows(items)

    assert len(rows) == len(items)
    assert [row["input"] for row in rows] == [item.query for item in items]
    assert {row["bucket"] for row in rows} == set(BUCKET_COUNTS)
    assert len({row["input"] for row in rows}) == len(rows)


def test_write_json(tmp_path):
    out = tmp_path / "evalflow_v1.json"
    rows = to_rows(load_items())

    write_json(rows, out)

    assert out.exists()
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 44


def test_eval_orders_have_relative_dates():
    orders = {order.id: order for order in eval_orders()}

    assert len(orders) == 8
    assert orders["1101"].signed_at is not None
    assert orders["1105"].signed_at < orders["1105"].shipped_at      # 日期倒挂
    assert orders["1108"].status == "已发货"
    assert orders["1108"].signed_at is None


def test_duplicate_id_is_rejected(items):
    broken = items + [replace(items[0], query="另一句")]

    with pytest.raises(ValueError, match="重复的 id"):
        validate_items(broken)


def test_unknown_route_is_rejected(items):
    broken = [replace(items[0], expect_route="unknown")] + items[1:]

    with pytest.raises(ValueError, match="非法 expect_route"):
        validate_items(broken)


def test_empty_expected_output_is_rejected(items):
    broken = [replace(items[0], expected_output="")] + items[1:]

    with pytest.raises(ValueError, match="expected_output"):
        validate_items(broken)


def test_wrong_bucket_count_is_rejected(items):
    broken = [replace(items[0], bucket="B2")] + items[1:]

    with pytest.raises(ValueError, match="桶计数不符"):
        validate_items(broken)
