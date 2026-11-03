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


async def classify(query: str) -> tuple[str, float, bool]:
    """一次 LLM 调用判意图，返回 `(intent, confidence, multi_intent)`。

    `multi_intent` 表示"这句话里还有别的诉求"。它与 confidence **不是一回事**：

    | 情况 | confidence | multi_intent |
    |---|---|---|
    | 句子模糊、系统拿不准 | 低 | false |
    | 两个诉求都很明确、主诉求也确定 | **可以很高** | **true** |

    混在一个数里，下游就没法区分"该追问澄清"还是"该让用户二选一"。

    **本章只产出这个信号，不消费**：Ch02 是单轮，没有 `pending_question` 可挂；
    Ch05 才会据此生成「您想先处理哪一件？」并记住等的是哪个选择。

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
        return DEFAULT_INTENT, 0.0, False

    intent = str(data.get("intent", ""))
    if intent not in INTENT_TO_ROUTE:          # 越界：模型返回了清单外的词
        return DEFAULT_INTENT, 0.0, False
    confidence = float(data.get("confidence", 0.0))
    return intent, confidence, bool(data.get("multi_intent", False))


async def classify_intent(state: ConversationState) -> dict:
    """意图分类节点：读 `resolved_query`（空则回落原话），写 intent / 置信度 / route。

    `route` 必须在这里写进 state——条件边函数没有写权限。它与 `route_by_intent`
    查同一张 `INTENT_TO_ROUTE`，所以 state 里的 route 和图实际走的分支不会不一致。

    `multi_intent` **暂不写进 state**：本章没有消费者（单轮、无追问），
    等 Ch05 做多意图澄清时再落 state + `pending_question`。
    """
    query = state.get("resolved_query") or state.get("raw_query", "")
    intent, confidence, _multi_intent = await classify(query)
    return {
        "intent": intent,
        "intent_confidence": confidence,
        "route": INTENT_TO_ROUTE.get(intent, FALLBACK_ROUTE),
    }


# ── 各出口 ────────────────────────────────────────────────
# 每个出口一个函数（或子图），便于独立替换与独立测试。
# Ch04 之后五个出口全部是真实实现，占位机制已删除。


async def human_exit(state: ConversationState) -> dict:
    """D 转人工：安慰 + 告知已转接，并给出 `transfer_human` 动作。

    **本章不建工单**——`create_ticket` 在 Ch04（P4-3）。现在只负责"把话说好 + 通知前端"。

    不打印工单号、不承诺处理时效：没有工单，就没有可承诺的东西。
    """
    return {
        "answer": "已为您转接人工客服，请稍候。人工客服会尽快与您联系。",
        "actions": [{"type": "transfer_human"}],
        "is_placeholder": False,
    }


# ── E 出口：三脚本（零模型）──────────────────────────────────
# 用关键词命中即可，不调模型：闲聊/致谢这类输入不值得一次 LLM 调用，
# 而且模型可能"过度发挥"，把一句"谢谢"答成一段推销。
#
# 局限（本章接受）：纯关键词，遇到"谢谢你，那退货怎么弄"这种**寒暄 + 业务**
# 的混合句，会靠 `_has_business_intent` 排除脚本、走兜底——比误判成"致谢"好。

_SOCIAL_WORDS = ("在吗", "在不在", "你好", "您好", "hi", "hello", "有人吗")
_THANKS_WORDS = ("谢谢", "多谢", "谢啦", "感谢", "好的", "好滴", "嗯", "收到", "辛苦了")
# 出现这些词说明用户在问业务，不能按寒暄/致谢处理
_BUSINESS_WORDS = (
    "退", "换", "订单", "物流", "快递", "发票", "优惠券", "券",
    "会员", "积分", "库存", "多少钱", "价保", "地址",
)

SOCIAL_REPLY = "在的，请问有什么可以帮您？"
THANKS_REPLY = "不客气，还有其他可以帮您的吗？"
UNKNOWN_REPLY = "抱歉，这个问题我暂时没能理解。您可以换个说法，或者点「转人工」由客服跟进。"


def _has_business_intent(text: str) -> bool:
    return any(word in text for word in _BUSINESS_WORDS)


def _matches(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


async def fallback_exit(state: ConversationState) -> dict:
    """E 兜底：闲聊 / 致谢 / 无法处理，三分支。

    顺序有讲究：**先排除业务意图**，再判断寒暄与致谢。
    否则「谢谢你，那退货怎么弄」会被答成「不客气」。
    """
    query = (state.get("resolved_query") or state.get("raw_query") or "").strip()

    if query and not _has_business_intent(query):
        if _matches(query, _SOCIAL_WORDS):
            return {"answer": SOCIAL_REPLY, "is_placeholder": False}
        if _matches(query, _THANKS_WORDS):
            return {"answer": THANKS_REPLY, "is_placeholder": False}

    return {
        "answer": UNKNOWN_REPLY,
        "actions": [{"type": "transfer_human"}],
        "is_placeholder": False,
    }
