"""主图的节点。

命名约定：**节点名跟出口名，函数名加 `_exit` 后缀**。
节点名出现在 trace / 边 / 路由表 / SSE 帧里，是**跨章不变的契约**；
函数体则在后续章节被整体替换（知识变子图、规则变 8 状态流水线）。
"""
from app.core.llm import LLMFormatError, structured_call
from app.core.prompts import (
    INTENT_PARAMETERS,
    INTENT_TOOL_NAME,
    intent_messages,
)
from app.graph.routing import DEFAULT_INTENT, FALLBACK_ROUTE, INTENT_TO_ROUTE
from app.graph.state import ConversationState


async def resolve_reference(state: ConversationState) -> dict:
    """指代消解：本章透传，`resolved_query = raw_query`。

    Ch05 换成真实实现（结合历史把「它 / 这个 / 那单」补成完整问句），
    节点名与下游读的字段都不变。
    """
    return {"resolved_query": state.get("raw_query", "")}


async def classify(query: str) -> tuple[str, float]:
    """一次 LLM 调用判意图，返回 `(intent, confidence)`。

    **可恢复的错误一律降级成「其他」**（模型没按格式返回、意图越界）——
    分类失败最多是这轮走兜底，不该让请求挂掉。
    **不可恢复的错误直接上抛**（鉴权失败、超时、限流）——那是配置或服务问题，
    吞掉会变成"所有问题都答不上来"的假象，反而更难排查。
    """
    try:
        data = await structured_call(
            INTENT_TOOL_NAME, INTENT_PARAMETERS, intent_messages(query)
        )
    except LLMFormatError:
        return DEFAULT_INTENT, 0.0

    intent = str(data.get("intent", ""))
    if intent not in INTENT_TO_ROUTE:          # 越界：模型返回了清单外的词
        return DEFAULT_INTENT, 0.0
    return intent, float(data.get("confidence", 0.0))


async def classify_intent(state: ConversationState) -> dict:
    """意图分类节点：读 `resolved_query`（空则回落原话），写 intent / 置信度 / route。

    `route` 必须在这里写进 state——条件边函数没有写权限。它与 `route_by_intent`
    查同一张 `INTENT_TO_ROUTE`，所以 state 里的 route 和图实际走的分支不会不一致。
    """
    query = state.get("resolved_query") or state.get("raw_query", "")
    intent, confidence = await classify(query)
    return {
        "intent": intent,
        "intent_confidence": confidence,
        "route": INTENT_TO_ROUTE.get(intent, FALLBACK_ROUTE),
    }


# 占位话术：明确告知"开发中"，绝不出现任何金额（Ch02 验收第 3 条）
PLACEHOLDER_TEXT: dict[str, str] = {
    "knowledge": "知识库检索正在开发中，您可以先转人工处理。",
    "data": "订单与库存查询正在开发中，您可以先转人工处理。",
    "rules": "退费核算正在开发中，您可以先转人工处理。",
    "human": "转人工功能正在开发中，请稍后再试。",
    "fallback": "我暂时没能理解您的问题，您可以换个说法，或转人工处理。",
}


def _placeholder(route: str) -> dict:
    """五个占位出口共用的返回体：明确告知 + 带转人工动作，不返回任何数字。"""
    return {
        "answer": PLACEHOLDER_TEXT[route],
        "is_placeholder": True,
        "actions": [{"type": "transfer_human"}],
    }


# 五个出口各写一个函数（而不是共用单个 placeholder）：
# 它们将来各自演化，必须能被**独立替换**，且保证"每个出口说自己的话"。


async def knowledge_exit(state: ConversationState) -> dict:
    """A 知识出口：Task 7 换成知识子图（retrieve → gate → generate）。"""
    return _placeholder("knowledge")


async def data_exit(state: ConversationState) -> dict:
    """B 数据出口：Ch04 换成参数校验 → 工具 → 结果校验。"""
    return _placeholder("data")


async def rules_exit(state: ConversationState) -> dict:
    """C 规则出口：Ch03 换成 8 状态确定性流水线（金额由代码算）。"""
    return _placeholder("rules")


async def human_exit(state: ConversationState) -> dict:
    """D 转人工：Task 8 换成真实话术 + create_ticket。"""
    return _placeholder("human")


async def fallback_exit(state: ConversationState) -> dict:
    """E 兜底：Task 8 换成三脚本（闲聊 / 致谢 / 无法处理）。"""
    return _placeholder("fallback")
