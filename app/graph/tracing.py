"""子图节点的 `trace_span` 包装。

父图的 `run_turn` 只会在子图整体结束时看到一行 `node:<出口>`——
里面跑了哪几个状态、各花多久，看不到。spec 要求"每个状态落一行"，
所以子图自己给每个节点套一层。

命名规则：span 名 = `{prefix}:{name}`，如 `rules:judge` / `data:query`。
写失败不影响主流程（`record_span` 自己兜底）。
"""
from app.core.tracer import record_span
from app.db.models import utcnow
from app.graph.state import ConversationState


def traced(prefix: str, name: str, node):
    """返回一个带 span 的节点包装。

    `name` 是节点键名（如 `intake`），不是函数名——它要和图的节点名一致，
    否则 trace 里对不上边表。
    """

    async def wrapper(state: ConversationState) -> dict:
        start = utcnow()
        result = await node(state)
        trace_id = state.get("trace_id")
        if trace_id:
            await record_span(
                trace_id=trace_id,
                name=f"{prefix}:{name}",
                start_at=start,
                end_at=utcnow(),
                attrs={"keys": sorted((result or {}).keys())},
            )
        return result

    wrapper.__name__ = f"traced_{prefix}_{name}"
    return wrapper
