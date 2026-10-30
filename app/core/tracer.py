"""把节点执行写成 `trace_span` 行（L7）。

**结构对齐 OpenTelemetry 的 span 模型**：

| 字段 | 含义 |
|---|---|
| `trace_id` | 一次请求的 id，与日志里的 `trace_id` 相同 |
| `span_id` | 单个节点/工具调用的 id |
| `parent_span_id` | 父 span，用来还原调用树（本章留空，`Ch09` 若要树形再加） |
| `name` | 如 `node:classify_intent` |
| `start_at` / `end_at` | 起止时间；`end_at` 为 NULL 表示未结束或异常中断 |
| `attrs` | JSON：耗时、参数摘要、错误码 |

**为什么要落库而不是只打日志**：回放一次请求时，"每个节点花了多久、按什么顺序跑"
是排查超时与路由错误的第一手证据，日志里翻起来太慢。
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TraceSpan, utcnow
from app.db.session import session_scope


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


async def record_span(
    *,
    trace_id: str,
    name: str,
    start_at: datetime,
    end_at: datetime | None = None,
    attrs: dict[str, Any] | None = None,
    parent_span_id: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """写一行 span。

    **写失败不影响主流程**：追踪是观测手段，不是业务依赖。
    数据库写不进去时宁可丢一条 span，也不能让用户这轮对话失败。
    """
    try:
        async with session_scope(session) as s:
            s.add(
                TraceSpan(
                    trace_id=trace_id,
                    span_id=new_span_id(),
                    parent_span_id=parent_span_id,
                    name=name,
                    start_at=start_at,
                    end_at=end_at or utcnow(),
                    attrs=None if attrs is None else json.dumps(attrs, ensure_ascii=False),
                )
            )
    except Exception:
        logging.getLogger(__name__).warning("写入 trace_span 失败", exc_info=True)
