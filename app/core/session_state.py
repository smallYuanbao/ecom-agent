"""跨轮流程状态（M4/M7）。

只持久化三个字段：`active_flow` / `pending_question` / `draft`。
其余 state 字段都是一轮内的中间产物，不落库——避免「两份状态互相打架」。

**30 分钟失效**：以 `conversations.updated_at` 为准，读时惰性判断。
注意它是「最后一次活动」而不是「最后一次 park」，用户持续聊别的话题会刷新它。
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, utcnow
from app.db.repo_conversation import clear_flow as _clear_flow
from app.db.repo_conversation import get_conversation, set_flow

logger = logging.getLogger(__name__)

FLOW_TTL = timedelta(minutes=30)

# 只有这两个出口会产生「需要下一轮接回」的流程
FLOW_ROUTES = {"rules": "refund", "data": "data"}


class RefundDraft(TypedDict, total=False):
    """C 出口的要素（对应 `ConversationState.refund_draft`）。"""

    reason_code: str          # quality / no_reason / other
    item_count: int
    condition_ok: bool | None # 三态：True / False / None（用户没说）
    packaging_opened: bool


class FlowDraft(TypedDict, total=False):
    """`conversations.draft` 的结构。

    - `intent`：恢复时反查出口（`INTENT_TO_ROUTE`）；
    - `order_id` / `sku_id`：已经明确的实体；
    - `validate_retry`：C 出口的追问次数，跨轮累计；
    - `refund`：C 出口自己的要素，恢复时直接塞回 `state["refund_draft"]`。
    """

    intent: str
    order_id: str
    sku_id: str
    validate_retry: int
    refund: RefundDraft


@dataclass(frozen=True)
class FlowState:
    active_flow: str | None
    pending_question: str | None
    draft: FlowDraft
    expired: bool


@dataclass(frozen=True)
class FlowDecision:
    """轮末对流程的处理决定，纯计算，不碰数据库。"""

    action: str                       # save / keep / clear
    active_flow: str | None = None
    pending_question: str | None = None
    draft: FlowDraft = field(default_factory=dict)
    parked_flow: str | None = None    # 保留旧流程时，给前端提示用
    replaced_flow: str | None = None  # 新流程替换了旧流程时，记 trace 用


def is_expired(updated_at) -> bool:
    """按最后活动时间判断。`updated_at` 是 naive UTC。"""
    return utcnow() - updated_at > FLOW_TTL


def _parse_draft(raw: str | None) -> FlowDraft:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("流程 draft 不是合法 JSON，已忽略")
        return {}
    return value if isinstance(value, dict) else {}


async def load_flow(
    cid: int,
    *,
    user_id: str | None = None,
    conversation: Conversation | None = None,
    session: AsyncSession | None = None,
) -> FlowState:
    """读跨轮状态；过期就清空并返回 `expired=True`。

    **传 `conversation` 可复用调用方已经取到的行**，避免同一请求查两次；
    此时调用方必须已经完成归属校验。不传时用 `user_id` 自己查。
    """
    conv = conversation
    if conv is None:
        if user_id is None:
            raise ValueError("load_flow 需要 conversation 或 user_id")
        conv = await get_conversation(cid, user_id=user_id, session=session)
    if not conv.active_flow:
        return FlowState(None, None, {}, False)

    if is_expired(conv.updated_at):
        await clear_flow(cid, session=session)
        logger.info("流程已过期 conv=%s flow=%s", cid, conv.active_flow)
        return FlowState(None, None, {}, True)

    return FlowState(
        active_flow=conv.active_flow,
        pending_question=conv.pending_question,
        draft=_parse_draft(conv.draft),
        expired=False,
    )


def draft_from_state(state: dict) -> FlowDraft:
    """从一轮的终态提取要持久化的 draft。

    外层是流程元信息（intent / order_id / validate_retry），
    内层 `refund` 是 C 出口自己的 `refund_draft`——恢复时直接塞回 state，
    不会把元信息混进判定 prompt。
    """
    draft: FlowDraft = {}
    if state.get("intent"):
        draft["intent"] = state["intent"]
    if state.get("order_id"):
        draft["order_id"] = state["order_id"]
    if state.get("sku_id"):
        draft["sku_id"] = state["sku_id"]
    if state.get("validate_retry"):
        draft["validate_retry"] = state["validate_retry"]
    if state.get("refund_draft"):
        draft["refund"] = dict(state["refund_draft"])
    return draft


def decide_flow(
    state: dict,
    *,
    parked: FlowState | None = None,
    resumed: bool = False,
) -> FlowDecision:
    """轮末决定：存新流程 / 保留旧流程 / 清空。纯计算，不碰数据库。

    | 终态 | 动作 |
    |---|---|
    | `route ∈ {rules, data}` 且 `pending_question` 非空 | 存新流程（可能替换旧流程） |
    | 本轮没接回、旧流程还在、又不是转人工 | **保留旧流程**（park） |
    | 其余（完成 / 取消 / 转人工） | 清空 |

    `replaced_flow` 只在「旧流程还在、本轮又开始了新流程」时非空。
    """
    route = state.get("route")
    pending = state.get("pending_question")
    active_flow = FLOW_ROUTES.get(route)

    if active_flow and pending:
        replaced = (
            parked.active_flow
            if parked and parked.active_flow and not resumed
            else None
        )
        return FlowDecision(
            action="save",
            active_flow=active_flow,
            pending_question=pending,
            draft=draft_from_state(state),
            replaced_flow=replaced,
        )

    if parked and parked.active_flow and not resumed and route != "human":
        return FlowDecision(
            action="keep",
            active_flow=parked.active_flow,
            pending_question=parked.pending_question,
            draft=parked.draft,
            parked_flow=parked.active_flow,
        )

    return FlowDecision(action="clear")


async def apply_flow_decision(
    cid: int, decision: FlowDecision, *, session: AsyncSession | None = None
) -> None:
    if decision.action == "clear":
        await clear_flow(cid, session=session)
        return
    await set_flow(
        cid,
        active_flow=decision.active_flow,
        pending_question=decision.pending_question,
        draft=decision.draft,
        session=session,
    )


async def save_flow_from_state(
    cid: int,
    state: dict,
    *,
    parked: FlowState | None = None,
    resumed: bool = False,
    session: AsyncSession | None = None,
) -> FlowDecision:
    """轮末把「还要不要接回」写进会话表，返回本次决定。

    兼容旧调用：不传 `parked` / `resumed` 时，行为与 Task 4 相同（完成即清空）。
    """
    decision = decide_flow(state, parked=parked, resumed=resumed)
    await apply_flow_decision(cid, decision, session=session)
    return decision


async def clear_flow(cid: int, *, session: AsyncSession | None = None) -> None:
    await _clear_flow(cid, session=session)
