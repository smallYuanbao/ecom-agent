"""B 出口：数据查询子图（5 状态 / 8 转移）。

订单 / 物流 / 库存三类查询。**内部零模型调用**：
参数用正则 + 数据库匹配，回答用模板。

```
extract_params ─► validate ─┬─► query ─┬─► format ─► END
                            │          ├─► need_info ─► END
                            └─► need_info       └─► 转人工
```

**「转人工」不是节点**：主图已有 `human` 出口，子图把转人工写成一条边到 `END`。
"""
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.state import ConversationState
from app.graph.tracing import traced

# 5 个节点的名字（与本文件的函数名一致）
NODE_NAMES: tuple[str, ...] = (
    "extract_params",
    "validate",
    "query",
    "need_info",
    "format",
)

# 条件边的键 → 目标节点
VALIDATE_NEXT = {"query": "query", "need_info": "need_info"}
QUERY_NEXT = {"format": "format", "need_info": "need_info", "human": END}
NEED_INFO_NEXT = {"end": END}


# ── 节点：Task 4–7 逐个替换 ────────────────────────────────


async def _stub(state: ConversationState) -> dict:
    """占位节点：不读写任何业务字段。"""
    return {}


# ── 守卫：只返回键，不写 state ──────────────────────────────


def after_validate(state: ConversationState) -> str:
    """**校验之后**：`validate` 写了 `pending_question` 就追问，否则查数据。"""
    return "need_info" if state.get("pending_question") else "query"


def after_query(state: ConversationState) -> str:
    """**查询之后**：ok 去组装；查不到/参数不合法去追问；其余转人工。"""
    code = state.get("tool_code")
    if code == "ok":
        return "format"
    if code in {"not_found", "invalid_arg"}:
        return "need_info"
    return "human"


def after_need_info(state: ConversationState) -> str:
    """**追问之后**：本轮结束。用户回答后是下一次图执行。"""
    return "end"


# ── 子图 ────────────────────────────────────────────────────


def build_data_graph() -> CompiledStateGraph:
    """编译 B 出口子图。主图把它挂在 `data` 节点上。"""
    graph: Any = StateGraph(ConversationState)

    for name in NODE_NAMES:
        graph.add_node(name, traced("data", name, _stub))

    graph.set_entry_point("extract_params")
    graph.add_edge("extract_params", "validate")
    graph.add_conditional_edges("validate", after_validate, VALIDATE_NEXT)
    graph.add_conditional_edges("query", after_query, QUERY_NEXT)
    graph.add_conditional_edges("need_info", after_need_info, NEED_INFO_NEXT)
    graph.add_edge("format", END)

    return graph.compile()
