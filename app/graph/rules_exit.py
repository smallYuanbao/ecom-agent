"""C 出口：退款子图（9 状态 / 17 转移）。

本章只做退款。价保 / 订单变更在意图层就转人工，不进这个子图。

```
intake ─► extract ─► validate ─► query_data ─► judge ─► compute ─► compliance ─► respond
  │                     │  ▲           │  ▲        │
  │                     │  └ need_info ┘  │        │
  │                     └──(2 次失败)─────┼────────┼──► 转人工
  └──(缺订单号)──► need_info              └────────┘
```

**「转人工」不是第 10 个节点**：主图已经有 `human` 出口，子图只需把
`transfer_reason` / `answer` / `actions` 写好，然后回到 END。
spec 说的"9 状态"就是上面这 9 个节点。

### 9 个节点各自在干什么

| 节点名 | 中文 | 职责（一句话） |
|---|---|---|
| `intake` | **受理** | 从用户话里抠出订单号（正则），不做业务校验 |
| `extract` | **抽取** | 模型抽出退款要素：原因、件数、完好程度、是否拆封 |
| `validate` | **校验** | 代码检查要素齐不齐、单子是不是本人的、日期是否矛盾 |
| `need_info` | **补充信息** | 缺什么问什么；缺订单号时列最近 3 单让用户点 |
| `query_data` | **查数据** | 查订单快照，同时检索退款政策原文 |
| `judge` | **判定** | 模型判「能不能退」，给出理由码和置信度 |
| `compute` | **算钱** | 纯函数算退多少（分）；不判资格 |
| `compliance` | **合规核对** | 红线检查 + 用规则代码独立复算，与模型不一致就转人工 |
| `respond` | **回复** | 模型组织话术；金额必须逐字引用 compute 的结果 |

记忆锚点：**受理 → 抽取 → 校验 → （缺就追问）→ 查数 → 判定 → 算钱 → 核对 → 回复**。

### 守卫的名字怎么读

守卫就是"岔路口"，函数名全是 `after_<上一个节点>`，
意思是 **"刚做完 `<上一个节点>`，下一步走哪"**。返回值是**目标节点的名字**（或 `end`）。

| 守卫 | 读作 | 判断什么 |
|---|---|---|
| `after_intake` | intake 之后 | 抽到单号没有 |
| `after_need_info` | 补充信息之后 | 用户补充了吗，还是还在等 / 放弃了 |
| `after_validate` | 校验之后 | 要素齐不齐；追问 2 次还不行就转人工 |
| `after_query_data` | 查数据之后 | 订单查到没有（用户问题）、政策有没有（系统问题） |
| `after_judge` | 判定之后 | 模型有把握吗（置信度 ≥ 0.7） |
| `after_compliance` | 核对之后 | 踩红线或复算不一致了吗 |

**本章 Task 4 只搭骨架**：9 个节点都是空实现，边与守卫按 spec 的转移表写死。
真实实现从 Task 5 起逐个替换，**边和守卫的键不改**——那是这一章的验收对象。
"""
import logging
import re
from datetime import datetime
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.llm import LLMFormatError, structured_call
from app.core.llm import chat as llm_chat
from app.core.prompts import (
    EXTRACT_PARAMETERS,
    EXTRACT_REASONS,
    EXTRACT_TOOL_NAME,
    JUDGE_PARAMETERS,
    JUDGE_REASONS,
    JUDGE_TOOL_NAME,
    extract_messages,
    judge_messages,
    respond_messages,
)
from app.core.retrieval import search
from app.core.tracer import record_span
from app.db.errors import NotFound
from app.db.models import Order, utcnow
from app.db.repo_order import get_order, list_user_orders
from app.graph.state import ConversationState
from app.rules.eligibility import (
    NOT_APPLICABLE_KEYWORDS,
    SEVENTH_ARTICLE_KEYWORDS,
    WINDOW_DAYS,
    OrderSnapshot,
    RefundRequest,
    judge_eligibility,
)
from app.rules.refund import Judgement, compute_amount

logger = logging.getLogger(__name__)

# 判定置信度门槛：低于它转人工（拿不准不放行）
CONFIDENCE_MIN = 0.7

# validate 追问超过这个次数仍不合格 → 转人工
MAX_VALIDATE_RETRY = 2

# 用户无法修正的数据矛盾：追问没有意义，直接转人工。
# 目前只有「签收早于发货」——那是 ERP/时区/补录的问题，不是用户填错。
NON_RETRYABLE_ERRORS = frozenset({"date_anomaly"})

# 转人工的兜底话术：不解释内部原因，也不承诺任何数字
TRANSFER_TEXT = "这个问题我这边处理不了，已经为您转接人工客服。"
TRANSFER_ACTION = {"type": "transfer_human"}

# 判定输出里，哪些理由码对应"可退"。`eligible` 与它必须一致——
# `eligible=False` + `ok` 这种自相矛盾的输出要整条作废
ELIGIBLE_REASONS = frozenset({"ok", "reject_then_refund"})

# 订单号：4 位以上连续数字，且左右不能再接数字。
# `(?<!\d)` / `(?!\d)` 防的是从 `123456` 里抠出前 4 位。
ORDER_ID_RE = re.compile(r"(?<!\d)(\d{4,})(?!\d)")

# 列给用户的候选订单数：超过 3 个选择困难，也占满屏幕
CANDIDATE_LIMIT = 3

# 政策检索取几条。5 条足够覆盖退款时效 / 不适用商品 / 完好要求三类条款
POLICY_TOP_K = 5

# `reason_code` 是英文枚举，直接拼进中文查询等于给检索器喂噪声
REASON_TEXT = {"quality": "质量问题", "no_reason": "无理由", "other": "退款"}

# 9 个节点的名字。顺序 = 正常路径，`need_info` 是旁路，单独标出
NODE_NAMES: tuple[str, ...] = (
    "intake",
    "extract",
    "validate",
    "need_info",
    "query_data",
    "judge",
    "compute",
    "compliance",
    "respond",
)

# ── 条件边的键 → 目标节点 ────────────────────────────────────
# 守卫函数的返回值必须落在这里面；测试会逐个断言。
INTAKE_NEXT = {"extract": "extract", "need_info": "need_info"}
NEED_INFO_NEXT = {"validate": "validate", "end": END}
VALIDATE_NEXT = {
    "query_data": "query_data",
    "need_info": "need_info",
    "human": END,
}
QUERY_DATA_NEXT = {"judge": "judge", "need_info": "need_info", "human": END}
JUDGE_NEXT = {"compute": "compute", "human": END}
COMPLIANCE_NEXT = {"respond": "respond", "human": END}


# ── 节点：全部空实现 ────────────────────────────────────────


async def _stub(state: ConversationState) -> dict:
    """占位节点：不读写任何业务字段。Task 6 起逐个换成真实实现。"""
    return {}


# ── intake · 受理 ───────────────────────────────────────────


def extract_order_id(text: str) -> str | None:
    """从用户原话里抠订单号；抠不到返回 `None`。

    注意：**这里不判断"这个订单号存不存在"**——那是 `validate` 与
    `query_data` 的事。正则只负责把纯数字挑出来，业务校验不混进来。

    已知局限：手机号这类长数字也会被当成订单号。它会在 `query_data`
    查不到订单，退回 `need_info` 列候选——**错得可控**，所以不为它加特判。
    """
    match = ORDER_ID_RE.search(text or "")
    return match.group(1) if match else None


async def intake(state: ConversationState) -> dict:
    """受理：从用户话里抠订单号。

    抽到就写 `order_id`，并清掉上一轮的追问标记（用户已经补充了）。
    抽不到什么都不写，由 `after_intake` 送去 `need_info`。
    """
    query = state.get("resolved_query") or state.get("raw_query") or ""
    order_id = extract_order_id(query)
    if not order_id:
        return {}
    return {"order_id": order_id, "pending_question": None}


# ── extract · 抽取（模型）──────────────────────────────────


def _clean_draft(data: dict) -> dict:
    """把模型返回的原始结果洗成可信的 draft。

    **模型返回值一律不可信**：枚举可能越界、数字可能是字符串或负数、
    布尔可能被写成 `"true"`。不合法的字段直接丢掉——丢掉的后果是
    `validate` 判缺失、走追问，比拿一个错值去算金额安全得多。
    """
    draft: dict = {}

    reason = data.get("reason_code")
    if reason in EXTRACT_REASONS:
        draft["reason_code"] = reason

    count = data.get("item_count")
    # `isinstance(True, int)` 为真——布尔要先排除，否则 True 会变成 1 件
    if isinstance(count, int) and not isinstance(count, bool) and count >= 1:
        draft["item_count"] = count

    for key in ("condition_ok", "packaging_opened"):
        value = data.get(key)
        if isinstance(value, bool):
            draft[key] = value

    return draft


async def extract_draft(query: str) -> dict:
    """调模型抽退款要素；格式错误降级成空 draft。

    **格式错误不转人工**：`validate` 会把"缺要素"判成需要追问，
    用户补充一轮就能继续——这比直接踢给人工便宜得多。
    网络/鉴权错误照常上抛（与 `classify` 相同的异常边界）。
    """
    try:
        data = await structured_call(
            EXTRACT_TOOL_NAME, EXTRACT_PARAMETERS, extract_messages(query)
        )
    except LLMFormatError:
        return {}
    return _clean_draft(data)


async def extract(state: ConversationState) -> dict:
    """抽取节点：读 `resolved_query`，把要素合并进 `refund_draft`。

    **合并而非覆盖**：用户可能分两轮补充（先说"质量有问题"、再说"退一件"），
    每轮只抽到一部分，覆盖会把上一轮的答案抹掉。
    """
    query = state.get("resolved_query") or state.get("raw_query") or ""
    draft = dict(state.get("refund_draft") or {})
    draft.update(await extract_draft(query))
    return {"refund_draft": draft}


# ── validate · 校验（代码）──────────────────────────────────


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def order_snapshot(order: Order) -> dict:
    """把 ORM 订单转成快照 dict。

    **不直接把 ORM 对象塞进 state**：state 会被序列化、会进 trace、
    也可能会被 LangGraph 的 checkpointer 深拷贝。转成纯 dict 更安全，
    也让 `judge` 的 prompt 可以直接渲染。

    时间统一用 ISO 字符串：模型读得懂，日志里也不会有 `<object at 0x...>`。
    """
    return {
        "order_id": order.id,
        "status": order.status,
        "amount": order.amount,
        "quantity": order.quantity,
        "sku_id": order.sku_id,
        "spec": order.spec,
        "category": order.category or "",   # 存量行可能是 NULL/空，别让匹配函数收到 None
        "created_at": _iso(order.created_at),
        "paid_at": _iso(order.paid_at),
        "shipped_at": _iso(order.shipped_at),
        "signed_at": _iso(order.signed_at),
    }


def _date_anomaly(order: Order) -> bool:
    """签收早于发货 = 数据矛盾。

    和「未签收」要区分开：`signed_at is None` 是**还没有**（正常业务状态），
    这个函数只抓「有值但错了」（数据故障）。
    """
    if order.shipped_at is None or order.signed_at is None:
        return False
    return order.signed_at < order.shipped_at


def _transfer(reason: str) -> dict:
    """转人工的返回体：话术 + 动作 + 原因（trace / 落池用）。"""
    return {
        "transfer_reason": reason,
        "answer": TRANSFER_TEXT,
        "actions": [dict(TRANSFER_ACTION)],
        "is_placeholder": False,
    }


async def validate(state: ConversationState) -> dict:
    """校验要素：齐不齐、是不是本人的单、日期是否矛盾。

    **`condition_ok` 不在这里拦**——它是判定材料，不是申请门槛。
    缺了交给 `judge` 结合政策考虑，拿不准会给低 confidence。

    **订单只在订单号存在时才查**：缺单号时 `get_order` 没有输入，
    也没必要为一个空字符串打一次库。
    """
    draft = state.get("refund_draft") or {}
    order_id = state.get("order_id")
    user_id = state.get("user_id") or ""

    errors: list[str] = []
    updates: dict = {}

    if not order_id:
        errors.append("missing_order_id")
    if not draft.get("reason_code"):
        errors.append("missing_reason_code")

    if order_id and not errors:
        try:
            order = await get_order(order_id, user_id=user_id)
        except NotFound:
            # 不存在与越权共用 NotFound（S1）：不能泄露"这单确实存在"
            errors.append("order_not_found")
        else:
            updates["order_snapshot"] = order_snapshot(order)
            if _date_anomaly(order):
                errors.append("date_anomaly")

    updates["validate_errors"] = errors

    retry = state.get("validate_retry") or 0
    if set(errors) & NON_RETRYABLE_ERRORS:
        updates.update(_transfer("date_anomaly"))
    elif errors and retry >= MAX_VALIDATE_RETRY:
        updates.update(_transfer("validate_retry_exhausted"))

    return updates


# ── query_data · 查数据（代码）──────────────────────────────


async def query_data(state: ConversationState) -> dict:
    """查数据：订单快照 + 退款政策原文。

    **订单通常不用再查**：`validate` 为了校验必须读库，快照已经写进 state。
    只有直接进入本节点（测试或未来的其他路径）时才补查一次——一轮只查一次库。

    **政策检索为空或失败都转人工**：那是系统缺内容，不是用户能补的信息。
    没有依据就让模型判资格，等于让它凭记忆答——参考实现刻意避免这一点，
    它的退款流程里 `retrieve_policy` 是强制步骤。
    """
    updates: dict = {}
    snapshot = state.get("order_snapshot")

    if not snapshot:
        order_id = state.get("order_id")
        if not order_id:
            return {}                      # after_query_data 会送去 need_info
        try:
            order = await get_order(order_id, user_id=state.get("user_id") or "")
        except NotFound:
            return {}                      # 不存在与越权不区分（S1）
        snapshot = order_snapshot(order)
        updates["order_snapshot"] = snapshot

    reason = (state.get("refund_draft") or {}).get("reason_code") or "other"
    query = f"退款 {REASON_TEXT.get(reason, '退款')} {snapshot.get('status', '')}".strip()

    try:
        hits = await search(query, top_k=POLICY_TOP_K)
    except Exception:                      # 检索失败一律是系统问题（见下）
        # 向量库挂了不该让整轮 500：转人工是可执行的结果，错误帧不是
        logger.warning("退款政策检索失败", exc_info=True)
        hits = []

    updates["evidence"] = hits
    if not hits:
        updates.update(_transfer("policy_retrieval_empty"))
    return updates


# ── judge · 判定（模型）────────────────────────────────────


def _clean_judgement(data: dict) -> dict | None:
    """校验模型输出；任何一项不合法都返回 `None`，由调用点转人工。

    与 `_clean_draft` 的区别：draft 缺字段还能继续（去追问），
    但判定**没有"部分正确"**——资格要么有结论，要么没有。
    所以这里不是"丢掉坏字段"，而是整条作废。
    """
    eligible = data.get("eligible")
    reason = data.get("reason_code")
    confidence = data.get("confidence")

    if not isinstance(eligible, bool):
        return None
    if reason not in JUDGE_REASONS:
        return None
    if eligible != (reason in ELIGIBLE_REASONS):
        return None                      # 结论与理由码自相矛盾
    # `True` 也能通过 `(int, float)`，但它不是置信度
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not 0.0 <= float(confidence) <= 1.0:
        return None

    judgement = {
        "eligible": eligible,
        "reason_code": reason,
        "confidence": float(confidence),
    }
    cited = data.get("cited_article")
    if isinstance(cited, str) and cited.strip():
        judgement["cited_article"] = cited.strip()
    return judgement


async def judge(state: ConversationState) -> dict:
    """判定资格：模型判，代码只做形状校验和门槛。

    **给模型四样材料**：规则表（prompt 里）+ 订单快照 + 申请要素 + 政策原文。
    缺任何一样，模型就只能凭记忆判——那正是参考实现刻意避免的。

    **失败一律转人工**：格式错误说明模型没按契约走，低置信度说明它自己拿不准。
    两种都不该放行到 `compute`——算出一个没有依据的金额，比转人工更糟。
    """
    query = state.get("resolved_query") or state.get("raw_query") or ""
    try:
        data = await structured_call(
            JUDGE_TOOL_NAME,
            JUDGE_PARAMETERS,
            judge_messages(
                query,
                state.get("order_snapshot") or {},
                state.get("refund_draft") or {},
                state.get("evidence") or [],
            ),
        )
    except LLMFormatError:
        return _transfer("judge_format_error")

    judgement = _clean_judgement(data)
    if judgement is None:
        return _transfer("judge_format_error")

    updates: dict = {"refund_judgement": judgement}
    if judgement["confidence"] < CONFIDENCE_MIN:
        updates.update(_transfer("judge_low_confidence"))
    return updates


# ── compute · 算钱 + compliance · 合规核对 ──────────────────


def _parse_dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


def _to_order_snapshot(snapshot: dict, packaging_opened: bool | None) -> OrderSnapshot:
    """state 里的快照 dict → 规则函数的入参。

    `packaging_opened` 不在订单快照里（数据库没有这个字段），
    它来自 `extract` 抽出的 `refund_draft`。
    """
    return OrderSnapshot(
        order_id=str(snapshot.get("order_id") or ""),
        status=str(snapshot.get("status") or ""),
        amount=int(snapshot.get("amount") or 0),
        quantity=int(snapshot.get("quantity") or 1),
        category=str(snapshot.get("category") or ""),
        packaging_opened=packaging_opened,
        # 规则表不用 created_at，但 dataclass 要求非空；给一个明确的哨兵值
        created_at=_parse_dt(snapshot.get("created_at")) or datetime(1970, 1, 1),
        shipped_at=_parse_dt(snapshot.get("shipped_at")),
        signed_at=_parse_dt(snapshot.get("signed_at")),
    )


def _to_request(draft: dict, quantity: int) -> RefundRequest:
    """申请要素 → 规则函数入参。

    `item_count` 缺省时按**整单**处理：用户没说退几件，默认全退，
    与「退一件」的显式说明区分开。
    """
    return RefundRequest(
        reason_code=str(draft.get("reason_code") or "other"),
        item_count=int(draft.get("item_count") or quantity),
        condition_ok=draft.get("condition_ok"),
    )


async def compute(state: ConversationState) -> dict:
    """算钱：把 `judge` 的结论翻译成金额。纯计算，不做判断。"""
    snapshot = state.get("order_snapshot") or {}
    judgement = state.get("refund_judgement") or {}
    draft = state.get("refund_draft") or {}

    order = _to_order_snapshot(snapshot, draft.get("packaging_opened"))
    amount = compute_amount(
        order,
        Judgement(
            eligible=bool(judgement.get("eligible")),
            reason_code=str(judgement.get("reason_code") or ""),
        ),
        _to_request(draft, order.quantity),
    )
    return {"refund_amount": amount}


def _category_matches(category: str, keywords: tuple[str, ...]) -> bool:
    return any(word in category for word in keywords)


def _red_line(
    order: OrderSnapshot,
    request: RefundRequest,
    judgement: dict,
    amount: int,
    now: datetime,
) -> str | None:
    """绝对红线：不依赖模型，也不依赖复算。

    只在"判定可退"时有意义——不可退的金额本来就是 0。
    """
    if not judgement.get("eligible"):
        return None
    if amount > order.amount:
        return "amount_exceeds_paid"
    if amount <= 0:
        return "amount_not_positive"
    if _category_matches(order.category, NOT_APPLICABLE_KEYWORDS):
        return "not_applicable_bypass"
    if _category_matches(order.category, SEVENTH_ARTICLE_KEYWORDS) and order.packaging_opened:
        return "not_applicable_bypass"
    if (
        request.reason_code == "no_reason"
        and order.signed_at is not None
        and (now.date() - order.signed_at.date()).days > WINDOW_DAYS
    ):
        return "window_bypass"
    return None


def _divergence(
    judgement: dict, order: OrderSnapshot, request: RefundRequest, now: datetime
) -> str | None:
    """用规则代码独立复算，与模型结论对比。

    **比的是 `(eligible, reason_code)` 这一对，不是只比能不能退**：
    `ok` 与 `reject_then_refund` 都算可退，但一个让用户"寄回"、一个让用户"拒收"，
    给错指引比给错结论更让人困惑。

    代码返回 `None` 表示**代码也判不了**（信息不足），不构成分歧——
    代码都不知道答案，没资格否决模型。
    """
    code = judge_eligibility(order, request, now=now)
    if code is None:
        return None

    model_pair = (bool(judgement.get("eligible")), str(judgement.get("reason_code") or ""))
    if model_pair == (code.eligible, code.reason_code):
        return None
    return f"rule_divergence: model={model_pair[1]}, code={code.reason_code}"


async def _record_violation(state: ConversationState, violation: str, judgement: dict) -> None:
    """分歧要能被看见。写 span 失败不影响主流程（`record_span` 自己兜底）。"""
    trace_id = state.get("trace_id")
    if not trace_id:
        return
    await record_span(
        trace_id=trace_id,
        name="compliance:violation",
        start_at=utcnow(),
        attrs={"violation": violation, "model": judgement},
    )


async def compliance(state: ConversationState) -> dict:
    """合规核对：红线 + 独立复算。任一不通过 → 转人工。

    **分歧时不以代码为准**：那样模型就白用了；直接采信模型又失去可审计性。
    转人工是第三条路——代价是体验，换来的是"分歧必被看见"。
    """
    snapshot = state.get("order_snapshot") or {}
    judgement = state.get("refund_judgement") or {}
    draft = state.get("refund_draft") or {}
    amount = int(state.get("refund_amount") or 0)
    now = utcnow()

    order = _to_order_snapshot(snapshot, draft.get("packaging_opened"))
    request = _to_request(draft, order.quantity)

    violation = _red_line(order, request, judgement, amount, now)
    if violation is None:
        violation = _divergence(judgement, order, request, now)

    if violation is None:
        return {"compliance_violation": None}

    await _record_violation(state, violation, judgement)
    updates: dict = {"compliance_violation": violation}
    updates.update(_transfer(violation))
    return updates


# ── need_info · 补充信息 ────────────────────────────────────


def yuan_text(cents: int) -> str:
    """分 → 「1249.00 元」。用整数运算，避免浮点在边界上差 1 分。"""
    return f"{cents // 100}.{cents % 100:02d} 元"


def _candidate(order: Order) -> dict:
    """候选订单的展示形状（给前端画卡片，不暴露内部字段）。"""
    title = " ".join(part for part in (order.sku_id, order.spec) if part)
    return {
        "order_id": order.id,
        "title": title,
        "status": order.status,
        "amount": order.amount,                   # 分
        "amount_text": yuan_text(order.amount),   # 展示用
        "created_at": order.created_at.strftime("%Y-%m-%d"),
    }


async def need_info(state: ConversationState) -> dict:
    """缺什么问什么。

    顺序：先要订单号（能列候选就列候选），再要退款原因。
    什么都不缺就返回空字典，由 `after_need_info` 放行回 `validate`。

    **为什么列候选而不是让用户打字**：用户记不住订单号。
    参考实现就是这么做的——点一下比敲 6 位数字可靠，也顺带说明"这单是你的"。

    **`validate_retry` 每次追问 +1**：追问满 2 次仍不合格，
    `after_validate` 会把流程送去人工。注意：跨请求累加要等 Ch05 把
    `validate_retry` 落库（P5-3）；在那之前它只在一轮图执行内有效。
    """
    errors = set(state.get("validate_errors") or [])
    draft = state.get("refund_draft") or {}
    retry = (state.get("validate_retry") or 0) + 1

    if not state.get("order_id") or errors & {"missing_order_id", "order_not_found"}:
        return {**await _ask_order(state), "validate_retry": retry}
    if not draft.get("reason_code") or "missing_reason_code" in errors:
        return {**_ask_reason(), "validate_retry": retry}
    return {}


async def _ask_order(state: ConversationState) -> dict:
    """要订单号。有 `user_id` 就列最近 3 单；没有就退化成文字追问。"""
    user_id = state.get("user_id")
    candidates: list[dict] = []
    if user_id:
        orders = await list_user_orders(user_id=user_id)
        candidates = [_candidate(order) for order in orders[:CANDIDATE_LIMIT]]

    if not candidates:
        return {
            "pending_question": "order_id",
            "answer": "请提供一下订单号（在「我的订单」里可以看到）。",
            "is_placeholder": False,
        }

    return {
        "order_candidates": candidates,
        "pending_question": "order_id",
        "answer": "我查到您最近有这几笔订单，请问是哪一单？",
        "actions": [{"type": "select_order", "candidates": candidates}],
        "is_placeholder": False,
    }


def _ask_reason() -> dict:
    """要退款原因。代码问，不问模型——这句话是固定话术。"""
    return {
        "pending_question": "reason_code",
        "answer": "请问您想退货的原因是什么？是商品有质量问题，还是不想要了？",
        "is_placeholder": False,
    }


# ── 守卫：只返回键，不写 state ──────────────────────────────


def after_intake(state: ConversationState) -> str:
    """**受理之后**：抽到订单号就继续，抽不到就追问。"""
    return "extract" if state.get("order_id") else "need_info"


def after_need_info(state: ConversationState) -> str:
    """**补充信息之后**：有追问话术 = 还在等用户回答，本轮结束；
    否则用户已补充 → 回到 validate。"""
    return "end" if state.get("pending_question") else "validate"


def after_validate(state: ConversationState) -> str:
    """**校验之后**：要素齐全 → 查数据；缺失 → 追问；追问 2 次仍不合格 → 转人工。

    「签收早于发货」这类**用户改不了的数据矛盾**不追问，直接转人工——
    让用户回答一个他无法回答的问题，只会多浪费一轮。
    """
    errors = set(state.get("validate_errors") or [])
    if not errors:
        return "query_data"
    if errors & NON_RETRYABLE_ERRORS:
        return "human"
    if (state.get("validate_retry") or 0) >= MAX_VALIDATE_RETRY:
        return "human"
    return "need_info"


def after_query_data(state: ConversationState) -> str:
    """**查数据之后**：订单查不到是用户的问题（可能报错单号）→ 追问；
    政策检索为空是系统的问题（知识库缺内容）→ 转人工。"""
    if not state.get("order_snapshot"):
        return "need_info"
    if not state.get("evidence"):
        return "human"
    return "judge"


def after_judge(state: ConversationState) -> str:
    """**判定之后**：置信度不足 → 转人工。判定本身由 Task 9 的模型给出。"""
    judgement = state.get("refund_judgement") or {}
    confidence = judgement.get("confidence", 0.0)
    return "compute" if confidence >= CONFIDENCE_MIN else "human"


def after_compliance(state: ConversationState) -> str:
    """触红线或与代码复算不一致 → 转人工，且不经过 respond。"""
    return "human" if state.get("compliance_violation") else "respond"


# ── respond · 回复（模型）──────────────────────────────────


def _amount_in(answer: str, amount_text: str) -> bool:
    """回答里有没有这个金额。**忽略空格与千分位逗号**。

    真模型实测发现：它会把「1249.00 元」写成「1249.00元」或「1,249.00元」。
    按原样匹配会认不出来，于是重复追加一句，用户看到两个金额。
    数字与小数位才是硬约束，格式不是。
    """

    def _normalize(text: str) -> str:
        # `\s` 在 Python 里含全角空格（U+3000）
        return re.sub(r"[\s,，]+", "", text)

    return _normalize(amount_text) in _normalize(answer)


def _ensure_amount(answer: str, judgement: dict, amount_text: str) -> str:
    """机械兜底：可退的回答里必须出现算好的金额。

    这是"最简版"校验（完整版留给 Ch06）：不比对语义，只查金额在不在。
    不在就**追加**一句，而不是重试模型——重试可能又少写，追加是确定的。

    不可退时不做这个检查：那时回答里本来就不该出现金额。
    """
    if not judgement.get("eligible") or not amount_text:
        return answer
    if _amount_in(answer, amount_text):
        return answer
    return f"{answer.rstrip()}\n\n本次应退金额：{amount_text}。"


async def respond(state: ConversationState) -> dict:
    """组织话术：只复述 `judge` 的结论和 `compute` 的金额，不重新判断。

    模型在这里唯一的自由度是**怎么说**，不是**说什么**。
    """
    judgement = state.get("refund_judgement") or {}
    amount = int(state.get("refund_amount") or 0)
    amount_text = yuan_text(amount) if judgement.get("eligible") else ""
    query = state.get("resolved_query") or state.get("raw_query") or ""

    answer = await llm_chat(
        respond_messages(
            query,
            state.get("order_snapshot") or {},
            state.get("refund_draft") or {},
            judgement,
            amount_text,
        )
    )
    return {
        "answer": _ensure_amount(answer, judgement, amount_text),
        "is_placeholder": False,
    }


# ── 子图 ────────────────────────────────────────────────────


def _traced(name: str, node):
    """给子图节点套一层 `trace_span`。

    父图 `run_turn` 只会在子图整体结束时看到一行 `node:rules`——
    里面跑了哪 9 个状态、各花多久，看不到。spec 验收要求"每个状态落一行"，
    所以这里逐个记录。写失败不影响流程（`record_span` 自己兜底）。
    """

    async def wrapper(state: ConversationState) -> dict:
        start = utcnow()
        result = await node(state)
        trace_id = state.get("trace_id")
        if trace_id:
            await record_span(
                trace_id=trace_id,
                name=f"rules:{name}",
                start_at=start,
                end_at=utcnow(),
                attrs={"keys": sorted((result or {}).keys())},
            )
        return result

    wrapper.__name__ = f"traced_{name}"
    return wrapper


def build_rules_graph() -> CompiledStateGraph:
    """编译退款子图。主图把它挂在 `rules` 节点上（节点名不变）。"""
    graph: Any = StateGraph(ConversationState)

    real_nodes = {
        "intake": intake,
        "extract": extract,
        "validate": validate,
        "query_data": query_data,
        "judge": judge,
        "compute": compute,
        "compliance": compliance,
        "respond": respond,
        "need_info": need_info,
    }
    for name in NODE_NAMES:
        graph.add_node(name, _traced(name, real_nodes.get(name, _stub)))

    graph.set_entry_point("intake")

    graph.add_conditional_edges("intake", after_intake, INTAKE_NEXT)
    graph.add_edge("extract", "validate")
    graph.add_conditional_edges("validate", after_validate, VALIDATE_NEXT)
    graph.add_conditional_edges("need_info", after_need_info, NEED_INFO_NEXT)
    graph.add_conditional_edges("query_data", after_query_data, QUERY_DATA_NEXT)
    graph.add_conditional_edges("judge", after_judge, JUDGE_NEXT)
    graph.add_edge("compute", "compliance")
    graph.add_conditional_edges("compliance", after_compliance, COMPLIANCE_NEXT)
    graph.add_edge("respond", END)

    return graph.compile()
