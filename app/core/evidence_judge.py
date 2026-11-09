"""证据充分性自评（Ch07 语义闸）。

机械闸只看 top1 余弦相似度，分不开：

- 「能分期付款吗」→ `member#积分规则`（0.572，话题近但没有答案）
- 「FB-30 保修多久」→ 产品块（0.566，确实有答案）

语义闸只在边界带调用一次模型，回答「这些证据够不够回答」。
**超时 3s、不重试、失败即 weak**：它是用户等待路径上的一步，不能吃掉 C5 总预算。
"""
import logging

from app.config import get_settings
from app.core import llm
from app.core.prompts import (
    EVIDENCE_JUDGE_PARAMETERS,
    EVIDENCE_JUDGE_TOOL_NAME,
    evidence_judge_messages,
)

logger = logging.getLogger(__name__)


async def judge_evidence(query: str, hits: list[dict]) -> dict:
    """返回 `{sufficient, confidence, reason}`；格式非法按失败处理。"""
    data = await llm.structured_call(
        EVIDENCE_JUDGE_TOOL_NAME,
        EVIDENCE_JUDGE_PARAMETERS,
        evidence_judge_messages(query, hits),
    temperature=0.0,
        timeout=get_settings().evidence_judge_timeout,
        max_attempts=1,
    )

    sufficient = data.get("sufficient")
    if not isinstance(sufficient, bool):
        raise llm.LLMFormatError("证据自评缺少 sufficient")

    confidence = data.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "sufficient": sufficient,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(data.get("reason") or ""),
    }
