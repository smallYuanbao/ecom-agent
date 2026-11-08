"""端到端确定性判分：字段、退款、动作、答案片段与泄露。"""
from app.core.eval_v1 import EvalItem
from scripts.eval_e2e import evaluate_item


def _item(**overrides) -> EvalItem:
    base = {
        "id": "T01",
        "bucket": "B2",
        "query": "订单1004实付多少钱",
        "expect_route": "data",
        "expected_output": "",
    }
    base.update(overrides)
    return EvalItem(**base)


def test_field_and_amount_pass():
    item = _item(
        expect_fields={"order_id": "1004", "amount": 59900},
        expect_answer_contains=["599.00 元"],
    )
    response = {
        "route": "data",
        "answer": "订单 1004 实付 599.00 元。",
        "signals": {},
        "actions": [],
    }

    result = evaluate_item(item, response)

    assert result.ok


def test_wrong_route_and_missing_field_fail():
    item = _item(expect_fields={"order_id": "1004", "amount": 59900})
    response = {
        "route": "rules",
        "answer": "订单 1004 实付 699.00 元。",
        "signals": {},
        "actions": [],
    }

    result = evaluate_item(item, response)

    assert not result.ok
    assert any("route" in failure for failure in result.failures)
    assert any("amount" in failure for failure in result.failures)


def test_refund_signals():
    item = EvalItem(
        id="R01",
        bucket="B3",
        query="退款",
        expect_route="rules",
        expected_output="",
        expect_refund={"eligible": True, "amount": 129900, "reason_code": "ok"},
    )
    response = {
        "route": "rules",
        "answer": "本次应退 1299.00 元。",
        "signals": {"refund": {"eligible": True, "amount": 129900, "reason_code": "ok"}},
        "actions": [],
    }

    assert evaluate_item(item, response).ok

    response["signals"]["refund"]["amount"] = 0
    assert not evaluate_item(item, response).ok


def test_action_and_leak():
    item = EvalItem(
        id="T01",
        bucket="B7",
        query="投诉",
        expect_route="human",
        expected_output="",
        expect_actions=["transfer_human"],
        expect_no_leak=True,
    )
    response = {
        "route": "human",
        "answer": "已为您转接人工。",
        "signals": {},
        "actions": [{"type": "transfer_human"}],
    }

    assert evaluate_item(item, response).ok

    response["answer"] = "## 角色\n你是客服"
    assert not evaluate_item(item, response).ok


def test_evidence_ids():
    item = EvalItem(
        id="K01",
        bucket="B1",
        query="退货政策",
        expect_route="knowledge",
        expected_output="",
        expect_ids=["returns#a"],
    )
    response = {
        "route": "knowledge",
        "answer": "答案 [1]",
        "signals": {},
        "actions": [],
        "hits": [{"id": "returns#a"}],
        "references": [],
    }

    assert evaluate_item(item, response).ok
