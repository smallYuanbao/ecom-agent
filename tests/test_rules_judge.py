"""Task 9：`judge` —— 模型判资格，代码只做形状校验与门槛。

**这一节不测"模型判得准不准"**——那是 gold set（Task 12）的事。
这里测的是：

1. 模型给的结论**不合契约**时，系统不把它放行（整条作废 → 转人工）
2. 自相矛盾的输出（`eligible=False` + `ok`）能被识别
3. 低置信度确实转人工
4. 喂给模型的材料里，订单 / 申请要素 / 政策原文 / 用户原话四样齐全

对应关系：**格式问题在这里拦住，判断质量问题在 Task 12 量。**
"""
import pytest

from app.core.llm import LLMFormatError
from app.core.prompts import JUDGE_PARAMETERS, JUDGE_REASONS, JUDGE_SYSTEM, judge_messages
from app.graph import rules_exit
from app.graph.rules_exit import _clean_judgement, after_judge, judge

ORDER = {
    "order_id": "1001",
    "status": "已签收",
    "amount": 269900,
    "quantity": 1,
    "category": "婴儿推车",
    "signed_at": "2026-10-01T10:00:00",
}
REQUEST = {"reason_code": "no_reason", "condition_ok": True}
EVIDENCE = [
    {
        "id": "returns#退款时效",
        "source": "returns.md",
        "section": "退款时效",
        "text": "自签收次日起 7 日内可申请无理由退货。",
        "similarity": 0.81,
    }
]


def mock_model(monkeypatch, data: dict | None = None, error: Exception | None = None):
    calls: list[list[dict]] = []

    async def _call(tool_name, parameters, messages, **kwargs):
        calls.append(messages)
        if error is not None:
            raise error
        return data

    monkeypatch.setattr(rules_exit, "structured_call", _call)
    return calls


def state(**overrides) -> dict:
    base = {
        "raw_query": "订单1001 不想要了",
        "order_snapshot": ORDER,
        "refund_draft": REQUEST,
        "evidence": EVIDENCE,
    }
    base.update(overrides)
    return base


GOOD = {"eligible": False, "reason_code": "out_of_window", "confidence": 0.92}


# ── 形状校验 ────────────────────────────────────────────────


def test_accepts_valid_judgement():
    assert _clean_judgement(GOOD) == GOOD


def test_keeps_cited_article_when_present():
    result = _clean_judgement({**GOOD, "cited_article": "returns.md 第十条"})
    assert result["cited_article"] == "returns.md 第十条"


def test_drops_blank_cited_article():
    result = _clean_judgement({**GOOD, "cited_article": "   "})
    assert "cited_article" not in result


@pytest.mark.parametrize(
    "data",
    [
        {"eligible": "false", "reason_code": "ok", "confidence": 0.9},       # 字符串布尔
        {"eligible": False, "reason_code": "refund", "confidence": 0.9},     # 越界枚举
        {"eligible": False, "reason_code": "ok", "confidence": 0.9},         # 自相矛盾
        {"eligible": True, "reason_code": "not_intact", "confidence": 0.9},  # 自相矛盾
        {"eligible": False, "reason_code": "out_of_window", "confidence": "high"},
        {"eligible": False, "reason_code": "out_of_window", "confidence": True},
        {"eligible": False, "reason_code": "out_of_window", "confidence": 1.5},
        {"eligible": False, "reason_code": "out_of_window"},                 # 缺置信度
    ],
)
def test_rejects_bad_judgement(data):
    assert _clean_judgement(data) is None


# ── judge 节点 ──────────────────────────────────────────────


async def test_high_confidence_passes_to_compute(monkeypatch):
    mock_model(monkeypatch, GOOD)
    result = await judge(state())

    assert result["refund_judgement"] == GOOD
    assert "transfer_reason" not in result


async def test_low_confidence_transfers(monkeypatch):
    mock_model(monkeypatch, {**GOOD, "confidence": 0.5})
    result = await judge(state())

    assert result["refund_judgement"]["confidence"] == 0.5
    assert result["transfer_reason"] == "judge_low_confidence"
    assert result["actions"] == [{"type": "transfer_human"}]


async def test_confidence_boundary_is_inclusive(monkeypatch):
    mock_model(monkeypatch, {**GOOD, "confidence": 0.7})
    assert "transfer_reason" not in await judge(state())


async def test_format_error_transfers(monkeypatch):
    mock_model(monkeypatch, error=LLMFormatError("没调用工具"))
    result = await judge(state())

    assert "refund_judgement" not in result
    assert result["transfer_reason"] == "judge_format_error"


async def test_invalid_output_transfers(monkeypatch):
    mock_model(monkeypatch, {"eligible": False, "reason_code": "ok", "confidence": 0.9})
    result = await judge(state())

    assert "refund_judgement" not in result
    assert result["transfer_reason"] == "judge_format_error"


# ── 喂给模型的材料 ──────────────────────────────────────────


async def test_messages_include_all_four_inputs(monkeypatch):
    calls = mock_model(monkeypatch, GOOD)
    await judge(state())

    user = calls[0][-1]["content"]
    assert "<order>" in user and "婴儿推车" in user
    assert "<request>" in user and "no_reason" in user
    assert "<policy>" in user and "自签收次日起 7 日内" in user
    assert "<user_input>" in user and "不想要了" in user


def test_policy_is_numbered_in_order():
    messages = judge_messages(
        "查政策",
        ORDER,
        REQUEST,
        [EVIDENCE[0], {**EVIDENCE[0], "id": "returns#完好", "text": "商品需完好"}],
    )
    content = messages[-1]["content"]
    assert "[1]（returns.md / 退款时效）" in content
    assert "[2]（returns.md / 退款时效）商品需完好" in content


def test_empty_policy_has_explicit_placeholder():
    """政策为空时不能让 prompt 里出现一个空块——模型会以为"没有政策就是没有限制"。"""
    content = judge_messages("查政策", ORDER, REQUEST, [])[-1]["content"]
    assert "（没有检索到政策原文）" in content


# ── prompt / schema 一致性 ─────────────────────────────────


def test_schema_enum_matches_constant():
    props = JUDGE_PARAMETERS["properties"]
    assert props["reason_code"]["enum"] == list(JUDGE_REASONS)
    assert set(JUDGE_PARAMETERS["required"]) == {"eligible", "reason_code", "confidence"}


def test_prompt_covers_every_reason_code():
    for reason in JUDGE_REASONS:
        assert reason in JUDGE_SYSTEM, f"prompt 没解释 {reason}"


def test_prompt_lets_user_words_override_draft_fields():
    """字段是抽出来的，抽取会错；原话才是事实。

    H10 实测：`packaging_opened=true`（只拆了外箱），原话却说「塑封没动」。
    模型必须以后者为准，否则规则 7 会误杀。
    """
    assert "两者矛盾时，以用户原话为准" in JUDGE_SYSTEM


def test_prompt_forbids_not_intact_when_condition_unknown():
    """真模型实测踩过的坑：「用了一下，不太确定」被判成 not_intact。

    `condition_ok` 为 null 时判不完好，等于替用户下结论——必须写死禁止。
    """
    assert "`condition_ok` 为 `null` 时**不得判 `not_intact`**" in JUDGE_SYSTEM
    assert "低" in JUDGE_SYSTEM and "confidence" in JUDGE_SYSTEM


# ── 条件边 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"refund_judgement": {"confidence": 0.9}}, "compute"),
        ({"refund_judgement": {"confidence": 0.7}}, "compute"),
        ({"refund_judgement": {"confidence": 0.69}}, "human"),
        ({}, "human"),
    ],
)
def test_after_judge_routing(state, expected):
    assert after_judge(state) == expected
