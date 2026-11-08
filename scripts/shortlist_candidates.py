"""从 385 条候选里挑出可人工评审的短名单（默认约 58 条）。

不是按「分数最高」选，而是按桶 + 来源模型轮转：

- 每个桶有上限，避免 B4/B6 这类大桶淹没评审；
- 同一模型的候选轮流取，保证至少看到不同模型的表达；
- 排除 `pii` / `prompt_artifact` / `near_duplicate`；
- 保留原始 `candidate_id`，方便回填。

用法：

    uv run python scripts/shortlist_candidates.py
    uv run python scripts/shortlist_candidates.py --per-bucket B1=16,B6=12,B7=12
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_candidate_review import (
    SOURCE_BUCKET,
    Candidate,
    load_raw,
    write_csv,
    write_markdown,
)

RAW_DIR = ROOT / "data" / "eval" / "generation" / "raw"
REVIEW_DIR = ROOT / "data" / "eval" / "generation" / "review"

DEFAULT_LIMITS = {
    "B1": 12,
    "B2": 6,
    "B3": 6,
    "B4": 10,
    "B5": 4,
    "B6": 10,
    "B7": 10,
}

BUCKET_FLAGS = {
    "B1": {"knowledge"},
    "B2": {"data_query"},
    "B3": {"refund"},
    "B4": {"refund", "boundary"},
    "B5": {"data_anomaly"},
    "B6": {"injection", "over_permission", "invalid_order_id", "mixed_intent", "off_topic"},
    "B7": {"complaint", "transfer", "fund_anomaly"},
}

EXCLUDE_FLAGS = {"pii", "prompt_artifact", "near_duplicate"}


def _score(candidate: Candidate, bucket: str) -> int:
    matched = len(set(candidate.flags) & BUCKET_FLAGS.get(bucket, set()))
    source_bonus = 3 if SOURCE_BUCKET.get(candidate.source_batch) == bucket else 0
    long_penalty = 2 if "long" in candidate.flags else 0
    return matched * 10 + source_bonus - long_penalty


def topic_key(candidate: Candidate, bucket: str) -> str:
    """粗粒度主题，只用来避免短名单里出现三条「起算时间」。"""
    query = candidate.query
    flags = set(candidate.flags)
    if "injection" in flags:
        return "injection"
    if "over_permission" in flags:
        return "over_permission"
    if "invalid_order_id" in flags:
        return "invalid_order_id"
    if "fund_anomaly" in flags:
        return "fund_anomaly"
    if "transfer" in flags:
        return "transfer"
    if "complaint" in flags:
        return "complaint"
    if "off_topic" in flags:
        return "off_topic"
    if "mixed_intent" in flags:
        return "mixed_intent"
    if "data_anomaly" in flags:
        return "data_anomaly"
    if bucket == "B6":
        return "other"
    if bucket == "B7":
        return "other"

    patterns = {
        "return_window": r"起算|从哪.{0,3}(算|开始)|哪天.{0,3}算|签收.{0,4}(次日|第二天)|多久",
        "not_applicable": r"不适用|哪些商品|定作|定制|音像|奶瓶|(?<!没)(?<!未)拆封",
        "return_shipping": r"运费.{0,6}(谁|承担|出)|快递费|退货运费",
        "condition": r"完好|吊牌|查验",
        "free_shipping": r"包邮|邮费|满.{0,4}元",
        "shipping_insurance": r"运费险",
        "invoice": r"发票",
        "member": r"会员|折扣",
        "coupon": r"优惠券|叠加",
        "product": r"ST-|CS-|FB-|规格|型号|适合|区别",
        "partial": r"部分|退一?件|其中.{0,4}件|两件|三件",
        "window": r"超.?7|超过.{0,4}七|一个多礼拜|半个月",
        "custom": r"定制|定作|刻字",
        "opened": r"(?<!没)(?<!未)拆封|奶瓶",
        "quality": r"质量问题|坏了|瑕疵|裂|异响|缺陷",
        "cancel": r"取消|待发货",
        "reject": r"拒收|没签收|未签收",
        "anomaly": r"签收.{0,10}(早于|比发货.{0,6}早)|日期.{0,6}(乱|不对|有误|异常)"
                   r"|查不到.{0,6}(物流|记录)|查无此件|没有物流|数据异常"
                   r"|系统.{0,4}(bug|故障|错误)",
        "stock": r"库存|有货|售罄|补货",
        "price": r"价格|多少钱|实付|优惠",
        "logistics": r"物流|快递|到哪|签收",
        "order_status": r"状态|发货",
    }
    if bucket in {"B3", "B4"}:
        order = ("window", "custom", "opened", "quality", "partial", "cancel", "reject")
    elif bucket == "B2":
        order = ("logistics", "stock", "price", "order_status")
    else:
        order = (
            "return_window", "not_applicable", "return_shipping", "condition",
            "free_shipping", "shipping_insurance", "invoice", "member", "coupon",
            "product", "anomaly", "partial", "window", "custom", "opened",
            "quality", "cancel", "reject", "stock", "price", "logistics",
            "order_status",
        )
    for name in order:
        if re.search(patterns[name], query):
            return name
    return "other"


def _interleave_by_model(items: list[Candidate], bucket: str) -> list[Candidate]:
    by_model: dict[str, list[Candidate]] = defaultdict(list)
    for item in items:
        by_model[item.model].append(item)
    for values in by_model.values():
        values.sort(key=lambda item: (-_score(item, bucket), item.id))

    result: list[Candidate] = []
    models = sorted(by_model)
    while any(by_model[model] for model in models):
        for model in models:
            if by_model[model]:
                result.append(by_model[model].pop(0))
    return result


def select_shortlist(
    candidates: list[Candidate], limits: dict[str, int]
) -> list[Candidate]:
    selected: list[Candidate] = []

    for bucket, limit in limits.items():
        pool = [
            item
            for item in candidates
            if item.suggested_bucket == bucket
            and not (set(item.flags) & EXCLUDE_FLAGS)
        ]
        by_topic: dict[str, list[Candidate]] = defaultdict(list)
        for item in pool:
            by_topic[topic_key(item, bucket)].append(item)

        queues = {
            topic: _interleave_by_model(items, bucket)
            for topic, items in by_topic.items()
        }
        topics = sorted(queues)
        chosen: list[Candidate] = []
        model_counts: dict[str, int] = defaultdict(int)
        cursor = 0
        while len(chosen) < limit and any(queues[topic] for topic in topics):
            topic = topics[cursor % len(topics)]
            queue = queues[topic]
            if queue:
                # 先保证模型均衡，再在同模型内看分数
                item = min(
                    queue,
                    key=lambda c: (
                        model_counts[c.model],
                        -_score(c, bucket),
                        c.id,
                    ),
                )
                queue.remove(item)
                model_counts[item.model] += 1
                matched = sorted(set(item.flags) & BUCKET_FLAGS.get(bucket, set()))
                item.notes = (
                    f"shortlist: topic={topic}; "
                    f"flags={','.join(matched) or 'none'}"
                )
                chosen.append(item)
            cursor += 1
        selected.extend(chosen)

    return selected


def parse_limits(raw: str | None) -> dict[str, int]:
    limits = dict(DEFAULT_LIMITS)
    if not raw:
        return limits
    for part in raw.split(","):
        key, _, value = part.partition("=")
        key = key.strip().upper()
        if key not in DEFAULT_LIMITS:
            raise SystemExit(f"未知桶 {key}；可选 {sorted(DEFAULT_LIMITS)}")
        limits[key] = int(value)
    return limits


def main() -> int:
    parser = argparse.ArgumentParser(description="生成候选短名单")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--out-dir", type=Path, default=REVIEW_DIR)
    parser.add_argument("--min-similarity", type=float, default=0.92)
    parser.add_argument(
        "--per-bucket",
        help="覆盖每桶上限，如 B1=16,B6=12,B7=12",
    )
    args = parser.parse_args()

    candidates, duplicates = load_raw(
        args.raw_dir, min_similarity=args.min_similarity
    )
    limits = parse_limits(args.per_bucket)
    shortlist = select_shortlist(candidates, limits)
    if not shortlist:
        print("没有可用的短名单")
        return 1

    write_csv(shortlist, args.out_dir / "shortlist.csv")
    write_markdown(shortlist, args.out_dir / "shortlist.md")

    counts: dict[str, int] = {}
    for item in shortlist:
        counts[item.suggested_bucket] = counts.get(item.suggested_bucket, 0) + 1
    print(f"候选 {len(candidates)} → 短名单 {len(shortlist)} 条（精确重复丢弃 {duplicates}）")
    print("短名单桶：" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"→ {args.out_dir / 'shortlist.csv'}")
    print(f"→ {args.out_dir / 'shortlist.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
