"""知识出口评测：引用覆盖率、答案要点覆盖，以及**质量传导**四格表。

**质量传导**（quality propagation）指：检索好坏会不会传到最终答案上。
把它列成 2×2，最要紧的是第四格——

|                | 答案覆盖全部要点 | 答案缺要点 |
|----------------|------------------|------------|
| **检索命中**    | 正常             | 检索给了但没用上 |
| **检索未命中**  | 🚨 **可疑**      | 兜底，符合预期 |

「检索没找到依据，答案却很完整」只有两种可能：模型瞎蒙对了，或者**编造**。
这一格必须人工看几条——这是 Ch06 加输出校验的依据。

只跑 A 出口子图（不经过主图的意图分类），这样指标只反映"检索 + 闸 + 生成"。

用法：

    uv run python scripts/eval_knowledge.py
    uv run python scripts/eval_knowledge.py --out docs/reports/knowledge-eval.md
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncio

from app.core.eval_set import load_queries
from app.core.gate import is_strong
from app.core.kb_version import kb_version
from app.graph.knowledge import build_knowledge_graph

HIT_AT = 3


def _hit(evidence: list[dict], expect_ids: list[str]) -> bool:
    ids = [item["id"] for item in evidence[:HIT_AT]]
    return any(expect in ids for expect in expect_ids)


def _covered(answer: str, points: list[str]) -> tuple[int, int]:
    """返回 (命中的要点数, 总要点数)。

    要点是**正则**（见 YAML 注释）：同一个意思常有几种说法，
    纯字面子串匹配会把「不能叠加」判成缺「不可叠加」。
    """
    if not points:
        return (0, 0)
    hits = sum(1 for pattern in points if re.search(pattern, answer))
    return (hits, len(points))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="把报告写到这个路径（markdown）")
    args = parser.parse_args()

    graph = build_knowledge_graph()
    rows: list[dict] = []

    queries = [q for q in load_queries() if not q.should_refuse]
    for index, query in enumerate(queries, start=1):
        state = await graph.ainvoke(
            {"raw_query": query.query, "resolved_query": query.query}
        )
        evidence = state.get("evidence") or []
        answer = state.get("answer", "")
        got, total = _covered(answer, query.expect_points)
        rows.append(
            {
                "query": query,
                "evidence": evidence,
                "answer": answer,
                "gated": is_strong(evidence),
                "hit": _hit(evidence, query.expect_ids),
                "points_got": got,
                "points_total": total,
                "cited": bool(state.get("citation_ids")),
            }
        )
        print(f"  {index}/{len(queries)} {query.query}", end="\r")
    print()

    n = len(rows)
    cited = sum(1 for r in rows if r["cited"])
    gated = sum(1 for r in rows if r["gated"])

    # 只有标了要点的查询才参与覆盖率统计（skip_coverage 的排除在外）
    scored = [r for r in rows if r["points_total"]]
    full = sum(1 for r in scored if r["points_got"] == r["points_total"])
    partial = sum(r["points_got"] for r in scored) / max(1, sum(r["points_total"] for r in scored))

    lines = [
        "# 知识出口评测（Ch02 Task 7）",
        "",
        (f"> 语料 kb_version=`{kb_version()}` ｜ {n} 条查询（拒答题不参与）"
         " ｜ 生成 temperature=0.2，同批数据重跑会有细微差异"),
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
    ]

    # 2×2 质量传导
    cells = {
        (True, True): [],
        (True, False): [],
        (False, True): [],
        (False, False): [],
    }
    for r in rows:
        if r["points_total"]:
            covered_all = r["points_got"] == r["points_total"]
            cells[(r["hit"], covered_all)].append(r)

    lines += [
        "",
        "## 质量传导四格（检索命中 × 答案覆盖全部要点）",
        "",
        "|  | 答案覆盖全部要点 | 答案缺要点 |",
        "|---|---|---|",
        (f"| **检索命中** | {len(cells[(True, True)])} | {len(cells[(True, False)])} |"),
        (f"| **检索未命中** | 🚨 {len(cells[(False, True)])} | {len(cells[(False, False)])} |"),
        "",
    ]

    suspicious = cells[(False, True)]
    if suspicious:
        lines += [
            "### 🚨 检索未命中但答案完整——必须人工核验",
            "",
            "检索没找到依据、答案却把要点写全了。只有两种可能：蒙对了，或者编造。",
            "",
        ]
        for r in suspicious:
            lines.append(f"- `{r['query'].query}`")
            lines.append(f"  - 期望：{' / '.join(r['query'].expect_ids)}")
            lines.append(f"  - 命中：{[e['id'] for e in r['evidence'][:HIT_AT]]}")
            lines.append(f"  - 回答：{r['answer'][:150]}")
    else:
        lines += ["**四格中最危险的一格为空**：没有出现「检索没找到、答案却很完整」的情况。"]

    unused = cells[(True, False)]
    if unused:
        lines += ["", "### 检索命中但答案缺要点", ""]
        for r in unused:
            missing = [p for p in r["query"].expect_points if p not in r["answer"]]
            lines.append(f"- `{r['query'].query}` 缺：{missing}")

    report = "\n".join(lines)
    print()
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
