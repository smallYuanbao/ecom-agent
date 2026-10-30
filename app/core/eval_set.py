"""评测集加载。

**一道基础题 = 多条问法，答案不变。** 这是刻意的设计：

- 标注成本只花一次（写一遍 expect_id / expect_points）
- 换问法不换答案，正好用来测**口语化、同义表达**带来的召回衰减
- 报告可以按 `style` 分组，看出"书面问得准、口语问就废"这类问题

字段说明见 `data/eval/knowledge_v1.yaml` 顶部注释。
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

EVAL_DIR = Path(__file__).resolve().parents[2] / "data" / "eval"
DEFAULT_SET = EVAL_DIR / "knowledge_v1.yaml"

STYLES = ("formal", "colloquial", "multi")


@dataclass(frozen=True)
class EvalCase:
    """一道基础题（含多条问法）。"""

    id: str
    bucket: str
    expect_ids: list[str]          # 任一命中即算对；should_refuse 时为空
    expect_points: list[str]       # 兜底要点（query 级没写时才用）
    should_refuse: bool
    queries: list[dict[str, Any]]  # [{"q": ..., "style": ..., "points": [...], "skip_coverage": bool}]

    @property
    def query_texts(self) -> list[str]:
        return [item["q"] for item in self.queries]


@dataclass(frozen=True)
class EvalQuery:
    """展开后的一条查询（一道基础题 × 一种问法）。"""

    case_id: str
    bucket: str
    style: str
    query: str
    expect_ids: list[str]
    expect_points: list[str]       # **这一问的**答案要点（query 级）
    skip_coverage: bool            # 命中哪块合理资料都对、答案内容不同 → 不计覆盖
    should_refuse: bool


def _parse_case(raw: dict[str, Any]) -> EvalCase:
    return EvalCase(
        id=raw["id"],
        bucket=raw["bucket"],
        expect_ids=list(raw.get("expect_ids") or []),
        expect_points=list(raw.get("expect_points") or []),
        should_refuse=bool(raw.get("should_refuse", False)),
        queries=list(raw["queries"]),
    )


@lru_cache
def load_cases(path: Path | None = None) -> tuple[EvalCase, ...]:
    """读原始结构（一道题一个元素）。"""
    target = path or DEFAULT_SET
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    return tuple(_parse_case(item) for item in data["cases"])


def load_queries(path: Path | None = None) -> list[EvalQuery]:
    """展开成逐条查询，供检索评测使用。"""
    flattened: list[EvalQuery] = []
    for case in load_cases(path):
        for item in case.queries:
            flattened.append(
                EvalQuery(
                    case_id=case.id,
                    bucket=case.bucket,
                    style=item["style"],
                    query=item["q"],
                    expect_ids=case.expect_ids,
                    # 要点优先取 query 级的：同一个 case 的不同问法问的不是同一件事
                    expect_points=list(item.get("points") or case.expect_points),
                    skip_coverage=bool(item.get("skip_coverage", False)),
                    should_refuse=case.should_refuse,
                )
            )
    return flattened
