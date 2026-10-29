"""检索策略对比：dense / bm25 / hybrid，同一批查询。

**只报点估计是没有意义的。** 45 条查询 / 15 道基础题，置信区间宽到 ±12~20pp，
所以这里同时输出：

- hit@3 的 **Wilson 95% 置信区间**
- 相对 dense 的**不一致对数**（McNemar 的原料）——单向差 6 条以上才算显著
- 每种策略**失败的具体查询**——45 条的汇总数字信息量低，失败样本信息量高

用法：

    uv run python scripts/eval_retrieval.py
    uv run python scripts/eval_retrieval.py --out docs/reports/retrieval-eval.md
"""
import argparse
import asyncio
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/eval_retrieval.py` 直接跑

from app.core.eval_set import EvalQuery, load_queries
from app.core.retrieval import search

STRATEGIES = ("dense", "bm25", "hybrid")
TOP_K = 5
HIT_AT = 3


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 区间。比正态近似更稳，尤其在 p 接近 0 或 1 时。"""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


async def evaluate(strategy: str, queries: list[EvalQuery]) -> dict:
    """逐条查询跑一遍，记录排名。

    `ranks` 与 `queries` **按下标对齐**。早前用 `case_id:style` 当键，
    结果同一道题的两个 formal 问法互相覆盖，n 被悄悄少算——指标就被高估了。
    """
    ranks: list[int | None] = []
    failures: list[tuple[EvalQuery, list[str]]] = []

    for query in queries:
        hits = await search(query.query, top_k=TOP_K, strategy=strategy)
        ids = [hit["id"] for hit in hits]
        matched = [ids.index(e) + 1 for e in query.expect_ids if e in ids]
        rank = min(matched) if matched else None
        ranks.append(rank)
        if rank is None or rank > HIT_AT:
            failures.append((query, ids))

    return {"strategy": strategy, "ranks": ranks, "failures": failures}


def summarize(result: dict, queries: list[EvalQuery]) -> dict:
    ranks = result["ranks"]
    n = len(ranks)
    assert n == len(queries), f"{result['strategy']}: ranks({n}) 与 queries({len(queries)}) 对不齐"
    hits = sum(1 for r in ranks if r is not None and r <= HIT_AT)
    low, high = wilson_interval(hits, n)
    mrr = sum(1.0 / r for r in ranks if r) / n

    by_bucket: dict[str, list] = defaultdict(list)
    by_style: dict[str, list] = defaultdict(list)
    for query, rank in zip(queries, ranks, strict=True):
        ok = rank is not None and rank <= HIT_AT
        by_bucket[query.bucket].append(ok)
        by_style[query.style].append(ok)

    return {
        "strategy": result["strategy"],
        "n": n,
        "hit": hits,
        "hit_at_3": hits / n,
        "ci": (low, high),
        "mrr": mrr,
        "by_bucket": {k: (sum(v), len(v)) for k, v in by_bucket.items()},
        "by_style": {k: (sum(v), len(v)) for k, v in by_style.items()},
        "failures": result["failures"],
    }


def discordance(base: dict, other: dict) -> tuple[int, int]:
    """返回 (base 命中而 other 未命中, other 命中而 base 未命中)。

    McNemar 只关心这两个数：6:0 才勉强显著，8:3 仍不显著。
    """
    a_only = b_only = 0
    for base_rank, other_rank in zip(base["ranks"], other["ranks"], strict=True):
        base_ok = base_rank is not None and base_rank <= HIT_AT
        other_ok = other_rank is not None and other_rank <= HIT_AT
        if base_ok and not other_ok:
            a_only += 1
        elif other_ok and not base_ok:
            b_only += 1
    return a_only, b_only


def render(summaries: list[dict], raws: dict[str, dict], n_cases: int) -> str:
    lines = [
        "# 检索策略对比（Ch02 Task 5）",
        "",
        (f"> 评测集：`data/eval/knowledge_v1.yaml`（{n_cases} 道基础题展开的查询，"
         "拒答题不计入检索指标）"),
        f"> 指标：hit@{HIT_AT}，取回 {TOP_K} 条候选",
        ("> **本文件是自动生成的原始数据**，决策与解读见 "
         "`docs/superpowers/specs/ch02-walking-skeleton-design.md`「关键决策记录」。"),
        "",
        "## 总表",
        "",
        "| 策略 | hit@3 | 95% CI | MRR | 相对 dense 不一致对数 |",
        "|---|---|---|---|---|",
    ]
    for summary in summaries:
        low, high = summary["ci"]
        if summary["strategy"] == "dense":
            diff = "—"
        else:
            # 不一致对数要基于逐条排名算，summary 里没有 ranks
            a_only, b_only = discordance(raws["dense"], raws[summary["strategy"]])
            verdict = "显著" if max(a_only, b_only) >= 6 else "不显著"
            diff = f"{a_only} : {b_only}（{verdict}）"
        lines.append(
            f"| {summary['strategy']} | {summary['hit_at_3']:.2f} "
            f"| {low:.2f}–{high:.2f} | {summary['mrr']:.3f} | {diff} |"
        )

    lines += ["", "## 分桶（命中/总数）", ""]
    buckets = sorted({b for s in summaries for b in s["by_bucket"]})
    lines.append("| 策略 | " + " | ".join(buckets) + " |")
    lines.append("|---|" + "---|" * len(buckets))
    for summary in summaries:
        cells = [
            f"{summary['by_bucket'].get(b, (0, 0))[0]}/{summary['by_bucket'].get(b, (0, 0))[1]}"
            for b in buckets
        ]
        lines.append(f"| {summary['strategy']} | " + " | ".join(cells) + " |")

    lines += ["", "## 分问法（命中/总数）", ""]
    styles = sorted({s for summary in summaries for s in summary["by_style"]})
    lines.append("| 策略 | " + " | ".join(styles) + " |")
    lines.append("|---|" + "---|" * len(styles))
    for summary in summaries:
        cells = [
            f"{summary['by_style'].get(s, (0, 0))[0]}/{summary['by_style'].get(s, (0, 0))[1]}"
            for s in styles
        ]
        lines.append(f"| {summary['strategy']} | " + " | ".join(cells) + " |")

    lines += ["", "## 失败样本（这些比总分有用）", ""]
    for summary in summaries:
        if not summary["failures"]:
            continue
        lines.append(f"### {summary['strategy']}（{len(summary['failures'])} 条未命中）")
        lines.append("")
        for query, ids in summary["failures"]:
            expected = " 或 ".join(query.expect_ids) or "（应拒答）"
            lines.append(f"- `{query.query}` → 期望 `{expected}`，实际 `{ids[0] if ids else '空'}`")
        lines.append("")

    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="把报告写到这个路径（markdown）")
    args = parser.parse_args()

    # 拒答题没有 expect_id，检索指标无意义（它们由证据闸 + 生成侧评估）
    queries = [q for q in load_queries() if not q.should_refuse]

    results = {}
    for strategy in STRATEGIES:
        print(f"跑 {strategy} …", flush=True)
        results[strategy] = await evaluate(strategy, queries)

    summaries = [summarize(results[s], queries) for s in STRATEGIES]
    report = render(summaries, results, len({q.case_id for q in queries}))

    print()
    print(report)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n已写入 {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
