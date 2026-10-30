"""证据闸校准：看闸在不同类型问题上的判定，而不是只看一个阈值数字。

三类样本：

| 类型 | 期望 | 来源 |
|---|---|---|
| 可答题 | 放行 | 评测集（42 条） |
| 该拒答 | 拦住 | 评测集 refuse 桶（3 条） |
| 域外探测 | 拦住 | **人工补充**（不在评测集里，专门探边界） |

域外探测故意包含「能分期付款吗」——实测 0.572 会越过 0.55 的闸，
是当前单阈值方案的已知漏洞。把它摆在明面上，比藏在文档里好。

用法：

    uv run python scripts/eval_gate.py
    uv run python scripts/eval_gate.py --out docs/reports/gate-eval.md
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncio

from app.config import get_settings
from app.core.eval_set import load_queries
from app.core.retrieval import search

# 域外问题：语料里没有答案，闸应当拦住。人工构造，不属于评测集。
OUT_OF_DOMAIN = (
    "你们老板是谁",
    "帮我写一首诗",
    "今天天气怎么样",
    "你们招人吗",
    "能分期付款吗",      # ← 已知会漏过去
    "支持花呗吗",
    "卖不卖二手车",
    "有没有猫粮",
)


async def top1(query: str) -> tuple[float, str]:
    """只取 top1 的相似度与 id。**分数只算一次**，阈值再扫也只是比较。"""
    hits = await search(query, top_k=5)
    top = hits[0] if hits else {"similarity": 0.0, "id": "（空）"}
    return top["similarity"], top["id"]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="把报告写到这个路径（markdown）")
    args = parser.parse_args()

    threshold = get_settings().evidence_threshold
    queries = load_queries()

    answerable = [q for q in queries if not q.should_refuse]
    refusable = [q for q in queries if q.should_refuse]

    lines = [
        "# 证据闸校准（Ch02 Task 6）",
        "",
        (f"> 阈值 `{threshold}` ｜ 策略 `{get_settings().retrieval_strategy}` ｜ "
         f"评测集 {len(queries)} 条查询 + {len(OUT_OF_DOMAIN)} 条域外探测"),
        "",
        "## 判定矩阵",
        "",
        "| 类型 | 条数 | 放行 | 拦住 | 期望 |",
        "|---|---|---|---|---|",
    ]

    errors: list[tuple[str, str, float, str]] = []   # (类型, 查询, 分数, top1)

    # 先把所有 top1 分数采一遍（每条一次检索），再拿去做阈值扫描
    scored: list[tuple[str, str, float, str]] = []      # (类型, 查询, 分数, top1 id)
    for q in answerable:
        similarity, top_id = await top1(q.query)
        scored.append(("可答题", q.query, similarity, top_id))
    for q in refusable:
        similarity, top_id = await top1(q.query)
        scored.append(("该拒答", q.query, similarity, top_id))
    for query in OUT_OF_DOMAIN:
        similarity, top_id = await top1(query)
        scored.append(("域外", query, similarity, top_id))

    a_pass = sum(1 for kind, _, sim, _ in scored if kind == "可答题" and sim >= threshold)
    r_pass = sum(1 for kind, _, sim, _ in scored if kind == "该拒答" and sim >= threshold)
    o_pass = sum(1 for kind, _, sim, _ in scored if kind == "域外" and sim >= threshold)
    a_block = len(answerable) - a_pass
    r_block = len(refusable) - r_pass
    o_block = len(OUT_OF_DOMAIN) - o_pass

    for kind, query, similarity, top_id in scored:
        expect_pass = kind == "可答题"
        if (similarity >= threshold) != expect_pass:
            errors.append((kind, query, similarity, top_id))

    lines.append(f"| 可答题 | {len(answerable)} | {a_pass} | {a_block} | 全放行 |")
    lines.append(f"| 该拒答 | {len(refusable)} | {r_pass} | {r_block} | 全拦住 |")
    lines.append(f"| 域外探测 | {len(OUT_OF_DOMAIN)} | {o_pass} | {o_block} | 全拦住 |")

    lines += ["", "## 判错的样本", ""]
    if not errors:
        lines.append("无。")
    else:
        for kind, query, similarity, top_id in errors:
            verdict = "错误放行" if kind != "可答题" else "错误拦截"
            lines.append(f"- **{verdict}**（{kind}）`{query}` → {similarity:.3f} `{top_id}`")

    gate_pass_rate = a_pass / len(answerable) if answerable else 0
    leak_rate = (r_pass + o_pass) / (len(refusable) + len(OUT_OF_DOMAIN))
    total_negative = len(refusable) + len(OUT_OF_DOMAIN)
    candidates = sorted({sim for _, _, sim, _ in scored}, reverse=True)

    def outcome(candidate: float) -> tuple[int, int]:
        kept = sum(1 for kind, _, sim, _ in scored if kind == "可答题" and sim >= candidate)
        blocked = sum(
            1 for kind, _, sim, _ in scored if kind != "可答题" and sim < candidate
        )
        return kept, blocked

    lines += [
        "",
        "## 阈值怎么定：只有两个真正不同的结果",
        "",
        ("> 阈值只有在**跨过某个观测分数**时才改变行为。把 45+8 条样本的 top1 分数"
         "排序后挨个试，结果只有下面这些——除此之外的阈值行为完全相同。"),
        "",
        "### 负样本的分数（决定能拦住几条）",
        "",
        "| 负样本分数 | 低于它的拦住数 | 样本 |",
        "|---|---|---|",
    ]
    for kind, query, sim, _ in sorted(
        (r for r in scored if r[0] != "可答题"), key=lambda r: -r[2]
    ):
        below = sum(1 for k, _, s2, _ in scored if k != "可答题" and s2 < sim)
        lines.append(f"| {sim:.3f} | {below}/{total_negative} | ⛔ {query} |")

    options = [(c, *outcome(c)) for c in candidates]
    frontier = [
        o for o in options
        if not any(
            b[1] >= o[1] and b[2] >= o[2] and (b[1] > o[1] or b[2] > o[2])
            for b in options
        )
    ]
    lines += [
        "",
        "### Pareto 前沿（没有更优解）",
        "",
        "| 阈值 | 可答题放行 | 负样本拦住 | 代价 |",
        "|---|---|---|---|",
    ]
    for candidate, kept, blocked in sorted(frontier):
        cost = "不用代价" if kept == len(answerable) else f"少答 {len(answerable) - kept} 条"
        mark = ""
        if outcome(threshold) == (kept, blocked):
            mark = " **← 当前阈值落在这个区间**"
        lines.append(
            f"| {candidate:.3f} | {kept}/{len(answerable)} | {blocked}/{total_negative} "
            f"| {cost}{mark} |"
        )

    same_as = next((o for o in frontier if (o[1], o[2]) == outcome(threshold)), None)
    lines += [
        "",
        (f"**当前阈值 {threshold} 与 {same_as[0]:.3f} 行为完全相同**——"
         "它落在两个相邻观测分数之间，微调没有任何区别。"
         if same_as else f"**当前阈值 {threshold} 在前沿之外**。"),
        "",
        ("要改变行为，必须跨过前沿里那两个分数（"
         + " / ".join(f"{o[0]:.3f}" for o in sorted(frontier)) + "），"
         "在它们之间怎么调都一样。"),
    ]

    lines += [
        "",
        "## 结论",
        "",
        f"- 可答题放行率 **{gate_pass_rate:.1%}**（低说明阈值太严，用户会被无谓地推给人工）",
        f"- 域外/拒答漏放率 **{leak_rate:.1%}**（高说明阈值太松，会拿不相关的资料硬答）",
        "",
        ("**这两个数不可能同时最优**——实测可答题最低 0.566、域外最高 0.572，"
         "两者重叠，任何单一阈值都要在「多拒」和「多放」之间选一边。"
         "方向是 fail-closed：宁可拒，不硬答。"),
    ]

    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
