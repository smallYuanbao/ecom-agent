"""Prompt 与结构化 schema。

本章只有意图分类一个 prompt；Ch06 会统一成四区结构（系统/用户/检索/工具结果）。
原则：**prompt 里出现的类目必须与 `INTENT_TO_ROUTE` 完全一致**——直接从路由表生成，
避免"prompt 里有 12 类、代码里只有 11 类"这种漂移。
"""
from typing import Any

from app.graph.routing import DEFAULT_INTENT, INTENTS

INTENT_TOOL_NAME = "emit_intent"

INTENT_SYSTEM = f"""## 角色
你是电商售后客服的意图识别器。只判断这句话属于哪个意图，**不要回答用户的问题**。

## 12 类意图（必须从这个清单里选）
1. 政策问答 —— 问平台规则本身，还没锁定到某一单（如「退货政策是什么」「退货运费谁出」）
2. 商品规格 —— 问商品的参数、尺寸、材质、适用年龄（如「ST-60 有多重」）
3. 优惠券活动 —— 问券怎么用、有什么活动、能不能叠加（如「满 300 减 50 和会员折扣能一起用吗」）
4. 会员权益 —— 问等级、积分、会员折扣（如「积分怎么算」）
5. 订单查询 —— 查某一单的状态、金额、下单时间（如「订单 1001 什么状态」）
6. 物流查询 —— 查快递到哪了、什么时候到（如「我的快递到哪了」）
7. 库存价格 —— 问某商品有没有货、现在多少钱（如「ST-20 还有货吗」）
8. 退款申请 —— 要对某一单退货或退款（如「订单 1001 我要退了」）
9. 价保申请 —— 要求补差价（如「上周买的降价了，能补差价吗」）
10. 订单变更 —— 改地址、取消订单、换规格（如「能改收货地址吗」）
11. 投诉 —— 对商品或服务不满，要说法、要追责
12. 闲聊 —— 寒暄、致谢、与购物无关的话题

## 四对最容易混的，必须分清
- 「退货运费谁出」= 政策问答（问规则，没锁定某一单）；「我要退这一单」= 退款申请
- 「ST-60 有多重」= 商品规格；「ST-60 还有货吗 / 多少钱」= 库存价格
- 「订单 1001 到哪了」= 订单查询；「订单 1001 能退吗」= 退款申请
- 「你们这什么破服务」= 投诉；「在吗」= 闲聊

## 拿不准怎么办
拿不准、或者这句话跟电商售后无关时，选「{DEFAULT_INTENT}」，**不要硬塞进某一类**。
「{DEFAULT_INTENT}」是兜底槽位，选它不会丢脸，硬塞才会让下游走错出口。

## 置信度
给出 0–1 的 confidence：
- 措辞明确、能对上上面的例子 → 0.85 以上
- 能判断但有点含糊 → 0.5–0.85
- 几乎靠猜 → 0.5 以下（同时考虑选「{DEFAULT_INTENT}」）

## 输出
只通过工具返回结果，不要用自然语言解释。
"""

# 意图候选：12 类 + 兜底槽位
_ALLOWED_INTENTS = (*INTENTS, DEFAULT_INTENT)

INTENT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(_ALLOWED_INTENTS)},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["intent", "confidence"],
}


def intent_messages(query: str) -> list[dict[str, str]]:
    """意图分类的消息列表。用户输入用标签包裹，为 Ch06 的注入防御留好位置。"""
    return [
        {"role": "system", "content": INTENT_SYSTEM},
        {"role": "user", "content": f"<user_input>{query}</user_input>"},
    ]
