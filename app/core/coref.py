"""指代消解（M3）：把「那单」「这个」补成不依赖上文的完整问句。

**只补指代与省略，不增删事实**。改写失败一律回落原句——
它是让意图/检索更准的增强，不是流程的前提。

三个零成本短路（不调模型）：

1. 历史为空：没有可消解的指代；
2. 原句已含订单号 / SKU：实体明确，改写只会引入风险；
3. 调用方标记 resume：「1001」这种补槽位不需要改写。
"""
import asyncio
import logging
import re

from app.core.llm import LLMFormatError, structured_call
from app.core.prompts import COREF_PARAMETERS, COREF_TOOL_NAME, coref_messages
from app.graph.params import ORDER_ID_RE, SKU_RE

logger = logging.getLogger(__name__)

# 历史只取最近几条消息：指代通常只回指上一两句，塞太多反而干扰
HISTORY_MESSAGES = 6

# 改写结果超过这个长度视为「扩写」而不是「消解」。
# 短句（「那单呢」）补全后本来就会长很多，所以给一个绝对增量下限。
MAX_EXPANSION = 2.0
MIN_EXTRA_CHARS = 24

# 指代消解是短输出；超过 5s 说明上游有问题，直接回落原句
COREF_TIMEOUT = 5.0


def _has_explicit_entity(text: str) -> bool:
    return bool(ORDER_ID_RE.search(text) or SKU_RE.search(text))


def _same_text(left: str, right: str) -> bool:
    """忽略空白差异后是否相同。"""
    return re.sub(r"\s+", "", left) == re.sub(r"\s+", "", right)


async def resolve(
    query: str, history: list[dict] | None, *, summary: str | None = None
) -> tuple[str, str]:
    """返回 `(resolved_query, mode)`。

    `mode`：

    - `rewrite`：真的改写了；
    - `passthrough`：不需要改 / 改写结果不可信；
    - `passthrough_error`：调用失败，回落原句。
    """
    text = (query or "").strip()
    if not text or not history:
        return query, "passthrough"
    if _has_explicit_entity(text):
        return query, "passthrough"

    try:
        data = await asyncio.wait_for(
            structured_call(
                COREF_TOOL_NAME,
                COREF_PARAMETERS,
                coref_messages(text, history[-HISTORY_MESSAGES:], summary),
            ),
            timeout=COREF_TIMEOUT,
        )
    except LLMFormatError:
        return query, "passthrough_error"
    except Exception:
        logger.warning("指代消解失败，回落原句", exc_info=True)
        return query, "passthrough_error"

    resolved = str(data.get("resolved_query") or "").strip()
    if not resolved or len(resolved) < 2:
        return query, "passthrough"
    max_len = max(int(len(text) * MAX_EXPANSION), len(text) + MIN_EXTRA_CHARS)
    if len(resolved) > max_len:
        return query, "passthrough"
    if _same_text(resolved, text):
        return query, "passthrough"
    return resolved, "rewrite"
