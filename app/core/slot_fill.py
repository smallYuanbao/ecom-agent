"""槽位填充（M6）：把用户对追问的回答解析成流程要的槽位。

**只解析，不路由**。命中后由 API 设 `resume_route`，`classify_intent` 再短路回原出口。

判定顺序：取消 → 恢复 → 结构化点选 → 抽槽位 → 都没命中。
"""
import logging
from dataclasses import dataclass
from typing import Any

from app.graph.params import extract_order_id, extract_sku
from app.graph.rules_exit import extract_draft

logger = logging.getLogger(__name__)

CANCEL_WORDS = frozenset({"取消", "算了", "不退了", "不用了", "先这样"})
RESUME_WORDS = frozenset(
    {"继续", "接着", "回到退款", "继续退款", "继续退费", "好的继续"}
)

# 结构化点选：前端卡片 type → 它回答的槽位
SELECTED_SLOTS = {
    "select_order": "order_id",
    "select_sku": "sku_id",
    "select_spec": "spec",
}

# 这些槽位用 C 出口的抽取器（而不是正则）
REFUND_SLOTS = frozenset({"reason_code", "refund_extract"})

# 全角数字归一：用户可能输入「１００１」
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


@dataclass(frozen=True)
class SlotResult:
    filled: bool
    slot: str | None
    value: Any = None
    cancel: bool = False
    resume: bool = False


def _clean(text: str) -> str:
    return text.strip(" \t\r\n，,。.!！?？、；;：:")


def _is_cancel(cleaned: str) -> bool:
    """入参必须已经 `_clean` 过。"""
    return cleaned in CANCEL_WORDS or any(cleaned.startswith(w) for w in CANCEL_WORDS)


def _is_resume(cleaned: str) -> bool:
    """入参必须已经 `_clean` 过。"""
    return cleaned in RESUME_WORDS


def _selected_value(selected: dict, pending_question: str) -> Any:
    """结构化点选是否正好回答当前槽位；类型不匹配返回 None。"""
    if SELECTED_SLOTS.get(str(selected.get("type"))) != pending_question:
        return None
    # 兼容前端传 order_id / value 两种写法
    for key in (pending_question, "value", "id"):
        value = selected.get(key)
        if value:
            return value
    return None


def _merge_refund(draft: dict, extracted: dict) -> dict:
    """合并退款要素：新值覆盖旧值，但只覆盖抽取器认可的字段。"""
    old = draft.get("refund") if isinstance(draft.get("refund"), dict) else {}
    return {**old, **extracted}


async def fill(
    pending_question: str | None,
    text: str,
    *,
    draft: dict | None = None,
    selected: dict | None = None,
) -> SlotResult:
    """解析用户回答。

    `draft` 是外层流程草稿；退款字段在 `draft["refund"]` 下。
    """
    draft = draft or {}
    # 没有待补槽位 = 没有可填的东西；取消/恢复也不该在此时产生信号
    if not pending_question:
        return SlotResult(False, None)

    raw = text or ""
    cleaned = _clean(raw)

    # 1. 取消：清流程由调用方做，这里只给出信号
    if cleaned and _is_cancel(cleaned):
        return SlotResult(False, None, cancel=True)

    # 2. 恢复：用户说「继续退费」，不需要新值
    if cleaned and _is_resume(cleaned):
        return SlotResult(False, None, resume=True)

    # 3. 结构化点选：类型必须与当前槽位匹配
    if selected:
        value = _selected_value(selected, pending_question)
        if value is not None:
            # 前端可能传数字或小写型号，统一成下游用的形状
            if pending_question == "order_id":
                value = str(value)
            elif pending_question == "sku_id":
                value = str(value).upper()
            return SlotResult(True, pending_question, value)

    if not cleaned:
        return SlotResult(False, None)

    # 4. 按槽位抽取
    if pending_question == "order_id":
        normalized = raw.translate(_FULLWIDTH_DIGITS)
        order_id = extract_order_id(normalized)
        if order_id:
            return SlotResult(True, "order_id", order_id)

    elif pending_question == "sku_id":
        sku = extract_sku(raw)
        if sku:
            return SlotResult(True, "sku_id", sku)

    elif pending_question == "spec":
        # 规格是开放集合：透传，匹配交给 data 出口
        return SlotResult(True, "spec", cleaned)

    elif pending_question in REFUND_SLOTS:
        try:
            extracted = await extract_draft(raw)
        except Exception:
            logger.warning("退款槽位抽取失败", exc_info=True)
            return SlotResult(False, None)
        if extracted:
            return SlotResult(
                True,
                pending_question,
                _merge_refund(draft, extracted),
            )

    return SlotResult(False, None)
