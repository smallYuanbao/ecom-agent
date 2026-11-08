"""Ch07 端到端评测集加载。

`eval_v1.yaml` 是唯一真源；EvalFlow 的扁平 JSON 由 `scripts/export_evalflow.py`
从这里导出，不反向维护两份。

**为什么单独一个 loader**：`app/core/eval_set.py` 面向 Ch02 的知识检索评测
（一题多问法、query 级正则要点）；Ch07 的 44 条是端到端样本，
字段多了 `expect_route` / `expect_fields` / `expect_refund`，混在一起会让两边都难读。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

EVAL_DIR = Path(__file__).resolve().parents[2] / "data" / "eval"
DEFAULT_SET = EVAL_DIR / "eval_v1.yaml"

BUCKET_COUNTS = {
    "B1": 10,
    "B2": 8,
    "B3": 8,
    "B4": 6,
    "B5": 4,
    "B6": 4,
    "B7": 4,
}

ROUTES = frozenset({"knowledge", "data", "rules", "human", "fallback", "any"})
SPLITS = frozenset({"dev", "test"})
MIN_TEST_CASES = 14


@dataclass(frozen=True)
class EvalItem:
    id: str
    bucket: str
    query: str
    expect_route: str
    expected_output: str
    expect_ids: tuple[str, ...] = ()
    expect_fields: dict[str, Any] = field(default_factory=dict)
    expect_refund: dict[str, Any] | None = None
    expect_actions: tuple[str, ...] = ()
    expect_answer_contains: tuple[str, ...] = ()
    should_refuse: bool = False
    expect_no_leak: bool = False
    style: str = "formal"
    split: str = "dev"
    notes: str = ""


def _parse_item(raw: dict[str, Any]) -> EvalItem:
    return EvalItem(
        id=str(raw["id"]),
        bucket=str(raw["bucket"]),
        query=str(raw["query"]).strip(),
        expect_route=str(raw["expect_route"]),
        expected_output=str(raw.get("expected_output") or "").strip(),
        expect_ids=tuple(raw.get("expect_ids") or ()),
        expect_fields=dict(raw.get("expect_fields") or {}),
        expect_refund=raw.get("expect_refund"),
        expect_actions=tuple(raw.get("expect_actions") or ()),
        expect_answer_contains=tuple(raw.get("expect_answer_contains") or ()),
        should_refuse=bool(raw.get("should_refuse", False)),
        expect_no_leak=bool(raw.get("expect_no_leak", False)),
        style=str(raw.get("style") or "formal"),
        split=str(raw.get("split") or "dev"),
        notes=str(raw.get("notes") or ""),
    )


def load_items(path: str | Path | None = None) -> list[EvalItem]:
    """读 YAML 并返回扁平 item 列表。**不做校验**，校验在 `validate_items`。"""
    source = Path(path) if path is not None else DEFAULT_SET
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    return [_parse_item(item) for item in raw.get("items") or []]


def _check_expected_output(item: EvalItem) -> None:
    if not item.expected_output:
        raise ValueError(f"{item.id}: expected_output 不能为空")
    for group in item.expected_output.split("/"):
        if not group.strip():
            raise ValueError(f"{item.id}: expected_output 有空的要点组")
        for alt in group.split("|"):
            if not alt.strip():
                raise ValueError(f"{item.id}: expected_output 有空的候选项")


def validate_items(items: list[EvalItem]) -> None:
    """结构校验；不通过直接抛 `ValueError`，避免导入一份坏数据。"""
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        raise ValueError(f"重复的 id: {duplicates}")

    counts = {bucket: 0 for bucket in BUCKET_COUNTS}
    test_count = 0
    for item in items:
        if item.bucket not in BUCKET_COUNTS:
            raise ValueError(f"{item.id}: 未知桶 {item.bucket}")
        counts[item.bucket] += 1

        if not item.query:
            raise ValueError(f"{item.id}: query 不能为空")
        if item.expect_route not in ROUTES:
            raise ValueError(f"{item.id}: 非法 expect_route {item.expect_route}")
        if item.split not in SPLITS:
            raise ValueError(f"{item.id}: 非法 split {item.split}")
        if item.split == "test":
            test_count += 1

        _check_expected_output(item)
        if item.expect_refund is not None:
            if not isinstance(item.expect_refund.get("eligible"), bool):
                raise ValueError(f"{item.id}: expect_refund.eligible 必须是 bool")
            if not isinstance(item.expect_refund.get("amount"), int):
                raise ValueError(f"{item.id}: expect_refund.amount 必须是整数分")

    if counts != BUCKET_COUNTS:
        raise ValueError(f"桶计数不符: {counts} != {BUCKET_COUNTS}")
    if test_count < MIN_TEST_CASES:
        raise ValueError(f"test 集只有 {test_count} 条，至少 {MIN_TEST_CASES} 条")
