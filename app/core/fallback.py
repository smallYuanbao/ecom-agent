"""兜底话术与低置信落池。

两件事：

1. **话术**：明确说"没找到依据、不能给结论"，并给出下一步。绝不编内容。
2. **落池**：把判不了的问题写进 `low_conf_questions`，给飞轮用（K5）。

`retrieved` 字段是三态，区别很重要：

| 值 | 含义 | 说明什么 |
|---|---|---|
| `"[]"` | 零命中 | **知识库真缺这块内容**，优先补语料 |
| `"[{...}]"` | 有召回但分数低 | **召回质量差**，该调阈值或改检索 |
| `NULL` | 没走检索 | 不是知识缺口（如规则类问题），别混进语料统计 |
"""
import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LowConfQuestion
from app.db.session import session_scope

# 意图文档 §七的模板。`topic` 从用户原话截取，不自行概括——
# 概括错了会让用户以为系统理解偏了。
FALLBACK_TEMPLATE = "关于「{topic}」，我这边没有找到确切的依据，不能给您结论。已经为您记录，{next_step}"

NEXT_STEP_TRANSFER = "您可以点击「转人工」联系客服。"
NEXT_STEP_SUGGEST = "您是不是想问「{title}」？"


def fallback_text(topic: str, top1_title: str | None = None) -> tuple[str, str]:
    """生成兜底话术，返回 `(正文, 下一步提示)`。

    **有候选标题时更友好**：反问"您是不是想问 X"，比直接甩给人转人工好。
    但只在 top1 有标题时才这么做——没标题说明连候选都没有，反问会很突兀。
    """
    if top1_title:
        next_step = NEXT_STEP_SUGGEST.format(title=top1_title)
    else:
        next_step = NEXT_STEP_TRANSFER
    return FALLBACK_TEMPLATE.format(topic=topic, next_step=next_step), next_step


def _retrieved_payload(hits: list[dict] | None) -> str | None:
    """把召回结果压成三态 JSON。

    只存 id 与分数：原文已经在向量库里，池子里存一份是重复；
    但 id 与分数是"当时为什么判它弱"的证据，必须留。
    """
    if hits is None:
        return None                      # 没走检索
    if not hits:
        return "[]"                      # 零命中
    return json.dumps(
        [{"id": h["id"], "similarity": round(h["similarity"], 4)} for h in hits],
        ensure_ascii=False,
    )


async def record_low_conf(
    *,
    question: str,
    reason: str,
    hits: list[dict] | None,
    conversation_id: int | None = None,
    source: str = "retrieval_low_conf",
    session: AsyncSession | None = None,
) -> int:
    """写一条低置信问题，返回行 id。

    `hits=None` 表示这条问题根本没走检索（如规则类），池子里要能区分出来——
    否则统计"知识缺口"时会被这类问题污染。
    """
    async with session_scope(session) as s:
        row = LowConfQuestion(
            conversation_id=conversation_id,
            question=question,
            source=source,
            reason=reason,
            retrieved=_retrieved_payload(hits),
        )
        s.add(row)
        await s.flush()
        return row.id
