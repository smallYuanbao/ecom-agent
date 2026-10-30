"""API 入口。

设计依据：
  · L1：暴露 /api/chat（流式）与 /api/meta（评测快照用）
  · L7：每次请求一个 trace_id，贯穿日志
  · C6：chat 接收 client_message_id 用于幂等（后续实现）
"""
import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from openai import APITimeoutError
from pydantic import BaseModel, Field

from app.api.chat import ChatError, run_turn
from app.config import get_settings, validate_or_die
from app.core.logging import new_trace_id, setup_logging, trace_id_var

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    validate_or_die()          # L6：缺必填项直接失败，不带着空配置跑
    yield


app = FastAPI(title="ecom-agent", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    tid = request.headers.get("X-Trace-Id") or new_trace_id()
    token = trace_id_var.set(tid)
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        return response
    finally:
        trace_id_var.reset(token)


class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None
    client_message_id: str | None = Field(
        default=None, description="幂等键，同一会话内唯一（K4）")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/meta")
async def meta() -> dict:
    """评测快照用（L1 / K6）：不含任何密钥值。"""
    return {"version": app.version, **get_settings().safe_summary()}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """跑一轮对话，以 SSE 输出。

    帧顺序：`ack → route → delta → citations → actions → done`；
    异常时用 `error` 帧替换后续内容。

    **不把内部异常信息透给用户**——只给一句标准话术，细节进日志（带 trace_id）。
    """
    trace_id = trace_id_var.get()

    async def event_stream():
        try:
            async for frame in run_turn(req.message, req.conversation_id, trace_id):
                yield _sse(frame)
        except ChatError as exc:
            # 校验类错误：可以告诉用户具体原因
            yield _sse({"event": "error", "code": exc.code, "message": exc.message})
        except (TimeoutError, httpx.TimeoutException, APITimeoutError):
            logger.warning("对话超时", exc_info=True)
            yield _sse({
                "event": "error",
                "code": "timeout",
                "message": "服务响应超时，请稍后重试或转人工。",
            })
        except Exception:
            logger.exception("对话失败")
            yield _sse({
                "event": "error",
                "code": "internal_error",
                "message": "服务暂时不可用，请稍后重试或转人工。",
            })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
