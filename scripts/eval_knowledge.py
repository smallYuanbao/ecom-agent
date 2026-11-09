"""知识出口评测：引用覆盖率、答案要点覆盖，以及**质量传导**四格表。

**质量传导**（quality propagation）指：检索好坏会不会传到最终答案上。
把它列成 2×2，最要紧的是第四格——

|                | 答案覆盖全部要点 | 答案缺要点 |
|----------------|------------------|------------|
| **检索命中**    | 正常             | 检索给了但没用上 |
| **检索未命中**  | 🚨 **可疑**      | 兜底，符合预期 |

「检索没找到依据，答案却很完整」只有两种可能：模型瞎蒙对了，或者**编造**。
这一格必须人工看几条——这是 Ch06 加输出校验的依据。

**命中口径**同时输出两套：

- hit@3：生产口径（`TOP_K=5`，gold 在前三条里）；
- hit@1：严格口径（gold 必须排第一）。top1 不是 gold、但 top2/3 是 gold 时，
  生成仍有依据，用来看「排序变差会不会传到答案」。

`--top-k 1` 是**受控降级实验**：只把一条证据交给生成，用来制造真实的检索未命中，
把 2×2 的空格填上。它不是生产配置，报告里必须标清楚。

只跑 A 出口子图（不经过主图的意图分类），这样指标只反映"检索 + 闸 + 生成"。

用法：

    uv run python scripts/eval_knowledge.py --out docs/reports/knowledge-eval.md
    uv run python scripts/eval_knowledge.py --set eval_v1 --top-k 1 --out /tmp/deg.md

    # 受控降级：截断 gold / 只喂 rank5 / 绕过闸（只用于质量传导报告）
    uv run python scripts/eval_knowledge.py --only-rank 1 --truncate 60 --limit 8
    uv run python scripts/eval_knowledge.py --only-rank 5 --force-strong --limit 10
"""
import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncio

import app.graph.knowledge as knowledge_graph
from app.core.eval_set import load_queries
from app.core.eval_v1 import load_items
from app.core.gate import is_strong
from app.core.kb_version import kb_version
from app.graph.knowledge import build_knowledge_graph

HIT_AT = 3


@dataclass(frozen=True)
class Case:
    query: str
    expect_ids: tuple[str, ...]
    source: str
    points: tuple[str, ...] = ()              # knowledge 集：正则
    groups: tuple[tuple[str, ...], ...] = ()  # eval_v1：/ 分组、| 任选
    skip_coverage: bool = False


@dataclass
class Row:
    case: Case
    evidence: list[dict]
    answer: str
    gated: bool
    cited: bool
    points_got: int
    points_total: int
    hit1: bool = False
    hit3: bool = False
    meta: dict = field(default_factory=dict)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _groups_from_expected(text: str) -> tuple[tuple[str, ...], ...]:
    """`"质量问题/消费者承担|买家承担"` → (("质量问题",), ("消费者承担", "买家承担"))。"""
    groups: list[tuple[str, ...]] = []
    for group in text.split("/"):
        alts = tuple(alt.strip() for alt in group.split("|") if alt.strip())
        if alts:
            groups.append(alts)
    return tuple(groups)


def _covered(answer: str, points: tuple[str, ...]) -> tuple[int, int]:
    """knowledge 集：要点是正则。返回 (命中数, 总数)。"""
    if not points:
        return (0, 0)
    hits = sum(1 for pattern in points if re.search(pattern, answer))
    return (hits, len(points))


def _covered_groups(answer: str, groups: tuple[tuple[str, ...], ...]) -> tuple[int, int]:
    """eval_v1：每组是「且」，组内是「或」；按去空白后的字面匹配。"""
    if not groups:
        return (0, 0)
    compact = _compact(answer)
    hits = sum(1 for alts in groups if any(_compact(alt) in compact for alt in alts))
    return (hits, len(groups))


def _hit(evidence: list[dict], expect_ids: tuple[str, ...], rank: int) -> bool:
    ids = {item["id"] for item in evidence[:rank]}
    return any(expect in ids for expect in expect_ids)


def _load_cases(set_name: str) -> list[Case]:
    if set_name == "eval_v1":
        return [
            Case(
                query=item.query,
                expect_ids=item.expect_ids,
                source="eval_v1",
                groups=_groups_from_expected(item.expected_output),
            )
            for item in load_items()
            if item.bucket == "B1"
        ]
    return [
        Case(
            query=q.query,
            expect_ids=tuple(q.expect_ids),
            source=f"knowledge/{q.style}",
            points=tuple(q.expect_points),
            skip_coverage=q.skip_coverage,
        )
        for q in load_queries()
        if not q.should_refuse
    ]


def _matrix(rows: list[Row], hit_attr: str) -> dict[tuple[bool, bool], list[Row]]:
    cells: dict[tuple[bool, bool], list[Row]] = {
        (True, True): [],
        (True, False): [],
        (False, True): [],
        (False, False): [],
    }
    for row in rows:
        if not row.points_total:
            continue
        covered_all = row.points_got == row.points_total
        cells[(getattr(row, hit_attr), covered_all)].append(row)
    return cells


def _cell_table(cells: dict[tuple[bool, bool], list[Row]]) -> list[str]:
    return [
        "|  | 答案覆盖全部要点 | 答案缺要点 |",
        "|---|---|---|",
        f"| **检索命中** | {len(cells[(True, True)])} | {len(cells[(True, False)])} |",
        f"| **检索未命中** | 🚨 {len(cells[(False, True)])} | {len(cells[(False, False)])} |",
    ]


def _suspicious(cells: dict[tuple[bool, bool], list[Row]]) -> list[str]:
    rows = cells[(False, True)]
    if not rows:
        return ["**这一格为空。**"]
    lines = [
        "检索没找到依据、答案却把要点写全了。只有两种可能：蒙对了，或者编造。",
        "",
    ]
    for row in rows:
        lines.append(f"- `{row.case.query}`")
        lines.append(f"  - 期望：{' / '.join(row.case.expect_ids)}")
        lines.append(f"  - 命中：{[e['id'] for e in row.evidence[:HIT_AT]]}")
        lines.append(f"  - 回答：{row.answer[:200]}")
    return lines


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="把报告写到这个路径（markdown）")
    parser.add_argument(
        "--set", dest="set_name", default="knowledge",
        choices=("knowledge", "eval_v1"),
        help="knowledge = knowledge_v1.yaml；eval_v1 = 44 条里的 B1",
    )
    parser.add_argument(
        "--top-k", dest="top_k", type=int, default=5,
        help="交给生成的证据条数；1 = 受控降级实验（非生产配置）",
    )
    parser.add_argument(
        "--only-rank", type=int,
        help="只把第 N 条命中交给生成（1-based）；受控降级实验，非生产配置",
    )
    parser.add_argument(
        "--truncate", type=int,
        help="把证据文本截到 N 个字符；模拟切分/截断丢信息",
    )
    parser.add_argument(
        "--force-strong", action="store_true",
        help="绕过证据闸（只用于错误证据实验，观察生成层会不会编）",
    )
    parser.add_argument("--limit", type=int, help="只跑前 N 条，便于抽检")
    args = parser.parse_args()

    # 受控降级：retrieve / judge 读取模块级函数与常量，改这里不影响生产配置。
    knowledge_graph.TOP_K = args.top_k
    if args.only_rank or args.truncate:
        original_search = knowledge_graph.search

        async def patched_search(query: str, top_k: int = 5) -> list[dict]:
            hits = await original_search(
                query, top_k=max(top_k, args.only_rank or 0)
            )
            if args.only_rank:
                hits = hits[args.only_rank - 1 : args.only_rank]
            if args.truncate:
                hits = [{**hit, "text": (hit.get("text") or "")[: args.truncate]} for hit in hits]
            return hits

        knowledge_graph.search = patched_search
    if args.force_strong:
        knowledge_graph.gate_decision = lambda hits: "strong"

    graph = build_knowledge_graph()
    rows: list[Row] = []
    cases = _load_cases(args.set_name)
    if args.limit:
        cases = cases[: args.limit]

    for index, case in enumerate(cases, start=1):
        state = await graph.ainvoke({"raw_query": case.query, "resolved_query": case.query})
        evidence = state.get("evidence") or []
        answer = state.get("answer", "")
        if case.points:
            got, total = _covered(answer, case.points)
        else:
            got, total = _covered_groups(answer, case.groups)
        rows.append(
            Row(
                case=case,
                evidence=evidence,
                answer=answer,
                gated=state.get("evidence_strong", is_strong(evidence)),
                cited=bool(state.get("citation_ids")),
                points_got=got,
                points_total=total,
                hit1=_hit(evidence, case.expect_ids, 1),
                hit3=_hit(evidence, case.expect_ids, HIT_AT),
            )
        )
        print(f"  {index}/{len(cases)} {case.query}", end="\r")
    print()

    n = len(rows)
    cited = sum(1 for r in rows if r.cited)
    gated = sum(1 for r in rows if r.gated)
    scored = [r for r in rows if r.points_total]
    full = sum(1 for r in scored if r.points_got == r.points_total)
    partial = sum(r.points_got for r in scored) / max(1, sum(r.points_total for r in scored))

    cells3 = _matrix(rows, "hit3")
    cells1 = _matrix(rows, "hit1")

    lines = [
        "# 知识出口与质量传导",
        "",
        (f"> 语料 kb_version=`{kb_version()}` ｜ 样本集 `{args.set_name}` ｜ "
         f"{n} 条查询（拒答题不参与） ｜ 生成 top_k={args.top_k}，temperature=0.2"),
        (f"> 诊断模式：only_rank={args.only_rank} ｜ truncate={args.truncate} ｜ "
         f"force_strong={args.force_strong}（默认生产配置时三项均为空/false）"),
        "",
        "## 总表",
        "",
        "| 指标 | 值 | 含义 |",
        "|---|---|---|",
        f"| 引用覆盖率 | {cited}/{n} = {cited / n:.0%} | 回答里带 `[n]` 角标的比例 |",
        (f"| 要点全覆盖率 | {full}/{len(scored)} = {full / max(1, len(scored)):.0%} "
         "| 该问的要点**全部**出现（仅统计标了要点的查询）|"),
        f"| 要点平均覆盖 | {partial:.0%} | 所有期望要点的平均命中率 |",
        f"| 闸放行率 | {gated}/{n} = {gated / n:.0%} | 证据被判为足够 |",
        "",
        "## 质量传导四格（命中 = gold 在 top3）",
        "",
        *_cell_table(cells3),
        "",
        "### 🚨 检索未命中但答案完整（top3 口径）",
        "",
        *_suspicious(cells3),
        "",
        "## 质量传导四格（命中 = gold 排第一）",
        "",
        *_cell_table(cells1),
        "",
        "### 🚨 检索未命中但答案完整（top1 口径）",
        "",
        *_suspicious(cells1),
        "",
        "## 检索命中但答案缺要点",
        "",
    ]
    unused = cells3[(True, False)] + cells1[(True, False)]
    if not unused:
        lines.append("无。")
    else:
        seen: set[str] = set()
        for row in unused:
            if row.case.query in seen:
                continue
            seen.add(row.case.query)
            missing = [
                pattern
                for pattern in row.case.points
                if not re.search(pattern, row.answer)
            ]
            if row.case.groups:
                compact = _compact(row.answer)
                missing = [
                    "|".join(alts)
                    for alts in row.case.groups
                    if not any(_compact(alt) in compact for alt in alts)
                ]
            lines.append(f"- `{row.case.query}` 缺：{missing or '（详见要点命中数）'}")

    report = "\n".join(lines)
    print()
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
