"""主图：resolve_reference → classify_intent → 按意图分流到五个出口。

出口的实现进度：

| 出口 | 状态 |
|---|---|
| `knowledge` | ✅ 真实子图（retrieve → gate → generate / weak_fallback） |
| `data` / `rules` / `human` / `fallback` | 占位（明确告知"开发中"） |

**节点名与边一个都不改**——这就是"行走骨架"：形状先立住，函数体逐章替换。
"""
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.knowledge import build_knowledge_graph
from app.graph.nodes import (
    classify_intent,
    data_exit,
    fallback_exit,
    human_exit,
    resolve_reference,
    rules_exit,
)
from app.graph.routing import route_by_intent
from app.graph.state import ConversationState

# 出口节点名（与路由表的取值、trace 里的名字保持一致）
EXIT_NODES: tuple[str, ...] = ("knowledge", "data", "rules", "human", "fallback")


def build_graph() -> CompiledStateGraph:
    """构建并编译主图。无 checkpointer：本章不跨请求保存状态（跨轮状态在 DB）。"""
    g = StateGraph(ConversationState)

    g.add_node("resolve_reference", resolve_reference)   # Ch05 换真实实现
    g.add_node("classify_intent", classify_intent)       # Task 2 换成真实分类
    g.add_node("knowledge", build_knowledge_graph())     # 编译后的子图可直接当节点
    g.add_node("data", data_exit)                        # Ch04 换成真实查询
    g.add_node("rules", rules_exit)                      # Ch03 换成 8 状态流水线
    g.add_node("human", human_exit)                      # Task 8 换成真实话术
    g.add_node("fallback", fallback_exit)                # Task 8 换成三脚本

    g.set_entry_point("resolve_reference")
    g.add_edge("resolve_reference", "classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        # 返回值 → 节点名。键必须与 route_by_intent 的返回集合完全一致
        {route: route for route in EXIT_NODES},
    )
    for name in EXIT_NODES:
        g.add_edge(name, END)

    return g.compile()
