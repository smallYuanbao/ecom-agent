"""Prompt 与结构化 schema。

三个 prompt：意图分类、退款要素抽取、知识问答。Ch06 会统一成四区结构
（系统/用户/检索/工具结果）。

原则：**prompt 里出现的类目必须与代码里的枚举完全一致**——直接从常量生成，
避免"prompt 里有 12 类、代码里只有 11 类"这种漂移。
"""
import json
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

## 一句话里有多个诉求时
例如「能退吗？顺便查下物流」——**选最主要的一个**（用户最后强调的、或更明确的那句），
同时把 `multi_intent` 置为 **true**。

不要因为"另一个诉求答不了"就改变选择；也不要试图一次回答两件事。
拿不准哪个是最主要的，就把 `multi_intent` 置为 true 并适当降低 confidence。

## 置信度
给出 0–1 的 confidence，**只表示"对选中的这个意图有多确定"**：
- 措辞明确、能对上上面的例子 → 0.85 以上
- 能判断但有点含糊 → 0.5–0.85
- 几乎靠猜 → 0.5 以下（同时考虑选「{DEFAULT_INTENT}」）

**不要用 confidence 表达"这句话有两个诉求"**——那是 `multi_intent` 的职责。
一句话里有两个明确诉求、你也能确定主诉求时，confidence 可以是 0.8 以上，`multi_intent` 为 true。

## 输出
只通过工具返回结果，不要用自然语言解释。
"""

# 意图候选：12 类 + 兜底槽位
_ALLOWED_INTENTS = (*INTENTS, DEFAULT_INTENT)

INTENT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(_ALLOWED_INTENTS)},
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "对『选中的这一个意图』的确定度，不表示句子有没有多个诉求",
        },
        "multi_intent": {
            "type": "boolean",
            "description": "这句话里是否还有别的诉求（下游据此决定要不要让用户二选一）",
        },
    },
    "required": ["intent", "confidence", "multi_intent"],
}


def intent_messages(query: str) -> list[dict[str, str]]:
    """意图分类的消息列表。用户输入用标签包裹，为 Ch06 的注入防御留好位置。"""
    return [
        {"role": "system", "content": INTENT_SYSTEM},
        {"role": "user", "content": f"<user_input>{query}</user_input>"},
    ]


# ══════════════════════════════════════════════════════════════
# C 出口：退款要素抽取（OpenAI function_calling 强约束）
# ══════════════════════════════════════════════════════════════

EXTRACT_TOOL_NAME = "emit_refund_draft"

EXTRACT_REASONS = ("quality", "no_reason", "other")

EXTRACT_SYSTEM = """## 角色
你从用户的话里抽取「退款申请」需要的要素。**只抽取用户明确说过的内容，不要推测。**

## 四个要素

### reason_code（退款原因，三选一）
- `quality`：商品有质量问题。例如「有异响」「坏了」「漏水」「少件」「味道不对」
- `no_reason`：不想要了。例如「不想要了」「买错了」「不喜欢」「用不上」
- `other`：能看出在申请退款，但原因不属于上面两类
- **用户没提原因，就省略这个字段**，不要硬塞 `other`

### item_count（要退几件）
- 只有用户明确说了数量才填：「退一件」「两件都退」「买 3 件退 2 件」
- 「试了一次」「用了一下」是在说使用次数，**不是在说件数**，所以不要填 `item_count`
- **但不要因此忽略这句话**——它说明商品被用过了，要同时去判断 `condition_ok`
- 没提数量就省略。**不要默认填 1**——那会让部分退款算错金额

### condition_ok（商品是否完好）
- `true`：只拆开看了一下；快递盒撕了但商品没动；试穿且吊牌还在；
  **只拆了快递外箱、内包装（塑封/内袋）没动**
- `false`：剪了吊牌；穿出门；用过了、洗过、留下使用痕迹；
  **「试了一次」「用了一下」这类明确的使用**
- **拿不准就省略**（等于 null），三种情况：
  1. 用户完全没提商品状态
  2. **用户自己表达了不确定**——「不太确定算不算用过」「我也不知道算不算拆封」
  3. 描述有歧义，两种判断都说得通
- 判据：**用户在陈述事实就判断，用户在表达犹豫就留空**。
  不要替用户下结论——猜错的代价是把能退的判成不能退

### packaging_opened（包装是否拆封）
- 指**直接接触商品的那层**：塑封、内袋、礼盒封条
- 只拆了快递外箱、里面塑封没动 → `false`
- 没提包装的事就省略

## 输出
只通过工具返回。没提到的字段直接省略——不要填 `"unknown"`、`"null"`、`0`，
那些值会被下游当成真实信息。
"""

EXTRACT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason_code": {
            "type": "string",
            "enum": list(EXTRACT_REASONS),
            "description": "退款原因。用户没提就不要填。",
        },
        "item_count": {
            "type": "integer",
            "minimum": 1,
            "description": "明确提到的退货件数。没说不要填，不要默认 1。",
        },
        "condition_ok": {
            "type": "boolean",
            "description": "商品是否完好。拿不准就不要填（三态的第三态）。",
        },
        "packaging_opened": {
            "type": "boolean",
            "description": "直接接触商品的包装是否拆封。只拆快递箱填 false。",
        },
    },
    # 刻意不设 required：四个要素都可能"用户没说"，
    # 强制必填会逼模型编一个值，正是这套设计最怕的
    "required": [],
}


def extract_messages(query: str) -> list[dict[str, str]]:
    """退款要素抽取的消息列表。用户输入用标签包裹，为 Ch06 的注入防御留位置。"""
    return [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": f"<user_input>{query}</user_input>"},
    ]


# ══════════════════════════════════════════════════════════════
# C 出口：退款资格判定（模型判，代码复算）
# ══════════════════════════════════════════════════════════════

JUDGE_TOOL_NAME = "emit_judgement"

# 封闭集合：与 spec「reason_code 是封闭集合」一致
JUDGE_REASONS = (
    "ok",
    "out_of_window",
    "not_applicable",
    "not_intact",
    "wrong_status",
    "reject_then_refund",
)

JUDGE_SYSTEM = """## 角色
你是电商售后客服的退款资格判定器。**只判「能不能退」**，不算金额、不写回复。

## 判定规则（按顺序，第一条命中就返回）
1. 状态 = 待付款 → 不可退，`wrong_status`
2. 状态 = 退款中 → 不可退，`wrong_status`（不可重复申请）
3. 状态 = 已关闭 → 不可退，`wrong_status`
4. 状态 = 待发货 → 可退，`ok`（整单取消，全额退）
5. 状态 = 已发货且未签收 → 可退，`reject_then_refund`（拒收后全额退）
6. 商品类别属于法定不适用（定作 / 刻字定制、鲜活易腐、拆封音像、报刊）→ 不可退，`not_applicable`
7. 商品是**已拆封**的入口喂养用品（奶瓶 / 奶嘴）→ 不可退，`not_applicable`
8. 商品**明确**不完好（剪吊牌、穿出门、使用过、洗过、有污损）→ 不可退，`not_intact`
9. 无理由且自签收次日起超过 7 天 → 不可退，`out_of_window`；**质量问题不受 7 天限制**
10. 以上都不命中 → 可退，`ok`

## 政策原文优先
`<policy>` 里是平台政策原文。规则与政策**冲突时以政策原文为准**，
并在 `cited_article` 写明依据。政策没有覆盖的情形，**不要自己发明规则**。

## 你还会收到
- `<order>`：订单快照（状态、金额、件数、商品类别、各环节时间）
- `<request>`：用户这次退款申请的要素（原因、件数、是否完好、是否拆封）
- `<user_input>`：用户原话

`<request>` 里为 `null` / 缺失的字段 = 用户没说。**不要替用户假设**。

## 原话优先
`<request>` 的字段是从 `<user_input>` 抽出来的，抽取可能不准。
**两者矛盾时，以用户原话为准**。例如 `<request>` 说已拆封，
但原话是「只拆了快递盒、里面塑封没动」——后者才是事实，应按"未拆封"处理。

**最重要的一条**：`condition_ok` 为 `null` 时**不得判 `not_intact`**——
那等于替用户认定商品不完好。用户自己表达犹豫时（「不太确定算不算用过」
「我也不知道算不算拆封」），正确做法是给 **0.6 以下的 confidence**，让系统转人工。
只有用户**明确陈述**用过 / 剪了吊牌 / 穿出门，才能判 `not_intact`。

## confidence
- 0.85 以上：规则明确命中、信息齐全
- 0.6–0.85：要素齐但存在解释空间
- 0.6 以下：信息不足或规则冲突——**宁可给低分，不要硬判**

低分会被系统转人工，那是安全结果；硬判错的代价是退错钱或招投诉。

## 输出
只通过工具返回，不要用自然语言解释。
"""

JUDGE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "eligible": {"type": "boolean", "description": "能不能退"},
        "reason_code": {
            "type": "string",
            "enum": list(JUDGE_REASONS),
            "description": "判定理由码；可退时用 ok 或 reject_then_refund",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "对这个判定的确定度。低于 0.6 会被系统转人工",
        },
        "cited_article": {
            "type": "string",
            "description": "依据的政策条目或法规条号，可为空",
        },
    },
    "required": ["eligible", "reason_code", "confidence"],
}


def _policy_block(evidence: list[dict]) -> str:
    """把检索结果渲染成带编号的原文块。

    编号与 `evidence` 顺序一致——后续 `respond` 引用 `[1]` 时靠这个对应。
    """
    if not evidence:
        return "（没有检索到政策原文）"
    lines = []
    for index, hit in enumerate(evidence, start=1):
        source = hit.get("source") or ""
        section = hit.get("section") or ""
        text = hit.get("text") or ""
        lines.append(f"[{index}]（{source} / {section}）{text}")
    return "\n".join(lines)


def judge_messages(
    query: str,
    order: dict,
    request: dict,
    evidence: list[dict],
) -> list[dict[str, str]]:
    """判定用的消息列表。四块材料都放在标签里，便于注入防御与调试。"""
    user = (
        f"<order>{json.dumps(order, ensure_ascii=False)}</order>\n"
        f"<request>{json.dumps(request, ensure_ascii=False)}</request>\n"
        f"<policy>\n{_policy_block(evidence)}\n</policy>\n"
        f"<user_input>{query}</user_input>"
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


# ══════════════════════════════════════════════════════════════
# C 出口：把判定结果说成人话（模型只组织语言，不改结论）
# ══════════════════════════════════════════════════════════════

RESPOND_SYSTEM = """## 角色
你是电商售后客服。系统已经做完判定，你只负责把它说清楚。

## 硬性要求
1. **结论不能改**。不要重新判断"能不能退"，不要给出与系统结论相反的建议。
2. 可退时，**金额必须原样引用用户消息里的「本次应退金额」**：
   一个字都不能改，不要四舍五入，不要加"约"、不要换算成"千元"。
3. 不可退时**不要提任何金额**，说清原因即可。
4. 可退时告诉用户下一步：
   - 理由码 `ok`：在订单页申请退货，商品保持完好寄回
   - 理由码 `reject_then_refund`：请拒收包裹，退回仓库后全额退款
5. 语气平和，**不超过 120 字**，口语化。不要编造平台政策、时效或赔付承诺。
6. 不要输出 JSON、不要输出思考过程，直接说人话。
"""


def respond_messages(
    query: str,
    order: dict,
    request: dict,
    judgement: dict,
    amount_text: str,
) -> list[dict[str, str]]:
    """回复用的消息列表。

    `amount_text` 是**已经算好的字符串**（如「1249.00 元」），不是数字——
    给数字模型就会自己乘除，给字符串它只能照抄。
    """
    amount_line = (
        f"本次应退金额：{amount_text}\n"
        if amount_text
        else "本次不可退，不要提金额。\n"
    )
    user = (
        f"<order>{json.dumps(order, ensure_ascii=False)}</order>\n"
        f"<request>{json.dumps(request, ensure_ascii=False)}</request>\n"
        f"<judgement>{json.dumps(judgement, ensure_ascii=False)}</judgement>\n"
        f"{amount_line}"
        f"<user_input>{query}</user_input>"
    )
    return [
        {"role": "system", "content": RESPOND_SYSTEM},
        {"role": "user", "content": user},
    ]


# ══════════════════════════════════════════════════════════════
# 知识出口：基于检索证据作答（四区结构的前三区，Ch06 补工具结果区）
# ══════════════════════════════════════════════════════════════

KB_ANSWER_SYSTEM = """## 角色
你是电商售后客服。回答要准确、简洁、口语化。

## 铁律
1. **只依据 <retrieved> 里的资料回答。** 资料没写的，直接说没有找到依据，不要用常识补。
2. **不得给出资料里没有的数字**——金额、天数、比例、时效一律不许编。
3. 每个关键结论后面标注来源编号，如「满 99 元包邮[1]」。多个来源写 `[1][2]`。
4. <retrieved> 里标注「本文档不提供什么」的内容是**边界声明**，说明这个问题不在范围内——遇到就如实告知，不要绕过。

## 输出
直接用中文回答，不要加标题、不要解释你在做什么、不要输出 JSON。"""


def kb_answer_messages(query: str, evidence: list[dict]) -> list[dict[str, str]]:
    """拼装生成用的消息：系统指令 + 用户输入 + 检索数据。

    `evidence` 的顺序就是引用编号 `[1] [2] …` 的顺序——**编号与列表下标严格对应**，
    否则答案里的 `[3]` 会指向错的资料。
    """
    blocks = [
        f"[{index}] 来源：{item.get('source', '')} / {item.get('section', '')}\n{item.get('text', '')}"
        for index, item in enumerate(evidence, start=1)
    ]
    return [
        {"role": "system", "content": KB_ANSWER_SYSTEM},
        {
            "role": "user",
            "content": (
                f"<user_input>{query}</user_input>\n\n"
                f"<retrieved>\n" + "\n\n".join(blocks) + "\n</retrieved>"
            ),
        },
    ]
