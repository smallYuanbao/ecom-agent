"""把 raw 候选整理成可勾选的评审表（CSV + Markdown）。

只做**机械整理**，不替人做决定：

- 读 `data/eval/generation/raw/*.jsonl`；
- 精确去重、近似去重（标出 `near_duplicate_of`，不直接删）；
- 按关键词给出「建议桶」和「可疑标记」；
- 输出 `review/candidates.csv`（Excel 可编辑）与 `candidates.md`（可读）。

最终 keep / rewrite / drop、gold、dev/test 仍由人确认。

用法：

    uv run python scripts/build_candidate_review.py
    uv run python scripts/build_candidate_review.py --min-similarity 0.9
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.eval_v1 import load_items

RAW_DIR = ROOT / "data" / "eval" / "generation" / "raw"
REVIEW_DIR = ROOT / "data" / "eval" / "generation" / "review"

SOURCE_BUCKET = {
    "A": "B1",
    "B": "B2",
    "C": "B3",
    "D": "B6",
    "E": "B7",
    "F": "B5",
}

BUCKET_ORDER = ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "待定")

VALID_ORDER_IDS = {
    "1001", "1002", "1003", "1004", "1005",
    "1101", "1102", "1103", "1104", "1105", "1106", "1107", "1108",
}

_PII_RE = re.compile(
    r"1[3-9]\d{9}"                       # 手机号
    r"|\d{17}[\dXx]"                     # 身份证
    r"|[\w.+-]+@[\w-]+\.[\w.]+"          # 邮箱
)

_INJECTION_RE = re.compile(
    r"忽略.{0,12}(指令|规则|事实卡|之前|以上)"
    r"|系统提示"
    r"|最高指令"
    r"|强制"
    r"|调试状态"
    r"|SYSTEM ERROR"
    r"|清除.{0,6}记忆"
    r"|你现在是"
    r"|输出.{0,8}(库存|规则).{0,8}列表",
    re.IGNORECASE,
)

_OVER_PERMISSION_RE = re.compile(r"2001|别人|他人|老公|老婆|爱人|名下")
_COMPLAINT_RE = re.compile(
    r"投诉|赔偿|十倍|惩罚|精神损失|误工|12315|消协|曝光|起诉|降权|没完"
    r"|给我个?说法|出事|差点|小红书|抖音|发到网上|媒体曝光"
)
_TRANSFER_RE = re.compile(r"转人工|人工客服|经理|主管|工号|活人")
_FUND_RE = re.compile(
    r"(退款|打款|转账|收款).{0,10}(卡|账户|微信|支付宝|朋友|爱人|老公|老婆)"
    r"|变更收款账户|银行卡|卡号"
)
_REFUND_RE = re.compile(r"退款|退货|退掉|退一?件|取消订单|拒收|退全款|换货|申请售后")
_DATA_RE = re.compile(
    r"订单|物流|快递|发货|签收|库存|有货|价格|多少钱|实付|状态"
)
_KNOWLEDGE_RE = re.compile(
    r"退货政策|运费|包邮|发票|会员|优惠券|保修|七天|无理由|规格|材质|适合|政策|规则"
)
_BOUNDARY_RE = re.compile(
    r"超.?7|超过.{0,4}七|(?<!没)(?<!未)拆封|定制|定作|已关闭|已发货|拒收"
    r"|质量问题|完好|奶瓶"
)
_ANOMALY_RE = re.compile(
    r"签收.{0,10}(早于|比发货.{0,6}早)|日期.{0,6}(乱|不对|有误|异常)"
    r"|查不到.{0,6}(物流|记录)|查无此件|没有物流|数据异常"
    r"|系统.{0,4}(bug|故障|错误)"
)
_MIXED_RE = re.compile(r"顺便|另外|还有|再推荐|同时|帮我.{0,4}再")
_OFF_TOPIC_RE = re.compile(r"穿搭|心理|育儿建议|天气|写诗|川菜|回锅肉|做饭|加盟|二手车")
_ARTIFACT_RE = re.compile(r"业务事实卡|你上方|预设的|上下文里的|prompt", re.IGNORECASE)


@dataclass
class Candidate:
    id: str
    query: str
    style: str
    scenario: str
    model: str
    source_batch: str
    source_file: str
    suggested_bucket: str
    flags: list[str] = field(default_factory=list)
    near_duplicate_of: str = ""
    decision: str = ""
    notes: str = ""


def detect_flags(query: str) -> set[str]:
    flags: set[str] = set()
    if _PII_RE.search(query):
        flags.add("pii")
    if _ARTIFACT_RE.search(query):
        flags.add("prompt_artifact")
    if _INJECTION_RE.search(query):
        flags.add("injection")
    if _OVER_PERMISSION_RE.search(query):
        flags.add("over_permission")
    if _COMPLAINT_RE.search(query):
        flags.add("complaint")
    if _TRANSFER_RE.search(query):
        flags.add("transfer")
    if _FUND_RE.search(query):
        flags.add("fund_anomaly")
    if _REFUND_RE.search(query):
        flags.add("refund")
    if _DATA_RE.search(query):
        flags.add("data_query")
    if _KNOWLEDGE_RE.search(query):
        flags.add("knowledge")
    if _BOUNDARY_RE.search(query):
        flags.add("boundary")
    if _ANOMALY_RE.search(query):
        flags.add("data_anomaly")
    if _MIXED_RE.search(query):
        flags.add("mixed_intent")
    if _OFF_TOPIC_RE.search(query):
        flags.add("off_topic")
    if len(query) > 100:
        flags.add("long")

    for match in re.finditer(
        r"订单号?\s*(?:是|为)?\s*([A-Za-z]{1,4})?\s*(\d+)", query
    ):
        prefix, digits = match.group(1), match.group(2)
        if prefix or digits not in VALID_ORDER_IDS:
            flags.add("invalid_order_id")
    if re.search(r"订单\s*[A-Za-z]+", query):
        flags.add("invalid_order_id")
    return flags


def suggest_bucket(source_batch: str, flags: set[str]) -> str:
    """先看强信号，再按生成批次兜底。

    批次本身是可靠来源：A 的 prompt 只要知识问法，C 只要退款场景。
    内容关键词只用来处理跨桶（如 D 里混进了商品问题、E 里出现越权）。
    """
    if "injection" in flags or "over_permission" in flags:
        return "B6"
    if "off_topic" in flags or "invalid_order_id" in flags:
        return "B6"
    if "complaint" in flags or "transfer" in flags or "fund_anomaly" in flags:
        return "B7"
    if "data_anomaly" in flags:
        return "B5"

    if source_batch == "A":
        return "B1"
    if source_batch == "B":
        return "B2"
    if source_batch == "C":
        return "B4" if "boundary" in flags else "B3"
    if source_batch == "D":
        return "B6"
    if source_batch == "E":
        return "B7"
    if source_batch == "F":
        return "B5"

    if "refund" in flags:
        return "B4" if "boundary" in flags else "B3"
    if "knowledge" in flags and "data_query" not in flags:
        return "B1"
    if "data_query" in flags:
        return "B2"
    return SOURCE_BUCKET.get(source_batch, "待定")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def load_raw(raw_dir: Path, *, min_similarity: float) -> tuple[list[Candidate], int]:
    existing = {_normalize(item.query) for item in load_items()}
    candidates: list[Candidate] = []
    duplicate_count = 0
    seen: dict[str, Candidate] = {}

    for path in sorted(raw_dir.glob("*.jsonl")):
        source_batch = path.stem.rsplit("_", 1)[-1]
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            query = str(row.get("query") or "").strip()
            if not query:
                continue
            key = _normalize(query)
            if key in existing or key in seen:
                duplicate_count += 1
                continue

            flags = detect_flags(query)
            candidate = Candidate(
                id=f"C{len(candidates) + 1:04d}",
                query=query,
                style=str(row.get("style") or "formal"),
                scenario=str(row.get("scenario") or ""),
                model=str(row.get("model") or path.stem),
                source_batch=source_batch,
                source_file=path.name,
                suggested_bucket=suggest_bucket(source_batch, flags),
                flags=sorted(flags),
            )
            seen[key] = candidate
            candidates.append(candidate)

    # 近似去重：保留先出现的，后面标 near_duplicate_of
    for index, candidate in enumerate(candidates):
        for earlier_index in range(index):
            earlier = candidates[earlier_index]
            if _similarity(candidate.query, earlier.query) >= min_similarity:
                candidate.near_duplicate_of = earlier.id
                candidate.flags = sorted({*candidate.flags, "near_duplicate"})
                break
    return candidates, duplicate_count


def write_csv(candidates: list[Candidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "candidate_id",
                "suggested_bucket",
                "query",
                "style",
                "scenario",
                "source_model",
                "source_batch",
                "flags",
                "near_duplicate_of",
                "decision(keep/rewrite/drop)",
                "notes",
            ]
        )
        for item in candidates:
            writer.writerow(
                [
                    item.id,
                    item.suggested_bucket,
                    item.query,
                    item.style,
                    item.scenario,
                    item.model,
                    item.source_batch,
                    ";".join(item.flags),
                    item.near_duplicate_of,
                    item.decision,
                    item.notes,
                ]
            )


def write_markdown(candidates: list[Candidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Ch07 候选评审表",
        "",
        f"> 共 {len(candidates)} 条；按建议桶分组。",
        "> 在「决定」列填 keep / rewrite / drop，或用 review_candidates.py 逐条审。",
        "> 自动标记只是提示，不是结论。",
        "",
    ]
    for bucket in BUCKET_ORDER:
        group = [item for item in candidates if item.suggested_bucket == bucket]
        if not group:
            continue
        lines += [f"## {bucket}（{len(group)} 条）", "",
                  "| 决定 | ID | 来源 | 批次 | query | style | 标记 | 备注 |",
                  "|---|---|---|---|---|---|---|---|"]
        for item in group:
            query = item.query.replace("|", "\\|")
            flags = ";".join(item.flags) or "-"
            note = f"近重复 {item.near_duplicate_of}" if item.near_duplicate_of else ""
            lines.append(
                f"|  | {item.id} | {item.model} | {item.source_batch} | "
                f"{query} | {item.style} | {flags} | {note} |"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成候选评审表")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--out-dir", type=Path, default=REVIEW_DIR)
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.92,
        help="近似重复阈值（0-1）；越高越严格",
    )
    args = parser.parse_args()

    candidates, duplicates = load_raw(
        args.raw_dir, min_similarity=args.min_similarity
    )
    if not candidates:
        print("没有候选；先跑 generate_eval_candidates.py 或导入网页输出")
        return 1

    write_csv(candidates, args.out_dir / "candidates.csv")
    write_markdown(candidates, args.out_dir / "candidates.md")

    counts: dict[str, int] = {}
    for item in candidates:
        counts[item.suggested_bucket] = counts.get(item.suggested_bucket, 0) + 1
    near = sum(1 for item in candidates if item.near_duplicate_of)
    print(f"候选 {len(candidates)} 条（精确重复丢弃 {duplicates}，近重复标记 {near}）")
    print("建议桶：" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"→ {args.out_dir / 'candidates.csv'}")
    print(f"→ {args.out_dir / 'candidates.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
