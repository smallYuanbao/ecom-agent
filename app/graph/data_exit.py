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
from datetime import datetime
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.format import yuan_text
from app.graph.order_select import ask_order
from app.graph.params import extract_order_id, extract_sku
from app.graph.state import ConversationState
from app.graph.tracing import traced
from app.graph.transfer import transfer
from app.tools.orders import (
    list_stock_skus,
    query_logistics,
    query_order,
    query_stock,
)

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


# ── query · 查数据 ─────────────────────────────────────────

REQUIRED_ORDER = ("order_id", "status", "amount", "created_at")
REQUIRED_STOCK = ("sku_id", "spec", "stock", "price", "status")


def match_spec(query: str, rows: list[dict]) -> str | None:
    """从用户原话里匹配规格。

    - 多个命中取**最长**：「灰」不能匹配成「星光灰」
    - 只有一个规格时，用户不说也能确定
    - 多个规格且没命中 → 返回 `None`，让 `need_info` 去追问
    """
    hits = [row["spec"] for row in rows if row.get("spec") and row["spec"] in query]
    if hits:
        return max(hits, key=len)
    if len(rows) == 1:
        return rows[0]["spec"]
    return None


def _complete(data: dict, fields: tuple[str, ...]) -> bool:
    return all(data.get(field) is not None for field in fields)


def _complete_tracking(data: dict) -> bool:
    tracking = data.get("tracking")
    if tracking is None:
        return _complete(data.get("order") or {}, REQUIRED_ORDER)
    return bool(tracking.get("tracking_no")) and bool(tracking.get("latest_status"))


def _on_failure(intent: str, code: str | None) -> dict:
    """工具失败 → 追问或转人工。`not_found` / `invalid_arg` 都还能靠用户补。"""
    if code in {"not_found", "invalid_arg"}:
        pending = "sku_id" if intent == "库存价格" else "order_id"
        return {"tool_code": code, "pending_question": pending}
    return transfer(f"tool_{code or 'unknown'}")


async def query(state: ConversationState) -> dict:
    """按意图查数据，写 `tool_code` 与 `data_result`。

    **结果校验也在这里**：字段不完整时直接置 `internal`（转人工），
    保证进入 `format` 的数据永远是完整的。
    """
    intent = state.get("intent")
    user_id = state.get("user_id") or ""
    text = state.get("resolved_query") or state.get("raw_query") or ""

    if intent == "订单查询":
        result = await query_order(state.get("order_id") or "", user_id=user_id)
        if not result.ok:
            return _on_failure(intent, result.code)
        if not _complete(result.data or {}, REQUIRED_ORDER):
            return transfer("data_incomplete")
        return {"tool_code": "ok", "data_result": result.data}

    if intent == "物流查询":
        order = await query_order(state.get("order_id") or "", user_id=user_id)
        if not order.ok:
            return _on_failure(intent, order.code)

        data = {"order": order.data, "tracking": None}
        tracking_no = (order.data or {}).get("tracking_no")
        if tracking_no:
            tracking = await query_logistics(tracking_no, user_id=user_id)
            if not tracking.ok:
                return _on_failure(intent, tracking.code)
            data["tracking"] = tracking.data

        if not _complete_tracking(data):
            return transfer("data_incomplete") # 数据不完整
        return {"tool_code": "ok", "data_result": data}

    if intent == "库存价格":
        listed = await query_stock(state.get("sku_id") or "")
        if not listed.ok:
            return _on_failure(intent, listed.code)

        rows = (listed.data or {}).get("rows", [])
        spec = match_spec(text, rows)
        if spec is None:
            return {"tool_code": "not_found", "pending_question": "spec"}

        row = next(r for r in rows if r["spec"] == spec)
        if not _complete(row, REQUIRED_STOCK):
            return transfer("data_incomplete")
        return {"tool_code": "ok", "data_result": row}

    # 理论到不了（路由只把三个数据意图送进来）；fail-closed
    return transfer("unsupported_intent")


# ── format · 组装回答 ──────────────────────────────────────


def _time_text(value: str | None) -> str:
    """ISO 时间 → 「2026-10-29 10:00」；解析失败原样返回。"""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _stock_text(stock: int, status: str) -> str:
    """库存状态 → 人话。`on_sale` 且 0 件也算"暂时无货"。"""
    if status == "sold_out":
        return "已售罄"
    if status == "presale":
        return "预售中"
    if status == "on_sale" and stock > 0:
        return f"有货（{stock} 件）"
    return "暂时无货"


def _recent_nodes(nodes: list[dict]) -> str:
    """取最近 3 条，按时间倒序拼成人话。"""
    recent = list(reversed(nodes[-3:]))
    return "；".join(
        f"{node.get('time', '')} {node.get('status', '')} {node.get('desc', '')}".strip()
        for node in recent
    )


async def format(state: ConversationState) -> dict:
    """把 `data_result` 拼成人话。

    **模板里所有数字都来自工具返回值**：金额统一 `yuan_text`，不做二次计算。
    """
    intent = state.get("intent")
    data = state.get("data_result") or {}

    if intent == "订单查询":
        text = (
            f"订单 {data['order_id']}：状态 {data['status']}；"
            f"实付 {yuan_text(data['amount'])}；"
            f"下单时间 {_time_text(data['created_at'])}。"
        )
        if data.get("tracking_no"):
            text += f"物流单号 {data['tracking_no']}。"
        return {"answer": text, "is_placeholder": False}

    if intent == "物流查询":
        tracking = data.get("tracking")
        if tracking is None:
            order = data.get("order") or {}
            return {
                "answer": (
                    f"订单 {order.get('order_id')} 当前状态：{order.get('status')}，"
                    "暂无物流信息。"
                ),
                "is_placeholder": False,
            }
        text = f"快递 {tracking['tracking_no']}：最新状态 {tracking['latest_status']}。"
        recent = _recent_nodes(tracking.get("nodes") or [])
        if recent:
            text += f"最近轨迹：{recent}。"
        return {"answer": text, "is_placeholder": False}

    if intent == "库存价格":
        return {
            "answer": (
                f"{data['sku_id']} {data['spec']}："
                f"{_stock_text(data['stock'], data['status'])}，"
                f"售价 {yuan_text(data['price'])}。"
            ),
            "is_placeholder": False,
        }

    return transfer("unsupported_intent")


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
        "query": query,
        "need_info": need_info,
        "format": format,
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
