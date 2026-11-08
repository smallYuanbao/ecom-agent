"""终端里逐条审候选：keep / rewrite / drop，自动落盘、可中断续审。

不用 CSV/Excel：每屏一条，看完按一个键。

    uv run python scripts/review_candidates.py
    uv run python scripts/review_candidates.py --redo       # 重新审已审过的
    uv run python scripts/review_candidates.py --limit 10   # 先试 10 条

决定写到 `data/eval/generation/review/decisions.jsonl`，每条一行：
`{"candidate_id": "C0041", "decision": "keep", "note": "...", "ts": "..."}`

按键：

    k / keep     保留
    r / rewrite  保留但需要改写（会提示输入备注）
    d / drop     丢弃
    s / skip     先跳过，后面还能再遇到
    q / quit     退出，已审的自动保存
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REVIEW_DIR = ROOT / "data" / "eval" / "generation" / "review"
DEFAULT_INPUT = REVIEW_DIR / "shortlist.csv"
DEFAULT_STATE = REVIEW_DIR / "decisions.jsonl"

_ALIASES = {
    "k": "keep",
    "keep": "keep",
    "1": "keep",
    "保留": "keep",
    "r": "rewrite",
    "rewrite": "rewrite",
    "2": "rewrite",
    "改写": "rewrite",
    "d": "drop",
    "drop": "drop",
    "3": "drop",
    "删除": "drop",
    "s": "skip",
    "skip": "skip",
    "跳过": "skip",
    "q": "quit",
    "quit": "quit",
    "退出": "quit",
}


def load_shortlist(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_decisions(path: Path) -> dict[str, dict]:
    decisions: dict[str, dict] = {}
    if not path.exists():
        return decisions
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("candidate_id"):
            decisions[item["candidate_id"]] = item
    return decisions


def record_decision(path: Path, candidate_id: str, decision: str, note: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "candidate_id": candidate_id,
        "decision": decision,
        "note": note.strip(),
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse_decision(raw: str) -> str | None:
    return _ALIASES.get(raw.strip().lower())


def print_candidate(index: int, total: int, row: dict[str, str]) -> None:
    print("\n" + "=" * 78)
    print(
        f"[{index}/{total}] {row['candidate_id']}  "
        f"建议桶={row['suggested_bucket']}  来源={row['source_model']}  "
        f"批次={row['source_batch']}"
    )
    print(f"query: {row['query']}")
    if row.get("scenario"):
        print(f"场景: {row['scenario']}")
    if row.get("flags"):
        print(f"标记: {row['flags']}")
    if row.get("near_duplicate_of"):
        print(f"近重复: {row['near_duplicate_of']}")
    print("-" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description="终端逐条审候选")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--redo", action="store_true", help="重新审已审过的")
    parser.add_argument("--limit", type=int, help="只看前 N 条")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"找不到 {args.input}；先跑 shortlist_candidates.py")

    rows = load_shortlist(args.input)
    if args.limit:
        rows = rows[: args.limit]
    decisions = load_decisions(args.state)

    pending = [
        row for row in rows
        if args.redo or row["candidate_id"] not in decisions
    ]
    print(
        f"共 {len(rows)} 条，已审 {len(rows) - len(pending)} 条，"
        f"本次待审 {len(pending)} 条"
    )
    if not pending:
        print("没有待审条目；用 --redo 重新审。")
        return 0

    for index, row in enumerate(pending, start=1):
        print_candidate(index, len(pending), row)
        while True:
            raw = input("k=keep  r=rewrite  d=drop  s=skip  q=quit > ")
            decision = parse_decision(raw)
            if decision:
                break
            print("无效输入，请按 k / r / d / s / q")
        if decision == "quit":
            break
        if decision == "skip":
            print("跳过")
            continue

        note = ""
        if decision == "rewrite":
            note = input("改写备注（可空）> ")
        elif decision in {"keep", "drop"}:
            note = input("备注（可空，直接回车跳过）> ")
        record_decision(args.state, row["candidate_id"], decision, note)

    latest = load_decisions(args.state)
    counts = {"keep": 0, "rewrite": 0, "drop": 0}
    for row in rows:
        item = latest.get(row["candidate_id"])
        if item and item["decision"] in counts:
            counts[item["decision"]] += 1
    print(
        f"\n已审：keep={counts['keep']} rewrite={counts['rewrite']} "
        f"drop={counts['drop']}  未审={len(rows) - sum(counts.values())}"
    )
    print(f"决定文件：{args.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
