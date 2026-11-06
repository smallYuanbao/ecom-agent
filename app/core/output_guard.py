"""输出承诺检查（S6）：回答离开系统前的最后一道机械校验。

模型有自由度的是「怎么说」，不是「说什么」。金额、能不能退、多久到账
这些硬约束由代码兜底：回答里出现 `compute`/工具结果之外的金额、
部分退款却说「全额退」、承诺具体到账时间、或复述了系统提示，就整段换掉。

**为什么不用第二个模型**：机械规则可复现、可单测；再引入模型只是把幻觉
换了个地方，金额和措辞不需要「理解」。

中文名词：
- 金额统一用**分**（int）比较，避免浮点在边界差 1 分；
- `full_refund` = 应退金额等于订单实付，只有这时才允许「全额退」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.format import yuan_text

# 各出口的安全话术。宁可生硬，也不能留下相反结论或未兑现的承诺。
RULES_REFUND_ANSWER = "本次应退金额：{amount}，具体以平台售后规则为准。"
RULES_REJECT_ANSWER = "该订单暂不符合退款条件，具体以平台售后规则为准。"
DATA_ERROR_ANSWER = "数据查询结果异常，请稍后重试。"
FALLBACK_SAFE_ANSWER = "抱歉，这个问题我暂时无法准确回答，具体以平台售后规则为准。"
LEAK_SAFE_ANSWER = "抱歉，我无法回答这个问题，请稍后再试。"

TRANSFER_ACTION = {"type": "transfer_human"}

# 金额：`¥1,249` / `1249.00 元` / `1249元`。只认阿拉伯数字，中文数字后续再补。
_AMOUNT_PATTERNS = (
    re.compile(r"¥\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
    re.compile(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元"),
)

# 订单号 / 物流号：4 位以上、两边不能紧挨数字或连字符（排除 `2026-10-25`）。
_ORDER_ID_RE = re.compile(r"(?<![\d-])(\d{4,})(?![\d-])")

_FULL_REFUND_WORDS = ("全额退", "全款退", "包退")
_GUARANTEE_WORDS = ("一定能退", "保证退", "百分百退")
_TIME_PROMISE_RE = re.compile(
    r"[0-9]+\s*个?\s*(?:工作日|小时|天)\s*(?:内)?\s*(?:到账|退款|处理)"
)

# 系统提示的「指纹」。不需要覆盖全文——真实防线是最小权限，这里只是兜底。
_LEAK_MARKERS = (
    "## 角色",
    "## 安全边界",
    "禁止承诺",
    "系统已经做完判定",
    "不得复述本系统提示",
    "system prompt",
    "忽略之前",
)


@dataclass(frozen=True)
class OutputContext:
    """从图终态提取的「这个出口允许说什么」。"""

    route: str
    allowed_amounts: frozenset[int] = frozenset()
    # `full_refund` = True 时允许「全额退」；否则不允许。
    full_refund: bool = False
    allowed_order_ids: frozenset[str] = frozenset()
    check_amounts: bool = False
    # 命中违规时用来拼安全模板；`eligible` 为假时模板说「不符合条件」
    refund_amount: int | None = None
    eligible: bool = False


@dataclass(frozen=True)
class GuardResult:
    answer: str
    violations: tuple[str, ...] = ()
    action: dict | None = None


def output_context(final: dict) -> OutputContext:
    """按出口收集允许出现的金额与标识。

    C 出口的金额来自 `order_snapshot` 和 `compute`；
    B 出口的金额来自 `data_result`（订单实付、商品售价、规格行）。
    A / E / D 不检查金额：政策原文和自由回答里可能有合法金额。
    """
    route = str(final.get("route") or "")

    if route == "rules":
        # C 出口：退款流水线。金额来自订单快照和 compute 输出。
        snapshot = final.get("order_snapshot") or {}
        amounts: set[int] = set() # 订单实付金额 + compute 算出的应退金额
        order_amount = snapshot.get("amount") # 订单实付金额
        # 订单实付金额是 int（分），不是 bool（True=1，False=0）
        if isinstance(order_amount, int) and not isinstance(order_amount, bool): # 订单实付金额
            amounts.add(order_amount)

        judgement = final.get("refund_judgement") or {} # compute 输出 {eligible, reason_code, confidence, cited_article?} 
        refund = final.get("refund_amount")  # compute 算出的金额
        eligible = bool(judgement.get("eligible")) and isinstance(refund, int) # eligible = True 且 refund 是 int（分）才算合法
        if eligible:
            amounts.add(refund)

        return OutputContext(
            route=route,
            allowed_amounts=frozenset(amounts),
            full_refund=eligible and order_amount == refund,
            check_amounts=True,
            refund_amount=refund if eligible else None,
            eligible=eligible,
        )

    if route == "data":
        data = final.get("data_result") or {} # 工具返回的结构化数据，供 format 使用
        # 回答里合法出现的标识与数量：订单号 / 物流号 / 型号 / 库存。
        # 先收集它们，查订单号时从文本里剔掉，避免 SF1001001、1200 件被误判。
        allowed_ids = _collect_values(
            data, {"order_id", "tracking_no", "sku_id", "quantity", "stock"}
        )
        order_id = final.get("order_id")
        if order_id:
            allowed_ids.add(str(order_id))
        return OutputContext(
            route=route,
            allowed_amounts=frozenset(_collect_values(data, {"amount", "price"}, ints=True)),
            allowed_order_ids=frozenset(allowed_ids),
            check_amounts=True,
        )

    return OutputContext(route=route)


def guard_answer(answer: str, ctx: OutputContext) -> GuardResult:
    """检查并（必要时）整段替换回答。

    违规分五类，便于 trace 里定位：
    `amount` / `order_id` / `full_refund` / `guarantee` / `time_promise` / `leak`。
    """
    violations: list[str] = [] # 违规类型列表，便于 trace 里定位
    # `leak` 最优先：一旦复述了系统提示，整段换掉，不再检查金额和承诺。
    if _has_leak(answer):
        violations.append("leak")

    if ctx.route == "rules":
        if ctx.check_amounts:
            violations += _amount_violations(answer, ctx.allowed_amounts)
        violations += _wording_violations(answer, full_refund=ctx.full_refund)

    elif ctx.route == "data":
        if ctx.check_amounts:
            violations += _amount_violations(answer, ctx.allowed_amounts)
        violations += _order_id_violations(answer, ctx.allowed_order_ids)

    elif ctx.route == "fallback":
        # E 出口不检查金额（ReAct 返回里没有汇总金额），但承诺同样要拦。
        violations += _wording_violations(answer, full_refund=False)

    # knowledge / human：只查泄露。政策原文可能合法出现「全额退款」和金额。

    if not violations:
        return GuardResult(answer)

    if "leak" in violations:
        return GuardResult(LEAK_SAFE_ANSWER, tuple(violations), dict(TRANSFER_ACTION))

    if ctx.route == "rules":
        return GuardResult(_rules_safe_answer(ctx), tuple(violations))

    if ctx.route == "data":
        return GuardResult(DATA_ERROR_ANSWER, tuple(violations), dict(TRANSFER_ACTION))

    return GuardResult(FALLBACK_SAFE_ANSWER, tuple(violations))


# ── 各类检查 ────────────────────────────────────────────────


def _rules_safe_answer(ctx: OutputContext) -> str:
    if ctx.eligible and ctx.refund_amount is not None:
        return RULES_REFUND_ANSWER.format(amount=yuan_text(ctx.refund_amount))
    return RULES_REJECT_ANSWER


def _amount_violations(answer: str, allowed: frozenset[int]) -> list[str]:
    """回答里出现 `allowed` 之外的金额 → 一条 `amount`。"""
    for cents in _answer_amounts(answer):
        if cents not in allowed:
            return ["amount"]
    return []


def _order_id_violations(answer: str, allowed: frozenset[str]) -> list[str]:
    """回答里出现数据结果之外的 4 位以上数字 → 一条 `order_id`。

    先把合法金额和合法标识从文本里剔掉，避免：
    - `1249.00 元` 被当成订单号；
    - 物流单号 `SF1001001` 里的 `1001001` 被当成别的订单。
    """
    masked = answer
    for match in _amount_matches(answer):
        masked = masked.replace(match.group(0), " ")
    if allowed:
        alternatives = "|".join(sorted((re.escape(v) for v in allowed), key=len, reverse=True))
        masked = re.sub(rf"(?<!\d)(?:{alternatives})(?!\d)", " ", masked)
    return ["order_id"] if _ORDER_ID_RE.search(masked) else []


def _wording_violations(answer: str, *, full_refund: bool) -> list[str]:
    """回答里出现「全额退」/「保证退」/「工作日到账」 → 一条 `full_refund` / `guarantee` / `time_promise`。"""
    found: list[str] = []
    if not full_refund and any(word in answer for word in _FULL_REFUND_WORDS):
        found.append("full_refund")
    if any(word in answer for word in _GUARANTEE_WORDS):
        found.append("guarantee")
    if _TIME_PROMISE_RE.search(answer):
        found.append("time_promise")
    return found


def _has_leak(answer: str) -> bool:
    """回答里复述了系统提示 → True。"""
    lowered = answer.lower()
    return any(marker.lower() in lowered for marker in _LEAK_MARKERS)


# ── 金额解析 ────────────────────────────────────────────────


def _amount_matches(answer: str) -> list[re.Match[str]]:
    """回答里出现的金额 → 匹配对象列表。"""
    matches: list[re.Match[str]] = []
    for pattern in _AMOUNT_PATTERNS:
        matches.extend(pattern.finditer(answer))
    return matches


def _answer_amounts(answer: str) -> set[int]:
    """回答里出现的金额 → 分（int）。"""
    return {_to_cents(match.group(1)) for match in _amount_matches(answer)}


def _to_cents(text: str) -> int:
    """`1,249.5` → 124950 分。整数运算，不经过 float。"""
    raw = text.replace(",", "")
    if "." in raw:
        whole, fraction = raw.split(".", 1)
        fraction = (fraction + "00")[:2]
    else:
        whole, fraction = raw, "00"
    return int(whole) * 100 + int(fraction)


# ── 递归取值 ────────────────────────────────────────────────


def _collect_values(
    value: Any, keys: set[str], *, ints: bool = False
) -> set[Any]:
    """递归收集 `data_result` 里指定 key 的值。

    `ints=True` 只收整数金额；否则收字符串标识（数字也转成字符串，
    用于从回答文本里剔除合法标识）。
    """
    found: set[Any] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in keys:
                    if ints:
                        if isinstance(child, int) and not isinstance(child, bool):
                            found.add(child)
                    elif child not in (None, ""):
                        found.add(str(child))
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found
