"""API 入口。

设计依据：
  · L1：暴露 /api/chat（流式）与 /api/meta（评测快照用）
  · L7：每次请求一个 trace_id，贯穿日志
  · C6：chat 接收 client_message_id 用于幂等（后续实现）
"""
import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings, validate_or_die
from app.core.logging import new_trace_id, setup_logging, trace_id_var


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
    """流式对话骨架。P3 起由状态机接管。"""
    async def event_stream():
        yield _sse({"event": "ack"})
        for ch in f"（骨架）收到：{req.message}":
            await asyncio.sleep(0.005)
            yield _sse({"delta": ch})
        yield _sse({"event": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
