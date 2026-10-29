"""主图的节点。

命名约定：**节点名跟出口名，函数名加 `_exit` 后缀**。
节点名出现在 trace / 边 / 路由表 / SSE 帧里，是**跨章不变的契约**；
函数体则在后续章节被整体替换（知识变子图、规则变 8 状态流水线）。
"""
from app.graph.routing import FALLBACK_ROUTE, INTENT_TO_ROUTE
from app.graph.state import ConversationState


async def resolve_reference(state: ConversationState) -> dict:
    """指代消解：本章透传，`resolved_query = raw_query`。

    Ch05 换成真实实现（结合历史把「它 / 这个 / 那单」补成完整问句），
    节点名与下游读的字段都不变。
    """
    return {"resolved_query": state.get("raw_query", "")}


async def classify_intent(state: ConversationState) -> dict:
    """意图分类：本章为占位，一律判「其他」→ 兜底出口。

    Task 2 换成真实 12 类分类（一次 LLM 调用，function_calling 结构化）。
    保持"判不出来就兜底"的语义，占位期也不会假装分类成功。
    """
    intent = "其他"
    return {
        "intent": intent,
        "intent_confidence": 0.0,
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
