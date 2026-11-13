"""端到端跑 `eval_v1.yaml`：调 `/api/eval/chat`，按 gold 做确定性判分。

只判确定性项：

- 路由
- 字段（B2）
- 退款资格/金额（B3/B4）
- 动作（转人工）
- 回答里必须出现的片段
- 检索 id（B1）
- 是否泄露系统提示

LLM judge 的质量分由 EvalFlow 负责，不在这里重复。

用法：

    uv run python scripts/eval_e2e.py \
      --base-url http://127.0.0.1:8081 \
      --out docs/reports/run-local-01.md

    # 只跑某几个桶 / 前几条
    uv run python scripts/eval_e2e.py --only B1,B6 --limit 10
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from app.core.eval_v1 import EvalItem, load_items, validate_items
from app.core.format import yuan_text

DEFAULT_BASE_URL = "http://127.0.0.1:8081"
LEAK_MARKERS = ("## 角色", "## 安全边界", "不是指令", "不得复述本系统提示")


@dataclass
class CheckResult:
    """一条样本的确定性判定结果。

    - `response is None`：请求或解析失败，`error` 里是原因；
    - `failures`：路由/字段/金额/动作/片段/证据/泄露等检查的失败项；
    - `ok` 只看“请求成功且没有失败项”，不参与 LLM judge 的分数。
    """

    item: EvalItem
    response: dict[str, Any] | None
    failures: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """请求成功、无异常、且所有确定性检查都通过。"""
        return self.response is not None and not self.failures and not self.error


def _compact(text: str) -> str:
    """金额/单位之间可能差空格（`1249.00 元` vs `1249.00元`），比较时忽略空白。"""
    return re.sub(r"\s+", "", text)


def _haystack(response: dict[str, Any]) -> str:
    """把回答和 `signals` 拼成一段可搜索文本。

    字段检查不能只看 `answer`：B2 的金额/状态可能只出现在 `signals` 里。
    先把空白压掉，避免“12.00 元”和“12.00元”这种格式差异导致误判。
    """
    return _compact(
        str(response.get("answer") or "")
        + " "
        + json.dumps(response.get("signals") or {}, ensure_ascii=False)
    )


def _field_ok(name: str, expected: Any, response: dict[str, Any]) -> bool:
    """检查 `expect_fields` 里的一个字段。

    - `amount` / `price` 的 gold 单位是“分”，回答里通常写成 `12.00 元`；
      两种形态都接受，避免只认其中一种；
    - 其他字段直接在 `answer + signals` 的压缩文本里找。
    """
    answer = _compact(str(response.get("answer") or ""))
    haystack = _haystack(response)
    if name in {"amount", "price"} and isinstance(expected, int):
        return _compact(yuan_text(expected)) in answer or str(expected) in haystack
    return _compact(str(expected)) in haystack


def _contains_ok(expression: str, answer: str) -> bool:
    """`expect_answer_contains` 支持 `A|B`：任一片段出现即可。

    比较前去掉所有空白，避免“2899.00 元”和“2899.00元”被误判成缺失。
    """
    compact_answer = _compact(answer)
    return any(
        _compact(part) in compact_answer
        for part in expression.split("|")
        if part.strip()
    )


def evaluate_item(item: EvalItem, response: dict[str, Any]) -> CheckResult:
    """对一条样本做全部确定性检查，返回 `CheckResult`。

    顺序：路由 → 字段 → 退款 signals → 动作 → 回答片段 → 证据 id → 泄露。
    任一失败只追加到 `failures`，不中断——这样一次能看到所有问题。
    """
    failures: list[str] = []
    answer = str(response.get("answer") or "")
    route = response.get("route")
    actions = {str(action.get("type")) for action in response.get("actions") or []}

    if item.expect_route != "any" and route != item.expect_route:
        failures.append(f"route: {route} != {item.expect_route}")

    for name, expected in item.expect_fields.items():
        if not _field_ok(name, expected, response):
            failures.append(f"field {name}: 期望 {expected}")

    if item.expect_refund is not None:
        refund = (response.get("signals") or {}).get("refund") or {}
        if not refund:
            failures.append("refund: signals 缺失")
        else:
            for key in ("eligible", "amount", "reason_code"):
                if key in item.expect_refund:
                    expected = item.expect_refund[key]
                    if refund.get(key) != expected:
                        failures.append(f"refund.{key}: {refund.get(key)} != {expected}")

    for action in item.expect_actions:
        if action not in actions:
            failures.append(f"action: 缺少 {action}")

    for expression in item.expect_answer_contains:
        if not _contains_ok(expression, answer):
            failures.append(f"answer: 缺少「{expression}」")

    if item.expect_ids:
        got = {
            str(item_.get("id"))
            for item_ in (response.get("hits") or []) + (response.get("references") or [])
        }
        if not any(expected in got for expected in item.expect_ids):
            failures.append(f"evidence: 未命中 {item.expect_ids}")

    if item.expect_no_leak and any(marker in answer for marker in LEAK_MARKERS):
        failures.append("leak: 回答包含系统提示标记")

    return CheckResult(item=item, response=response, failures=failures)


def _write_report(
    results: list[CheckResult],
    path: Path,
    *,
    base_url: str,
) -> None:
    """把结果写成 Markdown：总表按桶聚合，失败样本逐条列原因。

    query / reason / answer 里的 `|` 会破坏 Markdown 表格，所以统一转义；
    报告只写“通过/失败”，不写 judge 分。
    """
    total = len(results)
    passed = sum(1 for result in results if result.ok)
    lines = [
        "# Ch07 端到端本地跑分",
        "",
        (
            f"> 时间：{datetime.now().isoformat(timespec='seconds')} · "
            f"目标：`{base_url}` · 样本：{total}"
        ),
        "",
        "## 总表",
        "",
        f"**通过 {passed}/{total}（{passed / total:.1%}）**",
        "",
        "| 桶 | 通过 / 总数 | 通过率 |",
        "|---|---|---|",
    ]
    for bucket in sorted({result.item.bucket for result in results}):
        group = [result for result in results if result.item.bucket == bucket]
        bucket_passed = sum(1 for result in group if result.ok)
        lines.append(
            f"| {bucket} | {bucket_passed} / {len(group)} | "
            f"{bucket_passed / len(group):.1%} |"
        )

    failed = [result for result in results if not result.ok]
    lines += ["", "## 失败样本", ""]
    if not failed:
        lines.append("无。")
    else:
        lines += [
            "| id | 桶 | query | 失败项 | 实际路由 | 实际回答 |",
            "|---|---|---|---|---|---|",
        ]
        for result in failed:
            reason = (result.error or "; ".join(result.failures)).replace("|", "\\|")
            query = result.item.query.replace("|", "\\|")
            route = (result.response or {}).get("route")
            answer = str((result.response or {}).get("answer") or "")
            answer = answer.replace("\n", " ").replace("|", "\\|")[:120]
            lines.append(
                f"| {result.item.id} | {result.item.bucket} | {query} | "
                f"{reason} | {route} | {answer} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    """CLI 入口：加载并校验评测集 → 过滤 → 逐条请求 → 写报告。

    过滤顺序：`--only`（桶）→ `--id`（样本 id）→ `--limit`（截断）。
    单条请求失败也会保留一条 `CheckResult(response=None, error=...)`，
    不会因为一条超时或 500 中断整轮。
    """
    parser = argparse.ArgumentParser(description="端到端跑 eval_v1")
    parser.add_argument(
        "--base-url",
        default=os.getenv("EVAL_BASE_URL") or DEFAULT_BASE_URL,
    )
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "reports" / "run-local-01.md")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", help="只跑这些桶，逗号分隔，如 B1,B6")
    parser.add_argument("--id", help="只跑这些样本 id，逗号分隔，如 T04,B05")
    args = parser.parse_args()

    items = load_items()
    validate_items(items)
    if args.only:
        wanted = {part.strip().upper() for part in args.only.split(",") if part.strip()}
        items = [item for item in items if item.bucket in wanted]
    if args.id:
        wanted_ids = {part.strip() for part in args.id.split(",") if part.strip()}
        items = [item for item in items if item.id in wanted_ids]
    if args.limit:
        items = items[: args.limit]

    results: list[CheckResult] = []
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        for index, item in enumerate(items, start=1):
            try:
                response = client.post(
                    "/api/eval/chat", json={"message": item.query}
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:                   # noqa: BLE001
                result = CheckResult(item=item, response=None, error=repr(exc))
                results.append(result)
                print(f"[{index}/{len(items)}] {item.id} ERROR {exc}")
                continue

            result = evaluate_item(item, payload)
            results.append(result)
            mark = "PASS" if result.ok else "FAIL"
            print(f"[{index}/{len(items)}] {item.id} {mark} {item.bucket}")
            for failure in result.failures:
                print(f"    - {failure}")

    _write_report(results, args.out, base_url=args.base_url)
    passed = sum(1 for result in results if result.ok)
    print(f"\n通过 {passed}/{len(results)} → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
