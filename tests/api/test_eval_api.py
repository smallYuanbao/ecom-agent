"""`/api/eval/chat`：证据映射、开关与错误码。"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import eval as eval_module
from app.api.chat import ChatError
from app.api.eval import EvalRequest, eval_chat


def _settings(enabled: bool = True):
    return SimpleNamespace(enable_eval_api=enabled)


async def test_disabled_returns_404(monkeypatch):
    monkeypatch.setattr(eval_module, "get_settings", lambda: _settings(False))

    with pytest.raises(HTTPException) as exc:
        await eval_chat(EvalRequest(message="你好"))

    assert exc.value.status_code == 404


async def test_maps_answer_evidence_and_signals(monkeypatch):
    async def fake_run(message, cid, trace, *, capture=None, **kwargs):
        capture.update(
            final={
                "route": "knowledge",
                "intent": "政策问答",
                "citation_ids": ["a"],
                "evidence": [
                    {"id": "a", "text": "证据 A", "source": "s", "rrf_score": 0.9},
                    {"id": "b", "text": "证据 B", "source": "s", "rrf_score": 0.8},
                ],
                "refund_judgement": {"eligible": True, "reason_code": "ok"},
                "refund_amount": 129900,
                "tool_code": "ok",
                "order_snapshot": {"secret": "不该外露"},
            },
            answer="答案 [1]",
            actions=[{"type": "transfer_human"}],
            violations=["amount"],
        )
        yield {"event": "ack", "conversation_id": 7}
        yield {"delta": "答案 [1]"}

    monkeypatch.setattr(eval_module, "run_turn", fake_run)
    monkeypatch.setattr(eval_module, "get_settings", lambda: _settings())

    data = await eval_chat(EvalRequest(message="退货政策是什么"))

    assert data["conversation_id"] == 7
    assert data["route"] == "knowledge"
    assert data["answer"] == "答案 [1]"
    assert [item["id"] for item in data["references"]] == ["a"]
    assert [item["rank"] for item in data["hits"]] == [1, 2]
    assert data["hits"][1]["cited"] is False
    assert data["actions"] == [{"type": "transfer_human"}]
    assert data["violations"] == ["amount"]
    assert data["signals"]["refund"]["amount"] == 129900
    assert "secret" not in str(data["signals"])


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("invalid_arg", 422),
        ("timeout", 504),
        ("not_found", 404),
        ("busy", 429),
        ("internal", 500),
    ],
)
async def test_chat_error_mapping(monkeypatch, code, status):
    async def boom(*args, **kwargs):
        if False:
            yield
        raise ChatError(code, "x")

    monkeypatch.setattr(eval_module, "run_turn", boom)
    monkeypatch.setattr(eval_module, "get_settings", lambda: _settings())

    with pytest.raises(HTTPException) as exc:
        await eval_chat(EvalRequest(message="你好"))

    assert exc.value.status_code == status


async def test_internal_error_500(monkeypatch):
    async def boom(*args, **kwargs):
        if False:
            yield
        raise RuntimeError("boom")

    monkeypatch.setattr(eval_module, "run_turn", boom)
    monkeypatch.setattr(eval_module, "get_settings", lambda: _settings())

    with pytest.raises(HTTPException) as exc:
        await eval_chat(EvalRequest(message="你好"))

    assert exc.value.status_code == 500
