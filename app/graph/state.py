"""图状态：一轮对话的全部字段。

`total=False` 让每个字段都可选——节点只返回自己改的那几个键，LangGraph 做增量合并，
不用把整个 state 传回来（若设成 True，每个节点都得返回全部字段）。
字段释义见 `docs/superpowers/specs/ch02-walking-skeleton-design.md`「状态」。
"""
from typing import TypedDict


class ConversationState(TypedDict, total=False):
    # ── 输入：调用方（API 层）填，节点只读 ──────────────────────────
    trace_id: str                 # 请求 id，串起日志与 trace_span(L7)
    conversation_id: int | None   # 会话 id，同时是图的 thread_id(D5)；None = 新会话
    user_id: str                  # 当前用户；订单归属校验靠它（Ch06 换成真实鉴权）
    raw_query: str                # 用户原话，永不改写；审计与回放看它
    history: list[dict]           # 上一轮及更早的窗口消息 [{id,role,content}]（Ch05）
    summary: str | None           # 滚动摘要；没有就是 None（Ch05）
    resume_route: str | None      # 槽位命中后要回的出口；None = 正常分类（Ch05 Task 6）
    resumed: bool                 # 本轮是否接回未完成流程（Ch05）
    needs_tools: bool             # E 出口 gate：值不值得进只读 ReAct（Ch05 Task 10）

    # ── 中间产物：节点依次写入 ────────────────────────────────────
    resolved_query: str           # 补全后的完整问句；本章 = raw_query（透传，Ch05 真做）
    intent: str                   # 12 类之一；分类失败/越界 = "其他"
    intent_confidence: float      # 0–1；只做标记（<0.5 打 low_conf），不改路由
    route: str                    # 5 出口之一：knowledge/data/rules/human/fallback
    evidence: list[dict]          # 命中 chunk 原文 [{id,text,source,similarity}]；本章存全量
    evidence_strong: bool         # 最终闸结论（机械 + 语义自评）
    evidence_judge: dict          # 语义自评结果 {decision, sufficient, confidence, reason}
    citation_ids: list[str]       # 最终引用了哪些 chunk，如 ["第六条","returns#退款时效"]

    # ── C 出口：退款流水线（Ch03）────────────────────────────────
    order_id: str                    # intake 从用户话里抽出的订单号
    order_candidates: list[dict]     # need_info 列给用户点选的候选订单
    refund_draft: dict               # extract 抽出的要素 {reason_code, item_count, condition_ok, packaging_opened}
    validate_errors: list[str]       # validate 发现的问题；空 = 通过
    validate_retry: int              # 已追问轮次；达到 2 次转人工
    order_snapshot: dict             # query_data 查到的订单快照
    refund_judgement: dict           # judge 输出 {eligible, reason_code, confidence, cited_article?}
    refund_amount: int               # compute 算出的应退金额（分）
    compliance_violation: str | None # 触红线 / 复算不一致的原因；None = 通过
    pending_question: str | None     # 正在等哪个槽：order_id / sku_id / spec / reason_code
    skip_extract: bool               # 恢复轮只补了订单号，沿用 draft，不再调抽取模型
    transfer_reason: str             # 转人工的原因（trace 与落池用）

    # ── B 出口：数据查询（Ch04）────────────────────────────────
    sku_id: str                      # extract_params 抽出的商品型号
    sku_candidates: list[dict]       # 缺型号时列给用户选
    spec_candidates: list[dict]      # 缺规格时列该型号的可选规格
    data_result: dict                # 工具返回的结构化数据，供 format 使用
    tool_code: str                   # 工具结果码：ok / not_found / invalid_arg / timeout / internal

    # ── 输出：给用户看 ──────────────────────────────────────────
    answer: str                   # 回复正文；占位节点也必须写，不能留空
    actions: list[dict]           # 前端动作，目前只有 {"type": "transfer_human"}
    is_placeholder: bool          # True = 这条是"开发中"占位，不是真能力
