"""Ch03 跑分：简单组 / 困难组 / 金额对拍。

三张表分别回答三个问题：

| 表 | 问题 |
|---|---|
| 简单组 | 模型有没有把规则已经做对的事搞坏 |
| 困难组 | 模型能不能补上规则的窟窿 |
| 金额 | `compute_amount` 算得对不对 |

**每张表都同时给三列**：

| 列 | 含义 | 用途 |
|---|---|---|
| 模型 | `judge` 的原始判定 | **验收对象**（spec 验收项 9） |
| 规则 | `judge_eligibility` 的结论 | 基线；说明模型值不值得用 |
| 流水线 | 模型 + `compliance` 后的最终结论 | 诊断；受规则质量影响 |

验收卡"模型"，因为流水线同时受规则质量影响——混在一起会分不清
"规则错了"还是"模型错了"。流水线低于模型时，脚本会点明差在规则。

用法：

    uv run python scripts/eval_refund.py              # 三张表
    uv run python scripts/eval_refund.py --skip-model # 只跑金额对拍（不调模型）

需要 `CHAT_API_KEY` + Chroma + Ollama。缺任何一样，脚本会明确报错而不是跑出假分数。
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.core.llm import LLMFormatError, structured_call
from app.core.prompts import JUDGE_PARAMETERS, JUDGE_TOOL_NAME, judge_messages
from app.core.retrieval import search
from app.graph.rules_exit import (
    CONFIDENCE_MIN,
    REASON_TEXT,
    _clean_judgement,
    _to_order_snapshot,
    _to_request,
)
from app.rules.eligibility import judge_eligibility
from app.rules.refund import Judgement, compute_amount

EVAL_DIR = ROOT / "data" / "eval"
GOLD_SIMPLE = EVAL_DIR / "refund_gold_20.json"
GOLD_HARD = EVAL_DIR / "refund_gold_hard_10.json"
CONTRACTS = EVAL_DIR / "refund_10.json"

ORDER_KEYS = (
    "order_id", "status", "amount", "quantity", "category",
    "created_at", "paid_at", "shipped_at", "signed_at",
)


# ── 标注归一化 ──────────────────────────────────────────────


def as_label(result: dict | None) -> dict:
    """模型输出 → 与 `expected` 同构的标注形状。

    `None`（格式错）和低置信度都算"应转人工"——那是系统在说"我不确定"。
    """
    if result is None or result["confidence"] < CONFIDENCE_MIN:
        return {"needs_human": True}
    return {"eligible": result["eligible"], "reason_code": result["reason_code"]}


def rule_label(case: dict) -> tuple[dict, bool]:
    """规则代码的独立结论，以及"代码是否判不了"。

    第二个返回值决定 `compliance` 会不会否决模型：
    **代码返回 `None` 时不算分歧**——代码不知道答案，没资格否决。
    评测如果把这个语义搞错，会低估流水线。
    """
    order = case["order"]
    snap = _to_order_snapshot(order, order.get("packaging_opened"))
    request = _to_request(case["request"], snap.quantity)
    verdict = judge_eligibility(snap, request, now=datetime.fromisoformat(case["now"]))
    if verdict is None:
        return {"needs_human": True}, True
    return {"eligible": verdict.eligible, "reason_code": verdict.reason_code}, False


# ── 单条案例 ────────────────────────────────────────────────


async def run_judge(case: dict) -> dict | None:
    order = case["order"]
    request = case["request"]
    draft = {**request, "packaging_opened": order.get("packaging_opened")}
    query = f"退款 {REASON_TEXT.get(request['reason_code'], '退款')} {order['status']}"

    try:
        evidence = await search(query, top_k=5)
    except Exception as exc:  # noqa: BLE001 —— 单条失败不中断整轮评测
        print(f"  ! {case['id']} 政策检索失败：{exc}")
        evidence = []

    try:
        data = await structured_call(
            JUDGE_TOOL_NAME,
            JUDGE_PARAMETERS,
            judge_messages(case["query"], {k: order.get(k) for k in ORDER_KEYS}, draft, evidence),
        )
    except LLMFormatError:
        return None
    return _clean_judgement(data)


async def evaluate_cases(path: Path, *, verbose: bool = True) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for index, case in enumerate(data["cases"], start=1):
        model = await run_judge(case)
        expected = case["expected"]
        rule, rule_none = rule_label(case)
        row = {
            "id": case["id"],
            "group": case.get("group"),
            "expected": expected,
            "model": as_label(model),
            "rule": rule,
            "rule_none": rule_none,
            "raw": model,
        }
        rows.append(row)
        if verbose:
            mark = "✓" if row["model"] == expected else "✗"
            print(f"  [{index}/{len(data['cases'])}] {mark} {case['id']}")
    return rows


# ── 统计 ────────────────────────────────────────────────────


def _is_eligible(label: dict) -> bool:
    return label.get("eligible") is True


def _is_human(label: dict) -> bool:
    return label.get("needs_human") is True


def pipeline_label(row: dict) -> dict:
    """模型判定经过 `compliance` 之后的**最终结论**。

    - 模型自己拿不准 → 转人工
    - **代码也判不了（`rule_none`）→ 不否决**，采用模型结论
    - 两边结论不一致 → 转人工（代码否决）
    - 一致 → 放行

    这才是用户实际看到的结果。注意它有**双向代价**：
    模型判错时被拦下（好），规则判错时模型答对也会被升级成转人工（坏）。
    """
    if _is_human(row["model"]):
        return {"needs_human": True}
    if row["rule_none"]:
        return row["model"]
    return row["model"] if row["model"] == row["rule"] else {"needs_human": True}


def summary(rows: list[dict]) -> dict:
    total = len(rows)
    model_ok = sum(1 for r in rows if r["model"] == r["expected"])
    rule_ok = sum(1 for r in rows if r["rule"] == r["expected"])
    pipeline_ok = sum(1 for r in rows if pipeline_label(r) == r["expected"])
    # 只有"模型给了结论、代码也给了结论、且不一致"才算分歧。
    # 模型自己拿不准时不会进 compliance；代码判不了时不否决。
    divergence = sum(
        1
        for r in rows
        if not _is_human(r["model"]) and not r["rule_none"] and r["model"] != r["rule"]
    )

    deny = [r for r in rows if _is_eligible(r["expected"]) is False and not _is_human(r["expected"])]
    allow = [r for r in rows if _is_eligible(r["expected"])]
    wrong_release = sum(1 for r in deny if _is_eligible(r["model"]))
    wrong_block = sum(1 for r in allow if r["model"].get("eligible") is False)
    overclaim = sum(1 for r in rows if _is_human(r["expected"]) and not _is_human(r["model"]))

    return {
        "total": total,
        "model_ok": model_ok,
        "rule_ok": rule_ok,
        "pipeline_ok": pipeline_ok,
        "divergence": divergence,
        "wrong_release": wrong_release,
        "deny_total": len(deny),
        "wrong_block": wrong_block,
        "allow_total": len(allow),
        "overclaim": overclaim,
    }


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


def print_case_failures(rows: list[dict]) -> None:
    misses = [r for r in rows if pipeline_label(r) != r["expected"]]
    if not misses:
        return
    print("  流水线未命中：")
    for row in misses:
        print(
            f"    {row['id']}  期望={row['expected']}  "
            f"模型={row['model']}  规则={row['rule']}  流水线={pipeline_label(row)}"
        )


def print_group(title: str, rows: list[dict], *, full: bool = True) -> None:
    stats = summary(rows)
    print(f"\n── {title}（{stats['total']}）" + "─" * 40)
    print(
        f"  模型 {stats['model_ok']}/{stats['total']} = "
        f"{_pct(stats['model_ok'], stats['total'])}"
        f"    规则 {stats['rule_ok']}/{stats['total']} = "
        f"{_pct(stats['rule_ok'], stats['total'])}"
        f"    流水线 {stats['pipeline_ok']}/{stats['total']} = "
        f"{_pct(stats['pipeline_ok'], stats['total'])}"
    )
    if full:
        print(
            f"  错放率 {_pct(stats['wrong_release'], stats['deny_total'])}"
            f"（{stats['wrong_release']}/{stats['deny_total']}）"
            f"    错拦率 {_pct(stats['wrong_block'], stats['allow_total'])}"
            f"（{stats['wrong_block']}/{stats['allow_total']}）"
            f"    该转却给结论 {stats['overclaim']}"
        )
        print(
            f"  模型 vs 规则分歧 {stats['divergence']}/{stats['total']} = "
            f"{_pct(stats['divergence'], stats['total'])}"
        )
    print_case_failures(rows)


# ── 金额对拍 ────────────────────────────────────────────────


def evaluate_amounts() -> tuple[int, int]:
    data = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    passed = 0
    print(f"\n── 金额对拍（{len(data['cases'])}）" + "─" * 40)
    for case in data["cases"]:
        order = case["order"]
        # 合同只给判定需要的字段，其余用哨兵值补全
        snap = _to_order_snapshot(
            {**order, "order_id": case["id"], "category": ""}, None
        )
        request = _to_request({**case["request"], "reason_code": "other"}, snap.quantity)
        judgement = Judgement(**case["judgement"])
        amount = compute_amount(snap, judgement, request)
        ok = amount == case["expected_amount"]
        passed += ok
        mark = "✓" if ok else "✗"
        print(
            f"  {mark} {case['id']}  {case['note']}  "
            f"期望={case['expected_amount']} 实得={amount}"
        )
    print(f"  {passed}/{len(data['cases'])} 一致")
    return passed, len(data["cases"])


# ── 主流程 ──────────────────────────────────────────────────


async def main() -> int:
    parser = argparse.ArgumentParser(description="Ch03 退款评测")
    parser.add_argument("--skip-model", action="store_true", help="只跑金额对拍")
    args = parser.parse_args()

    amount_ok, amount_total = evaluate_amounts()

    if args.skip_model:
        return 0 if amount_ok == amount_total else 1

    if not get_settings().chat_api_key:
        print("\n缺少 CHAT_API_KEY，无法跑判定评测。", file=sys.stderr)
        return 2

    print("\n跑简单组（会逐条调模型，约 1 分钟）……")
    simple = await evaluate_cases(GOLD_SIMPLE)
    print("\n跑困难组……")
    hard = await evaluate_cases(GOLD_HARD)

    print_group("简单组", simple)
    print_group("困难组 A（规则判不了）", [r for r in hard if r["group"] == "A"])
    print_group("困难组 B（规则判错）", [r for r in hard if r["group"] == "B"])

    hard_stats = summary(hard)
    simple_stats = summary(simple)
    print("\n── 困难组合计 ─────────────────────────────")
    print(
        f"  模型 {hard_stats['model_ok']}/10"
        f"    规则 {hard_stats['rule_ok']}/10"
        f"    流水线 {hard_stats['pipeline_ok']}/10（下限 7/10）"
    )

    # ── 门槛 ────────────────────────────────────────────────
    # 验收对象是 `judge`（spec 验收项 9），所以卡**模型原始判定**。
    # 流水线是诊断指标：它同时受规则质量影响，混进验收会掩盖"规则错了还是模型错了"。
    checks = [
        ("简单组模型 ≥ 90%", simple_stats["model_ok"] / len(simple) >= 0.9),
        ("困难组模型 ≥ 7/10", hard_stats["model_ok"] >= 7),
        ("金额 10/10", amount_ok == amount_total),
    ]

    print(
        f"\n  诊断：流水线最终结论 {hard_stats['pipeline_ok']}/10"
        f"（模型 {hard_stats['model_ok']}/10）"
    )
    if hard_stats["pipeline_ok"] < hard_stats["model_ok"]:
        print(
            "  差在规则：规则判错时，compliance 会把模型的正确答案升级成转人工。"
            "修规则表才能释放这部分覆盖。"
        )
    print("\n── 验收 ──────────────────────────────────")
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
    return 0 if all(passed for _, passed in checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
