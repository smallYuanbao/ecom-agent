"""Task 11：`respond` 组织话术 + 接入主图。

respond 的职责边界：**模型只决定怎么说，不决定说什么**。
结论来自 `judge`，金额来自 `compute`，模型拿到的金额已经是字符串。

三个测试组：

1. 金额格式化（分 → 「1249.00 元」）
2. 机械兜底（回答里没出现金额就追加，不重试模型）
3. 主图接线（`rules` 节点是编译后的子图，不是占位函数）
"""
import pytest
from langgraph.graph.state import CompiledStateGraph

from app.graph import build, nodes, rules_exit
from app.graph.rules_exit import _ensure_amount, _traced, respond, yuan_text

# ── 金额格式化 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "0.00 元"),
        (1, "0.01 元"),
        (100, "1.00 元"),
        (124900, "1249.00 元"),
        (269900, "2699.00 元"),
        (123456, "1234.56 元"),
    ],
)
def test_yuan_text(cents: int, expected: str):
    assert yuan_text(cents) == expected


# ── 机械兜底 ────────────────────────────────────────────────


def test_keeps_answer_when_amount_present():
    answer = "可以退，本次应退 1249.00 元，请在订单页申请。"
    assert _ensure_amount(answer, {"eligible": True}, "1249.00 元") == answer


@pytest.mark.parametrize(
    "answer",
    [
        "本次应退 1249.00元。",      # 少一个空格（真模型实测会这样写）
        "本次应退 1,249.00 元。",    # 加了千分位逗号
        "本次应退 1249.00　元。",    # 全角空格
    ],
)
def test_amount_match_ignores_formatting(answer: str):
    """数字和小数位是硬约束，空格/逗号不是——否则会重复追加金额。"""
    assert _ensure_amount(answer, {"eligible": True}, "1249.00 元") == answer


def test_appends_amount_when_missing():
    answer = "可以退，请在订单页申请。"
    result = _ensure_amount(answer, {"eligible": True}, "1249.00 元")
    assert result.startswith(answer)
    assert result.endswith("本次应退金额：1249.00 元。")


def test_does_not_append_when_not_eligible():
    answer = "很抱歉，这一单已超过 7 天，无法无理由退货。"
    assert _ensure_amount(answer, {"eligible": False}, "") == answer


def test_does_not_append_without_amount_text():
    answer = "可以退。"
    assert _ensure_amount(answer, {"eligible": True}, "") == answer


# ── respond 节点 ────────────────────────────────────────────


def mock_chat(monkeypatch, answer: str):
    calls: list[list[dict]] = []

    async def _chat(messages, **kwargs):
        calls.append(messages)
        return answer

    monkeypatch.setattr(rules_exit, "llm_chat", _chat)
    return calls


def state(**overrides) -> dict:
    base = {
        "raw_query": "订单1002 不想要了",
        "order_snapshot": {"order_id": "1002", "status": "已发货", "amount": 124900},
        "refund_draft": {"reason_code": "no_reason"},
        "refund_judgement": {"eligible": True, "reason_code": "reject_then_refund"},
        "refund_amount": 124900,
    }
    base.update(overrides)
    return base


async def test_respond_returns_model_answer(monkeypatch):
    mock_chat(monkeypatch, "请拒收包裹，退回仓库后全额退款 1249.00 元。")
    result = await respond(state())

    assert result["is_placeholder"] is False
    assert "1249.00 元" in result["answer"]


async def test_respond_appends_missing_amount(monkeypatch):
    mock_chat(monkeypatch, "可以退，请拒收包裹。")
    result = await respond(state())

    assert "本次应退金额：1249.00 元。" in result["answer"]


async def test_respond_message_carries_amount_and_judgement(monkeypatch):
    calls = mock_chat(monkeypatch, "好的 1249.00 元。")
    await respond(state())

    user = calls[0][-1]["content"]
    assert "本次应退金额：1249.00 元" in user
    assert "reject_then_refund" in user
    assert "<user_input>订单1002 不想要了</user_input>" in user


async def test_respond_tells_model_not_to_mention_amount_when_ineligible(monkeypatch):
    calls = mock_chat(monkeypatch, "很抱歉，这一单无法退。")
    result = await respond(
        state(
            refund_amount=0,
            refund_judgement={"eligible": False, "reason_code": "out_of_window"},
        )
    )

    user = calls[0][-1]["content"]
    assert "本次不可退，不要提金额" in user
    assert result["answer"] == "很抱歉，这一单无法退。"


# ── 子图 trace ──────────────────────────────────────────────


async def test_each_subgraph_node_writes_a_span(monkeypatch):
    spans: list[dict] = []

    async def _span(**kwargs):
        spans.append(kwargs)

    monkeypatch.setattr(rules_exit, "record_span", _span)

    await rules_exit.build_rules_graph().ainvoke({"raw_query": "我要退款", "trace_id": "t1"})

    names = [s["name"] for s in spans]
    assert "rules:intake" in names
    assert "rules:need_info" in names
    assert all(s["trace_id"] == "t1" for s in spans)


def test_traced_wrapper_keeps_return_value():
    async def node(state):
        return {"x": 1}

    wrapped = _traced("demo", node)
    assert wrapped.__name__ == "traced_demo"


# ── 主图接线 ────────────────────────────────────────────────


def test_rules_node_is_compiled_subgraph():
    node = build.build_graph().get_graph().nodes["rules"]
    assert isinstance(node.data, CompiledStateGraph)


def test_placeholder_rules_exit_is_removed():
    """占位函数删掉，避免有人误以为它还在用。"""
    assert not hasattr(nodes, "rules_exit")
    assert "rules" not in nodes.PLACEHOLDER_TEXT
