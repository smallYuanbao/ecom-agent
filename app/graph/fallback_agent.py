"""E 出口子图：gate → agent（只读 ReAct）/ scripted（三脚本）。

**ReAct 是升级路径，不是兜底入口**：
「你好」直接走脚本，只有带订单号/SKU/数据关键词的长尾才进 agent。
"""
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core import react
from app.graph.nodes import fallback_exit
from app.graph.params import extract_order_id, extract_sku
from app.graph.state import ConversationState

NODE_NAMES = ("gate", "agent", "scripted")

DATA_KEYWORDS = (
    "订单",
    "物流",
    "快递",
    "到哪",
    "库存",
    "有货",
    "价格",
    "多少钱",
)


def needs_tools(text: str) -> bool:
    """值不值得上 ReAct：有明确实体或数据类关键词。"""
    query = text or ""
    return bool(
        extract_order_id(query)
        or extract_sku(query)
        or any(keyword in query for keyword in DATA_KEYWORDS)
    )


async def gate(state: ConversationState) -> dict:
    query = state.get("resolved_query") or state.get("raw_query") or ""
    return {"needs_tools": needs_tools(query)}


async def agent(state: ConversationState) -> dict:
    query = state.get("resolved_query") or state.get("raw_query") or ""
    result = await react.run(
        query,
        history=state.get("history") or [],
        user_id=state.get("user_id") or "",
        trace_id=state.get("trace_id"),
    )
    if result.answer and result.code in {"ok", "step_limit"}:
        return {
            "answer": result.answer,
            "citation_ids": result.citations,
            "actions": [],
            "is_placeholder": False,
        }
    # 空回答 / 模型失败：交给 scripted 兜底
    return {"answer": ""}


async def scripted(state: ConversationState) -> dict:
    return await fallback_exit(state)


def after_gate(state: ConversationState) -> str:
    return "agent" if state.get("needs_tools") else "scripted"


def after_agent(state: ConversationState) -> str:
    return "scripted" if not state.get("answer") else "end"


def build_fallback_graph() -> CompiledStateGraph:
    graph: StateGraph = StateGraph(ConversationState)
    graph.add_node("gate", gate)
    graph.add_node("agent", agent)
    graph.add_node("scripted", scripted)

    graph.set_entry_point("gate")
    graph.add_conditional_edges(
        "gate",
        after_gate,
        {"agent": "agent", "scripted": "scripted"},
    )
    graph.add_conditional_edges(
        "agent",
        after_agent,
        {"scripted": "scripted", "end": END},
    )
    graph.add_edge("scripted", END)
    return graph.compile()
