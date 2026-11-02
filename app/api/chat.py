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
| `timeout` | 模型或检索超时 |
| `internal_error` | 其余未预期异常（D9 四类之外，仅用于传输层） |

`not_found` / `forbidden` 属于会话归属校验，Ch06 才接。
"""
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.core.tracer import record_span
from app.db.models import utcnow
from app.graph.build import build_graph

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 500


class ChatError(Exception):
    """带错误码的对话异常，直接映射成 `error` 帧。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)


async def run_turn(
    message: str,
    conversation_id: int | None,
    trace_id: str,
    *,
    graph: Any | None = None,
) -> AsyncIterator[dict]:
    """跑一轮对话，逐个产出 SSE 帧（dict）。

    `graph` 可注入，测试用假图替换；生产走 `build_graph()`。
    """
    text = (message or "").strip()
    if not text:
        raise ChatError("invalid_arg", "消息不能为空")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ChatError("invalid_arg", f"消息过长（上限 {MAX_MESSAGE_CHARS} 字）")

    engine = graph if graph is not None else build_graph()
    state = {
        "raw_query": text,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        # Ch02 没有登录体系；订单归属校验需要一个固定的演示身份。
        # Ch06 接真实鉴权后改成从请求里取登录用户。
        "user_id": get_settings().demo_user_id,
    }

    yield {"event": "ack"}

    final: dict = {}
    turn_start = utcnow()          # 整个轮次的起点，`turn:total` 用它
    node_started = turn_start
    wall_start = time.perf_counter()

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
                }
        node_started = node_ended

    answer = final.get("answer") or ""
    if answer:
        yield {"delta": answer}

    citations = final.get("citation_ids") or []
    if citations:
        yield {"event": "citations", "items": citations}

    actions = final.get("actions") or []
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
        },
    )
    yield {"event": "done"}
