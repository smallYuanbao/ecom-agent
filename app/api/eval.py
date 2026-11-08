"""评测专用门面 `/api/eval/chat`（Ch07）。

不是产品接口：默认关闭，只在本机评测实例打开。
它复用 `run_turn`，把 SSE 帧收成 JSON，并额外返回检索证据：

- `references`：被回答引用的证据；
- `hits`：完整 top-k，带 rank / score / cited；
- `signals`：路由、工具码、退款判定等允许外露的终态字段。

**不复制图逻辑**：`run_turn(capture=...)` 在图跑完后把终态放进来。
SSE 的 `delta` 拼成 `answer`，保证评测看到的就是用户看到的。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from openai import APITimeoutError
from pydantic import BaseModel

from app.api.chat import ChatError, run_turn
from app.config import get_settings
from app.core.logging import new_trace_id, trace_id_var

logger = logging.getLogger(__name__)

router = APIRouter()


class EvalRequest(BaseModel):
    message: str


def _raise_mapped(exc: ChatError) -> None:
    status = {
        "invalid_arg": 422,
        "not_found": 404,
        "timeout": 504,
        "busy": 429,
    }.get(exc.code, 500)
    raise HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.message},
    ) from exc


def _signals(final: dict[str, Any]) -> dict[str, Any]:
    """只返回白名单字段：不放订单快照、prompt、用户信息。"""
    signals: dict[str, Any] = {}
    for key in ("tool_code", "pending_question"):
        if final.get(key) is not None:
            signals[key] = final[key]

    judgement = final.get("refund_judgement") or {}
    refund: dict[str, Any] = {
        key: judgement[key]
        for key in ("eligible", "reason_code", "confidence")
        if key in judgement
    }
    if final.get("refund_amount") is not None:
        refund["amount"] = final["refund_amount"]
    if refund:
        signals["refund"] = refund
    return signals


@router.post("/api/eval/chat")
async def eval_chat(req: EvalRequest) -> dict[str, Any]:
    if not get_settings().enable_eval_api:
        raise HTTPException(status_code=404, detail="Not Found")

    trace_id = trace_id_var.get()
    if trace_id == "-":
        trace_id = new_trace_id()

    captured: dict[str, Any] = {}
    frames: list[dict[str, Any]] = []
    try:
        async for frame in run_turn(
            req.message, None, trace_id, capture=captured
        ):
            frames.append(frame)
    except ChatError as exc:
        _raise_mapped(exc)
    except (TimeoutError, httpx.TimeoutException, APITimeoutError) as exc:
        raise HTTPException(
            status_code=504, detail={"code": "timeout"}
        ) from exc
    except Exception as exc:
        logger.exception("评测轮次失败 trace=%s", trace_id)
        raise HTTPException(
            status_code=500, detail={"code": "internal_error"}
        ) from exc

    final = captured.get("final") or {}
    answer = captured.get("answer") or "".join(
        str(frame.get("delta") or "") for frame in frames if "delta" in frame
    )
    actions = captured.get("actions") or final.get("actions") or []

    evidence = final.get("evidence") or []
    cited = set(final.get("citation_ids") or [])
    references = [
        {
            key: item[key]
            for key in ("id", "text", "source", "section")
            if item.get(key) is not None
        }
        for item in evidence
        if item.get("id") in cited
    ]
    hits = [
        {
            "id": item.get("id"),
            "text": item.get("text"),
            "rank": rank,
            "score": item.get("rrf_score"),
            "cited": item.get("id") in cited,
        }
        for rank, item in enumerate(evidence, start=1)
    ]

    conversation_id = next(
        (frame.get("conversation_id") for frame in frames if "conversation_id" in frame),
        None,
    )
    return {
        "conversation_id": conversation_id,
        "route": final.get("route"),
        "intent": final.get("intent"),
        "answer": answer,
        "references": references,
        "hits": hits,
        "actions": actions,
        "violations": captured.get("violations") or [],
        "signals": _signals(final),
    }
