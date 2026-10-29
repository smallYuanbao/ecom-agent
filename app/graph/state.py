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
    raw_query: str                # 用户原话，永不改写；审计与回放看它

    # ── 中间产物：节点依次写入 ────────────────────────────────────
    resolved_query: str           # 补全后的完整问句；本章 = raw_query（透传，Ch05 真做）
    intent: str                   # 12 类之一；分类失败/越界 = "其他"
    intent_confidence: float      # 0–1；只做标记（<0.5 打 low_conf），不改路由
    route: str                    # 5 出口之一：knowledge/data/rules/human/fallback
    evidence: list[dict]          # 命中 chunk 原文 [{id,text,source,similarity}]；本章存全量
    citation_ids: list[str]       # 最终引用了哪些 chunk，如 ["第六条","returns#退款时效"]

    # ── 输出：给用户看 ──────────────────────────────────────────
    answer: str                   # 回复正文；占位节点也必须写，不能留空
    actions: list[dict]           # 前端动作，目前只有 {"type": "transfer_human"}
    is_placeholder: bool          # True = 这条是"开发中"占位，不是真能力
