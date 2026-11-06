"""意图 → 出口的路由表与条件边函数。

**单一来源**：`classify_intent` 写 state 里的 `route` 时查这张表，`route_by_intent`
决定图走哪条边时也查这张表——两处读同一份数据，state 与图的实际分支不会漂移。

设计依据：意图文档 §三（12 意图 → 5 出口）。
"""
from app.graph.state import ConversationState

# 12 类意图 → 5 个出口。写死为常量：可审阅、可测试、无模型参与。
INTENT_TO_ROUTE: dict[str, str] = {
    # A 知识出口：查规则、查说明（还没锁定到某一单）
    "政策问答": "knowledge",
    "商品规格": "knowledge",
    "优惠券活动": "knowledge",
    "会员权益": "knowledge",
    # B 数据出口：查这一单 / 这批货的事实
    "订单查询": "data",
    "物流查询": "data",
    "库存价格": "data",
    # C 规则出口：要动某一单（金额由代码算）
    "退款申请": "rules",
    "价保申请": "rules",
    "订单变更": "rules",
    # D 转人工
    "投诉": "human",
    # E 兜底
    "闲聊": "fallback",
}

# 有效意图 = 路由表的键（单一来源，不再单独维护一份清单）
INTENTS: tuple[str, ...] = tuple(INTENT_TO_ROUTE)

# 分类失败 / 越界 / 拿不准时的默认值。注意它不是意图，只是兜底槽位
DEFAULT_INTENT = "其他"

FALLBACK_ROUTE = "fallback"

# 五个出口的节点名。build.py 与 resume 短路共用一份，避免两处漂移
ROUTE_NAMES: tuple[str, ...] = ("knowledge", "data", "rules", "human", "fallback")


def route_by_intent(state: ConversationState) -> str:
    """条件边函数：只返回节点名，不写 state（条件边没有写权限）。

    用 `.get(默认值)` 而不是 `[]`：模型偶尔会返回没见过的意图词，
    这时应保守兜底，而不是让整轮请求抛 `KeyError`。
    """
    return INTENT_TO_ROUTE.get(state.get("intent", ""), FALLBACK_ROUTE)
