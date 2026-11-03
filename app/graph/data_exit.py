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

from app.graph.order_select import ask_order
from app.graph.params import extract_order_id, extract_sku
from app.graph.state import ConversationState
from app.graph.tracing import traced
from app.tools.orders import list_stock_skus, query_stock

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


# ── 节点：Task 5–7 逐个替换 ────────────────────────────────


async def _stub(state: ConversationState) -> dict:
    """占位节点：不读写任何业务字段。"""
    return {}


# ── extract_params · 抽参数 ─────────────────────────────────


async def extract_params(state: ConversationState) -> dict:
    """从原话里抽订单号 / SKU。

    **只做字面提取，不查库**：存在性、归属、规格匹配都交给后面的节点。
    """
    query = state.get("resolved_query") or state.get("raw_query") or ""
    updates: dict = {}
    if order_id := extract_order_id(query):
        updates["order_id"] = order_id
    if sku_id := extract_sku(query):
        updates["sku_id"] = sku_id
    return updates


# ── validate · 按意图校验 ───────────────────────────────────


async def validate(state: ConversationState) -> dict:
    """只判断"缺什么"，**不查库**。

    写 `pending_question` 一个字段，守卫和 `need_info` 都读它——
    避免"守卫说缺订单号、追问却问型号"这种漂移。
    参数齐全时显式写 `None`，清掉上一轮可能残留的追问标记。
    """
    intent = state.get("intent")

    if intent in {"订单查询", "物流查询"} and not state.get("order_id"):
        return {"pending_question": "order_id"}
    if intent == "库存价格" and not state.get("sku_id"):
        return {"pending_question": "sku_id"}
    return {"pending_question": None}


# ── need_info · 补充信息 ────────────────────────────────────


async def need_info(state: ConversationState) -> dict:
    """缺什么问什么。

    **本轮只负责把问题问出去**，用户回答后是下一次图执行，
    从 `extract_params` 重新走。跨轮恢复靠 DB 的 `pending_question`。
    """
    pending = state.get("pending_question")
    if pending == "order_id":
        return await ask_order(state)
    if pending == "sku_id":
        return await _ask_sku()
    if pending == "spec":
        return await _ask_spec(state)
    return {}


async def _ask_sku() -> dict:
    """要型号。库里有几个就列几个；查不到就退化成文字追问。"""
    result = await list_stock_skus()
    if not result.ok or not result.data:
        return {
            "pending_question": "sku_id",
            "answer": "请提供商品型号（例如 ST-20），我帮您查库存。",
            "is_placeholder": False,
        }

    candidates = [{"sku_id": sku} for sku in result.data["skus"]]
    return {
        "sku_candidates": candidates,
        "pending_question": "sku_id",
        "answer": "请选择商品型号，我帮您查库存。",
        "actions": [{"type": "select_sku", "candidates": candidates}],
        "is_placeholder": False,
    }


async def _ask_spec(state: ConversationState) -> dict:
    """要规格。只列该型号的规格；查不到就退化成文字追问。"""
    sku_id = state.get("sku_id") or ""
    result = await query_stock(sku_id)
    if not result.ok or not result.data:
        return {
            "pending_question": "spec",
            "answer": "请提供一下规格（例如颜色或容量）。",
            "is_placeholder": False,
        }

    candidates = [
        {
            "sku_id": row["sku_id"],
            "spec": row["spec"],
            "price": row["price"],
            "stock": row["stock"],
            "status": row["status"],
        }
        for row in result.data.get("rows", [])
    ]
    return {
        "spec_candidates": candidates,
        "pending_question": "spec",
        "answer": f"{sku_id} 有这几个规格，您要哪一个？",
        "actions": [{"type": "select_spec", "candidates": candidates}],
        "is_placeholder": False,
    }


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

    real_nodes = {
        "extract_params": extract_params,
        "validate": validate,
        "need_info": need_info,
    }
    for name in NODE_NAMES:
        graph.add_node(name, traced("data", name, real_nodes.get(name, _stub)))

    graph.set_entry_point("extract_params")
    graph.add_edge("extract_params", "validate")
    graph.add_conditional_edges("validate", after_validate, VALIDATE_NEXT)
    graph.add_conditional_edges("query", after_query, QUERY_NEXT)
    graph.add_conditional_edges("need_info", after_need_info, NEED_INFO_NEXT)
    graph.add_edge("format", END)

    return graph.compile()
