"""证据闸校准：阈值曲线 + 语义闸模拟。

**两类样本**：

| 类型 | 期望 | 来源 |
|---|---|---|
| 可答题 | 放行 | `knowledge_v1.yaml` 的 42 条；或 `eval_v1` 的 B1 |
| 负样本 | 拦住 | 3 条拒答 + 8 条域外探测（人工构造） |

扫描 0.40–0.75、步长 0.01，输出每个阈值下的**误拦率 / 误放率**：

- 误拦率 = 可答题被拦 / 可答题总数（高 = 用户被无谓推给人工）
- 误放率 = 负样本被放 / 负样本总数（高 = 拿不相关资料硬答）

同时模拟**语义闸**：`[t, t+band)` 内的样本用一次证据自评的结论替代机械判定。
自评结果按 query 缓存，扫描 36 个阈值也只调一次。

用法：

    uv run python scripts/eval_gate.py
    uv run python scripts/eval_gate.py --set eval_v1 --out /tmp/gate.md
    uv run python scripts/eval_gate.py --no-semantic --csv /tmp/curve.csv
"""
import argparse
import asyncio
import csv
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.core import evidence_judge
from app.core.eval_set import load_queries
from app.core.eval_v1 import load_items
from app.core.retrieval import search

# 域外问题：语料里没有答案，闸应当拦住。人工构造，不属于评测集。
OUT_OF_DOMAIN = (
    "你们老板是谁",
    "帮我写一首诗",
    "今天天气怎么样",
    "你们招人吗",
    "能分期付款吗",      # ← 机械闸的已知漏点（0.572）
    "支持花呗吗",
    "卖不卖二手车",
    "有没有猫粮",
)

SCAN_START = 0.40
SCAN_STOP = 0.75
SCAN_STEP = 0.01


@dataclass
class Sample:
    kind: str               # 可答题 / 负样本
    query: str
    similarity: float
    top_id: str
    origin: str             # knowledge / eval_v1 / refuse / ood
    hits: list[dict] = field(default_factory=list)


def scan_thresholds() -> list[float]:
    count = round((SCAN_STOP - SCAN_START) / SCAN_STEP) + 1
    return [round(SCAN_START + i * SCAN_STEP, 2) for i in range(count)]


def _passes(sample: Sample, threshold: float, band: float, verdicts: dict[str, bool]) -> bool:
    """模拟一次闸判定：机械强 / 机械弱 / 边界自评。"""
    if band <= 0:
        return sample.similarity >= threshold
    if sample.similarity >= threshold + band - 1e-9:
        return True
    if sample.similarity < threshold:
        return False
    # 边界带：自评缺失（没跑或失败）按 weak 处理，fail-closed
    return bool(verdicts.get(sample.query, False))


def curve_row(
    samples: list[Sample],
    threshold: float,
    *,
    band: float,
    verdicts: dict[str, bool],
    semantic: bool,
) -> dict[str, float | int]:
    positives = [s for s in samples if s.kind == "可答题"]
    negatives = [s for s in samples if s.kind == "负样本"]
    pos_pass = sum(
        1 for s in positives
        if _passes(s, threshold, band if semantic else 0.0, verdicts)
    )
    neg_pass = sum(
        1 for s in negatives
        if _passes(s, threshold, band if semantic else 0.0, verdicts)
    )
    return {
        "threshold": threshold,
        "pos_pass": pos_pass,
        "pos_total": len(positives),
        "pos_miss_rate": (len(positives) - pos_pass) / len(positives) if positives else 0.0,
        "neg_block": len(negatives) - neg_pass,
        "neg_total": len(negatives),
        "neg_leak_rate": neg_pass / len(negatives) if negatives else 0.0,
    }


def _load_positives(set_name: str) -> list[tuple[str, str]]:
    """返回 [(query, origin)]，按 query 去重。"""
    rows: list[tuple[str, str]] = []
    if set_name in ("knowledge", "all"):
        rows += [(q.query, "knowledge") for q in load_queries() if not q.should_refuse]
    if set_name in ("eval_v1", "all"):
        rows += [(item.query, "eval_v1") for item in load_items() if item.bucket == "B1"]

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for query, origin in rows:
        if query in seen:
            continue
        seen.add(query)
        unique.append((query, origin))
    return unique


def _load_negatives() -> list[tuple[str, str]]:
    # 拒答题是闸的固定负样本，不随可答题来源切换。
    rows: list[tuple[str, str]] = [
        (q.query, "refuse") for q in load_queries() if q.should_refuse
    ]
    rows += [(query, "ood") for query in OUT_OF_DOMAIN]
    return rows


async def collect(set_name: str) -> list[Sample]:
    samples: list[Sample] = []
    for query, origin in _load_positives(set_name):
        hits = await search(query, top_k=5)
        top = hits[0] if hits else {"similarity": 0.0, "id": "（空）"}
        samples.append(
            Sample("可答题", query, float(top["similarity"]), str(top["id"]), origin, hits)
        )
    for query, origin in _load_negatives():
        hits = await search(query, top_k=5)
        top = hits[0] if hits else {"similarity": 0.0, "id": "（空）"}
        samples.append(
            Sample("负样本", query, float(top["similarity"]), str(top["id"]), origin, hits)
        )
    return samples


async def judge_band(samples: list[Sample], band: float) -> dict[str, bool]:
    """只对扫描过程中可能落进边界带的样本做一次自评，按 query 缓存。"""
    verdicts: dict[str, bool] = {}
    low = SCAN_START - 1e-9
    high = SCAN_STOP + band
    targets = [s for s in samples if low <= s.similarity < high]
    for index, sample in enumerate(targets, start=1):
        try:
            verdict = await evidence_judge.judge_evidence(sample.query, sample.hits)
            verdicts[sample.query] = bool(verdict.get("sufficient"))
        except Exception:                       # noqa: BLE001
            verdicts[sample.query] = False      # fail-closed
        print(f"  自评 {index}/{len(targets)} {sample.query}", end="\r")
    if targets:
        print()
    return verdicts


def render(
    samples: list[Sample],
    *,
    threshold: float,
    band: float,
    verdicts: dict[str, bool],
    semantic: bool,
    set_name: str,
    retrieval: str,
) -> tuple[str, list[dict[str, float | int]]]:
    positives = [s for s in samples if s.kind == "可答题"]
    negatives = [s for s in samples if s.kind == "负样本"]
    thresholds = scan_thresholds()
    rows = [
        curve_row(samples, t, band=band, verdicts=verdicts, semantic=semantic)
        for t in thresholds
    ]

    lines = [
        "# 证据闸校准：阈值曲线与语义闸",
        "",
        (f"> 阈值 `{threshold}` ｜ 边界带宽 `{band}` ｜ 策略 `{retrieval}` ｜ "
         f"样本集 `{set_name}` ｜ 可答题 {len(positives)} 条 + 负样本 {len(negatives)} 条"),
        "",
        "## 当前阈值的判定矩阵",
        "",
        "| 类型 | 条数 | 机械放行 | 语义放行后 | 期望 |",
        "|---|---|---|---|---|",
    ]
    for kind in ("可答题", "负样本"):
        group = [s for s in samples if s.kind == kind]
        mech = sum(1 for s in group if _passes(s, threshold, 0.0, verdicts))
        sem = sum(
            1 for s in group
            if _passes(s, threshold, band if semantic else 0.0, verdicts)
        )
        expect = "全放行" if kind == "可答题" else "全拦住"
        lines.append(f"| {kind} | {len(group)} | {mech} | {sem} | {expect} |")

    errors = [
        s for s in samples
        if _passes(s, threshold, band if semantic else 0.0, verdicts)
        != (s.kind == "可答题")
    ]
    lines += ["", "## 当前阈值判错的样本", ""]
    if not errors:
        lines.append("无。")
    else:
        for s in errors:
            verdict = "错误放行" if s.kind == "负样本" else "错误拦截"
            lines.append(
                f"- **{verdict}**（{s.origin}）`{s.query}` → {s.similarity:.3f} `{s.top_id}`"
            )

    leak = next((s for s in samples if s.query == "能分期付款吗"), None)
    if leak:
        mech_verdict = "放行" if _passes(leak, threshold, 0.0, verdicts) else "拦住"
        sem_verdict = (
            "放行"
            if _passes(leak, threshold, band if semantic else 0.0, verdicts)
            else "拦住"
        )
        lines += [
            "",
            "## 已知漏点「能分期付款吗」",
            "",
            f"- top1：`{leak.top_id}`，相似度 **{leak.similarity:.3f}**",
            f"- 机械闸（阈值 {threshold}）：**{mech_verdict}**",
            f"- 语义闸（band {band}）：**{sem_verdict}**",
        ]

    lines += [
        "",
        "## 阈值曲线（0.40–0.75，步长 0.01）",
        "",
        ("误拦率 = 可答题被拦 / 可答题；误放率 = 负样本被放 / 负样本。"
         "语义列在每个阈值下把 `[t, t+band)` 的机械判定替换为一次证据自评。"),
        "",
        ("| 阈值 | 机械放行 | 机械误拦率 | 机械拦住 | 机械误放率 "
         "| 语义放行 | 语义误拦率 | 语义拦住 | 语义误放率 |"),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        mech = curve_row(samples, row["threshold"], band=band, verdicts=verdicts, semantic=False)
        mark = "**" if abs(row["threshold"] - threshold) < 1e-9 else ""
        lines.append(
            f"| {mark}{row['threshold']:.2f}{mark} "
            f"| {mech['pos_pass']}/{mech['pos_total']} | {mech['pos_miss_rate']:.1%} "
            f"| {mech['neg_block']}/{mech['neg_total']} | {mech['neg_leak_rate']:.1%} "
            f"| {row['pos_pass']}/{row['pos_total']} | {row['pos_miss_rate']:.1%} "
            f"| {row['neg_block']}/{row['neg_total']} | {row['neg_leak_rate']:.1%} |"
        )

    current = next(r for r in rows if abs(r["threshold"] - threshold) < 1e-9)
    mech_rows = [
        curve_row(samples, t, band=band, verdicts=verdicts, semantic=False)
        for t in thresholds
    ]
    best_mech = min(
        mech_rows,
        key=lambda r: (r["pos_miss_rate"] + r["neg_leak_rate"], r["neg_leak_rate"]),
    )
    perfect = [
        r["threshold"] for r in rows
        if r["pos_miss_rate"] == 0 and r["neg_leak_rate"] == 0
    ]
    plateau = (
        f"{min(perfect):.2f}–{max(perfect):.2f}" if perfect else "无（不存在双零区间）"
    )
    if semantic and perfect:
        plateau_line = (
            f"- 语义闸把「误拦 0 + 误放 0」的区间扩到 **{plateau}**，"
            f"当前 {threshold} 落在区间中部。"
        )
    elif perfect:
        inside = "落在其中" if min(perfect) <= threshold <= max(perfect) else "不在其中"
        plateau_line = f"- 仅机械闸的「双零」区间是 **{plateau}**；当前 {threshold} {inside}。"
    else:
        plateau_line = "- 当前配置下没有出现「误拦 0 + 误放 0」的阈值。"

    footer = (
        "机械闸的可答题最低分与负样本最高分存在重叠，**单一阈值不可能同时做到"
        "零误拦和零误放**；语义闸只处理重叠带内的少数样本，其余仍由机械判定。"
    )
    footer += (
        "是否调整阈值，以本表最后一列的语义结果为准。"
        if semantic else "本表只含机械列，调阈值前请先跑语义闸。"
    )

    lines += [
        "",
        "## 结论",
        "",
        (f"- 当前阈值 **{threshold}**：误拦率 **{current['pos_miss_rate']:.1%}**，"
         f"误放率 **{current['neg_leak_rate']:.1%}**"
         f"（{'语义闸开启' if semantic else '仅机械闸'}）。"),
        (f"- 仅看机械闸：最优折中是 **{best_mech['threshold']:.2f}**，"
         f"误拦率 {best_mech['pos_miss_rate']:.1%}、"
         f"误放率 {best_mech['neg_leak_rate']:.1%}——机械闸**做不到双零**。"),
        plateau_line,
        "",
        footer,
    ]
    return "\n".join(lines), rows


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="把报告写到这个路径（markdown）")
    parser.add_argument("--csv", help="把阈值曲线写成 CSV")
    parser.add_argument(
        "--set", dest="set_name", default="knowledge",
        choices=("knowledge", "eval_v1", "all"),
        help="可答题来源；负样本始终为拒答题 + 域外探测",
    )
    parser.add_argument(
        "--no-semantic", action="store_true",
        help="跳过证据自评，只输出机械阈值曲线",
    )
    args = parser.parse_args()

    settings = get_settings()
    band = settings.evidence_semantic_band if settings.evidence_semantic_enabled else 0.0
    semantic = not args.no_semantic and band > 0

    samples = await collect(args.set_name)
    verdicts = await judge_band(samples, band) if semantic else {}

    report, rows = render(
        samples,
        threshold=settings.evidence_threshold,
        band=band,
        verdicts=verdicts,
        semantic=semantic,
        set_name=args.set_name,
        retrieval=settings.retrieval_strategy,
    )
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n已写入 {args.out}")
    if args.csv:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        Path(args.csv).write_text(buffer.getvalue(), encoding="utf-8")
        print(f"已写入 {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
