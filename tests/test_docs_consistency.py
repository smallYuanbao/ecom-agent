"""文档与代码的一致性守卫。

`00-tasks.md` 是后续执行的依据，但它不会自己校验：
写错的路径、坏掉的表格列数，都要等到有人照着做才会暴露。

两条守卫：

1. **标了 ✅ 的任务，产出路径必须真实存在**——否则"已完成"是假的
2. **所有表格列数一致**——P4 就曾因为多了一格，在渲染时错位
"""
import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
TASKS = DOCS / "superpowers/plans/00-tasks.md"

# 只有这些前缀算"仓库内的产出路径"，其余（如 `data/app.db` 运行产物）不校验
PATH_PREFIXES = ("app/", "scripts/", "data/", "tests/", "docs/", "client/")

_ROW_RE = re.compile(r"^\| (P\d-\d+) \|")
_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _table_rows(text: str) -> list[dict]:
    """把表格行解析成 dict：id / 产出 / 状态。

    列序：# | 任务 | 依据 | 产出 | 验收 | 状态
    """
    rows: list[dict] = []
    for line in text.splitlines():
        if not _ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        rows.append(
            {
                "id": cells[0],
                "output": cells[3] if len(cells) >= 5 else "",
                "status": cells[-1],
            }
        )
    return rows


def _paths_in(cell: str) -> list[str]:
    """从产出列里抽出仓库内路径（支持 `dir/*.ext` 通配）。"""
    found = []
    for raw in _BACKTICK_RE.findall(cell):
        if raw.startswith(PATH_PREFIXES) or raw.startswith("./"):
            found.append(raw)
    return found


@pytest.mark.parametrize("row", _table_rows(TASKS.read_text(encoding="utf-8")),
                         ids=lambda r: r["id"])
def test_completed_task_outputs_exist(row):
    """标 ✅ 的任务，它声明的产出必须真的在仓库里。

    这条防的是"任务表说做完了、路径其实写错或没做"——P2-7 就曾指向
    一个不存在的 `subgraph_knowledge.py`。
    """
    if not row["status"].startswith("✅"):
        pytest.skip("仅校验已完成任务")

    root = TASKS.parents[3]
    missing = [p for p in _paths_in(row["output"]) if not list(root.glob(p))]
    assert not missing, f"{row['id']} 标了已完成，但产出不存在：{missing}"


def test_all_tables_have_consistent_columns():
    """表头、分隔行、数据行的列数必须一致——否则 markdown 渲染会错位。

    P4 曾出现「验收列后面又跟了一格状态」，表面看不出问题，渲染时整行跑偏。
    """
    lines = TASKS.read_text(encoding="utf-8").splitlines()
    problems: list[str] = []

    for index, line in enumerate(lines):
        if not line.startswith("| # |"):
            continue
        expected = line.count("|") - 1
        separator = lines[index + 1] if index + 1 < len(lines) else ""
        if separator.count("|") - 1 != expected:
            problems.append(f"第 {index + 1} 行的分隔行列数不符：{separator}")

        cursor = index + 2
        while cursor < len(lines) and re.match(r"^\| P\d-\d+", lines[cursor]):
            actual = lines[cursor].count("|") - 1
            if actual != expected:
                problems.append(
                    f"第 {cursor + 1} 行列数 {actual} ≠ 表头 {expected}：{lines[cursor][:40]}…"
                )
            cursor += 1

    assert not problems, "表格列数不一致：\n" + "\n".join(problems)


def test_no_task_id_is_duplicated():
    """任务编号重复会让"勾选"指向不明。"""
    ids = [row["id"] for row in _table_rows(TASKS.read_text(encoding="utf-8"))]
    duplicated = {i for i in ids if ids.count(i) > 1}
    assert not duplicated, f"重复的任务编号：{duplicated}"


def test_status_values_are_known():
    """状态值只用约定里的四种，避免出现"差不多完成"这种自造状态。"""
    allowed = {"✅", "⏳", "⚠️", "—"}
    for row in _table_rows(TASKS.read_text(encoding="utf-8")):
        head = row["status"].split()[0] if row["status"] else ""
        assert head in allowed, f"{row['id']} 的状态非法：{row['status']!r}"
