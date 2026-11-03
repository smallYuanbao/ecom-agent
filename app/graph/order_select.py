"""缺订单号时的候选列表（B / C 两个出口共用）。

**为什么列候选而不是让用户打字**：用户记不住订单号。
点一下比敲 4–6 位数字可靠，也顺带说明"这单是你的"。
"""
from app.core.format import yuan_text
from app.db.models import Order
from app.db.repo_order import list_user_orders
from app.graph.state import ConversationState

# 列给用户的候选订单数：超过 3 个选择困难，也占满屏幕
CANDIDATE_LIMIT = 3


def candidate(order: Order) -> dict:
    """候选订单的展示形状（给前端画卡片，不暴露内部字段）。"""
    title = " ".join(part for part in (order.sku_id, order.spec) if part)
    return {
        "order_id": order.id,
        "title": title,
        "status": order.status,
        "amount": order.amount,                   # 分
        "amount_text": yuan_text(order.amount),   # 展示用
        "created_at": order.created_at.strftime("%Y-%m-%d"),
    }


async def ask_order(state: ConversationState) -> dict:
    """要订单号。有 `user_id` 就列最近 3 单；没有就退化成文字追问。

    只返回**追问载荷**，不写 `validate_retry`——那是 C 出口自己的计数。
    """
    user_id = state.get("user_id")
    candidates: list[dict] = []
    if user_id:
        orders = await list_user_orders(user_id=user_id)
        candidates = [candidate(order) for order in orders[:CANDIDATE_LIMIT]]

    if not candidates:
        return {
            "pending_question": "order_id",
            "answer": "请提供一下订单号（在「我的订单」里可以看到）。",
            "is_placeholder": False,
        }

    return {
        "order_candidates": candidates,
        "pending_question": "order_id",
        "answer": "我查到您最近有这几笔订单，请问是哪一单？",
        "actions": [{"type": "select_order", "candidates": candidates}],
        "is_placeholder": False,
    }
