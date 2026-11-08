"""`/api/chat` 的对话轮次：把主图跑一轮，翻译成 SSE 帧。

帧顺序固定（前端按这个顺序渲染）：

```
ack → route → delta → citations → actions → done
                  ↑ 异常时用 error 替换其后所有帧
```

**route 帧在分类完成时就发**，不等生成——前端可以立刻显示"正在查知识库"之类的提示。
这靠 `astream(stream_mode="updates")`：每个节点跑完就吐一次，而不是等全图结束。

## 错误码

D9 定的四类是 `not_found` / `forbidden` / `timeout` / `invalid_arg`，那是**给模型看的工具错误语义**。
HTTP 这一层还需要一个兜底码：

| code | 场景 |
|---|---|
| `invalid_arg` | 空消息、超长、参数不合法 |
| `not_found` | 会话不存在 / 不属于当前用户（同一句，防枚举） |
| `timeout` | 模型或检索超时 |
| `internal_error` | 其余未预期异常（D9 四类之外，仅用于传输层） |

`forbidden` 留给 Ch06 的真实鉴权；现在归属不符统一走 `not_found`。
"""
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import slot_fill
from app.core.history import load_window, save_assistant_message, save_user_message
from app.core.lock import BusyError, conversation_lock
from app.core.output_guard import guard_answer, output_context
from app.core.session_state import (
    apply_flow_decision,
    clear_flow,
    decide_flow,
    load_flow,
)
from app.core.tracer import record_span
from app.db.errors import NotFound
from app.db.models import utcnow
from app.db.repo_conversation import create_conversation, get_conversation
from app.db.session import session_scope
from app.graph.build import build_graph
from app.graph.routing import INTENT_TO_ROUTE
from app.graph.rules_exit import extract_draft

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 500
ERROR_ANSWER = "服务暂时不可用，请稍后重试。"


class ChatError(Exception):
    """带错误码的对话异常，直接映射成 `error` 帧。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)


def _only_order_id(text: str, order_id: str | None) -> bool:
    """这句话是不是「只补了订单号」。

    空文本（点选卡片）也算；「1001，尺寸不合适」不算——后面还要抽取新信息。
    """
    if not text.strip():
        return True
    if not order_id:
        return False
    return text.replace(str(order_id), "").strip(" ，,。.!！?？") == ""


FLOW_LABELS = {"refund": "退款申请", "data": "数据查询"}


def _with_park_notice(answer: str, flow: str) -> str:
    """在回答尾部加一句「您还有一笔未完成流程」。"""
    label = FLOW_LABELS.get(flow, flow)
    notice = f"（您还有一笔未完成的{label}，需要继续吗？）"
    return f"{answer}\n\n{notice}" if answer else notice


async def _capture_refund_draft(final: dict) -> dict | None:
    """C 出口缺订单号时不会走 `extract`；把用户已经说过的原因补进 draft。

    只在该轮「问订单号、且 draft 还没有 reason_code」时补一次，
    避免每轮都多调一次抽取模型。
    """
    if final.get("route") != "rules" or final.get("pending_question") != "order_id":
        return None

    draft = dict(final.get("refund_draft") or {})
    if draft.get("reason_code"):
        return None

    query = final.get("resolved_query") or final.get("raw_query") or ""
    try:
        extracted = await extract_draft(query)
    except Exception:
        logger.warning("补齐退款草稿失败", exc_info=True)
        return None
    if not extracted:
        return None
    return {**draft, **extracted}


async def conversation_access(
    conversation_id: int | None,
    *,
    user_id: str,
    session: AsyncSession | None = None,
) -> None:
    """SSE 建立前的会话归属预检。

    必须在返回 `StreamingResponse` 之前调用：状态码一旦发出，后面只能回 `error` 帧。
    不存在 / 越权 / 已软删都抛同一个 `not_found`，不泄露存在性。
    """
    if conversation_id is None:
        return
    try:
        await get_conversation(conversation_id, user_id=user_id, session=session)
    except NotFound as exc:
        raise ChatError("not_found", "会话不存在") from exc


async def _save_error_message(cid: int, session: AsyncSession | None) -> None:
    """图执行失败时补一条 error 助手消息，避免用户消息悬空。

    落库失败只记日志，不能盖住原始异常。
    """
    try:
        async with session_scope(session) as s:
            await save_assistant_message(
                cid,
                ERROR_ANSWER,
                meta={"status": "error"},
                status="error",
                session=s,
            )
    except Exception:
        logger.exception("错误消息落库失败 conv=%s", cid)


async def run_turn(
    message: str,
    conversation_id: int | None,
    trace_id: str,
    *,
    client_message_id: str | None = None,
    selected_action: dict | None = None,
    graph: Any | None = None,
    session: AsyncSession | None = None,
    capture: dict | None = None,
) -> AsyncIterator[dict]:
    """跑一轮对话，逐个产出 SSE 帧（dict）。

    `graph` 可注入，测试用假图替换；生产走 `build_graph()`。
    `session` 可注入，测试指向临时库；生产不传，走模块级 engine。
    `capture` 只给评测入口用：图跑完后把终态、guard 后的回答和动作放进去，
    避免为了评测复制一套 `run_turn`。
    """
    text = (message or "").strip()
    # 结构化点选（选单/选规格）可以没有文字内容
    if not text and not selected_action:
        raise ChatError("invalid_arg", "消息不能为空")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ChatError("invalid_arg", f"消息过长（上限 {MAX_MESSAGE_CHARS} 字）")

    # Ch02 没有登录体系；订单归属校验需要一个固定的演示身份。
    # Ch06 接真实鉴权后改成从请求里取登录用户。
    user_id = get_settings().demo_user_id
    if conversation_id is None:
        cid = await create_conversation(user_id, session=session)
    else:
        cid = conversation_id

    # 先拿锁再读会话：否则两个并发请求会各自读到「上一轮还没写库」的旧状态
    try:
        async with conversation_lock(cid):
            async for frame in _run_turn_locked(
                cid=cid,
                conversation_id=conversation_id,
                user_id=user_id,
                text=text,
                selected_action=selected_action,
                client_message_id=client_message_id,
                trace_id=trace_id,
                graph=graph,
                session=session,
                capture=capture,
            ):
                yield frame
    except BusyError as exc:
        raise ChatError("busy", "上一条消息还在处理中，请稍后再试") from exc


async def _run_turn_locked(
    *,
    cid: int,
    conversation_id: int | None,
    user_id: str,
    text: str,
    selected_action: dict | None,
    client_message_id: str | None,
    trace_id: str,
    graph: Any | None,
    session: AsyncSession | None,
    capture: dict | None = None,
) -> AsyncIterator[dict]:
    """锁内的一轮：读会话与流程、槽位填充、跑图、写库。"""
    # 锁内再读会话：并发请求在等锁期间，上一轮可能已经改了 summary / 流程
    conv = None
    summary: str | None = None
    summary_upto = 0
    flow = None
    if conversation_id is not None:
        try:
            conv = await get_conversation(
                conversation_id, user_id=user_id, session=session
            )
        except NotFound as exc:
            # 不存在与不属于本人同一句，避免会话 id 成为枚举 oracle（S1/S2）
            raise ChatError("not_found", "会话不存在") from exc
        summary = conv.summary
        summary_upto = conv.summary_upto_msg_id or 0
        flow = await load_flow(cid, conversation=conv, session=session)
        if flow.expired:
            logger.info("会话流程已过期 conv=%s", cid)

    # 槽位填充：先按「当前在等什么」解析这句话，命中就直接接回原出口
    slot = await slot_fill.fill(
        flow.pending_question if flow else None,
        text,
        draft=flow.draft if flow else {},
        selected=selected_action,
    )
    if slot.cancel:
        await clear_flow(cid, session=session)

    resume_route: str | None = None
    restored: dict = {}
    if flow is not None and not slot.cancel and (slot.resume or slot.filled):
        intent = flow.draft.get("intent")
        if intent in INTENT_TO_ROUTE:          # draft 损坏时不硬接
            resume_route = INTENT_TO_ROUTE[intent]
            restored = {
                "intent": intent,
                "order_id": flow.draft.get("order_id"),
                "sku_id": flow.draft.get("sku_id"),
                "refund_draft": flow.draft.get("refund") or {},
                "validate_retry": flow.draft.get("validate_retry", 0),
            }
            if slot.slot == "order_id":
                restored["order_id"] = slot.value
            elif slot.slot == "sku_id":
                restored["sku_id"] = slot.value
            elif slot.slot in {"reason_code", "refund_extract"}:
                restored["refund_draft"] = slot.value

    # 用户答了一个槽位 / 说「继续」= 有进展，重试计数重新开始；
    # 否则「问订单号 → 用户答 → 问原因」会被算成两次失败，直接转人工。
    if resume_route == "rules" and (slot.resume or slot.filled):
        restored["validate_retry"] = 0

    skip_extract = resume_route == "rules" and (
        slot.resume
        # 退款槽位在 slot_fill 里已经调用过抽取器，出口里不能再调一次
        or slot.slot in {"reason_code", "refund_extract"}
        or (
            slot.slot == "order_id"
            and _only_order_id(text, restored.get("order_id"))
        )
    )

    # 窗口在本轮用户消息落库之前构建：history 只放上一轮及更早，
    # 本轮输入由 raw_query 单独传，避免同一句话出现两次。
    window = await load_window(
        cid,
        summary=summary,
        summary_upto_msg_id=summary_upto,
        session=session,
    )

    user_meta = {"selected_action": selected_action} if selected_action else None
    user_message_id = await save_user_message(
        cid,
        text or None,
        client_message_id=client_message_id,
        meta=user_meta,
        session=session,
    )

    engine = graph if graph is not None else build_graph()
    state = {
        "raw_query": text,
        "conversation_id": cid,
        "trace_id": trace_id,
        "user_id": user_id,
        "history": window.messages,
        "summary": window.summary,
        "resume_route": resume_route,
        "resumed": bool(resume_route),
    }
    state.update(restored)
    if skip_extract:
        state["skip_extract"] = True

    yield {
        "event": "ack",
        "conversation_id": cid,
        "user_message_id": user_message_id,
    }

    final: dict = {}
    turn_start = utcnow()          # 整个轮次的起点，`turn:total` 用它
    node_started = turn_start
    wall_start = time.perf_counter()

    try:
        async for chunk in engine.astream(state, stream_mode="updates"):
            node_ended = utcnow()
            for node_name, update in (chunk or {}).items():
                update = update or {}
                final.update(update)
                await record_span(
                    trace_id=trace_id,
                    name=f"node:{node_name}",
                    start_at=node_started,
                    end_at=node_ended,
                    attrs={
                        "duration_ms": _elapsed_ms(node_started, node_ended),
                        "keys": sorted(update.keys()),
                        "route": update.get("route"),
                        "placeholder": update.get("is_placeholder"),
                    },
                )
                # 分类一完成就把分流结果发出去，前端不用等生成
                if node_name == "classify_intent":
                    yield {
                        "event": "route",
                        "route": update.get("route"),
                        "intent": update.get("intent"),
                        "resumed": bool(update.get("resumed")),
                    }
            node_started = node_ended
    except Exception:
        await _save_error_message(cid, session)
        raise

    captured = await _capture_refund_draft(final)
    if captured is not None:
        final["refund_draft"] = captured

    answer = final.get("answer") or ""
    actions = list(final.get("actions") or [])

    # 输出兜底：金额只能来自 compute / 工具结果，承诺和系统提示泄露一律整段替换。
    # 必须在 delta 之前、落库之前——用户看到和回放的都得是 guard 之后的回答。
    guarded = guard_answer(answer, output_context(final))
    answer = guarded.answer
    # actions 里可能有 `transfer_human`，也可能是 `resume_flow` / `park_flow` / `transfer_human` 的组合。
    if guarded.action and guarded.action not in actions:
        # 兜底动作：如果输出被替换成兜底回答，必须加一个转人工动作，避免用户看了回答却没出口。
        actions.append(guarded.action)

    # 本轮没接回、旧流程还在、又答完了新话题 → park：保留旧流程并提示
    decision = decide_flow(
        final,
        parked=flow if flow is not None and not slot.cancel else None,
        resumed=bool(resume_route),
    )
    if decision.parked_flow:
        answer = _with_park_notice(answer, decision.parked_flow)
        actions.append({"type": "resume_flow", "flow": decision.parked_flow})
    if decision.replaced_flow:
        logger.info(
            "流程被替换 conv=%s old=%s new=%s",
            cid,
            decision.replaced_flow,
            decision.active_flow,
        )

    if capture is not None:
        capture["final"] = final
        capture["answer"] = answer
        capture["actions"] = list(actions)
        capture["violations"] = list(guarded.violations)

    if answer:
        yield {"delta": answer}

    citations = final.get("citation_ids") or []
    if citations:
        yield {"event": "citations", "items": citations}

    if actions:
        yield {"event": "actions", "items": actions}

    await record_span(
        trace_id=trace_id,
        name="turn:total",
        start_at=turn_start,
        end_at=utcnow(),
        attrs={
            "wall_ms": int((time.perf_counter() - wall_start) * 1000),
            "route": final.get("route"),
            "has_answer": bool(answer),
            "citations": len(citations),
            "parked": decision.parked_flow is not None,
            "flow_replaced": decision.replaced_flow is not None,
            "output_violations": list(guarded.violations),
        },
    )

    # 助手消息落库失败不阻断回答：用户已经看到回复，下一轮回放会缺这一条
    assistant_message_id: int | None = None
    try:
        async with session_scope(session) as s:
            assistant_message_id = await save_assistant_message(
                cid,
                answer,
                meta={
                    "intent": final.get("intent"),
                    "route": final.get("route"),
                    "citation_ids": citations,
                    "resumed": bool(final.get("resumed")),
                },
                session=s,
            )
            # 与助手消息同事务：不能出现「消息写了、流程没写」的半截状态
            await apply_flow_decision(cid, decision, session=s)
    except Exception:
        # 事务回滚了：不要把可能已 flush 的自增 id 发出去
        assistant_message_id = None
        logger.exception("助手消息/流程落库失败 conv=%s", cid)

    yield {"event": "done", "message_id": assistant_message_id}
