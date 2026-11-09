"""S5：日志脱敏、配置摘要与 `print` 静态守卫。"""
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.core.logging import JsonFormatter, redact

APP_DIR = Path(__file__).resolve().parents[2] / "app"


# ── redact ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "调用失败 key=sk-abcdefghijklmnop1234",
        "api_key: sk-abcdefghijklmnop1234",
        "password=123456",
        "token=abc.def.ghi",
        "Authorization: Bearer abc.def.ghi",
    ],
)
def test_redact_hides_secrets(raw):
    cleaned = redact(raw)

    assert "sk-abcdefghijklmnop1234" not in cleaned
    assert "123456" not in cleaned
    assert "abc.def.ghi" not in cleaned
    assert "***" in cleaned


def test_redact_keeps_normal_text():
    text = "对话失败 conv=12 trace=abc"
    assert redact(text) == text


# ── JsonFormatter ───────────────────────────────────────────


def _record(msg: str, args: tuple = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_formatter_redacts_message():
    record = _record("模型调用失败 key=%s", ("sk-abcdefghijklmnop1234",))

    payload = json.loads(JsonFormatter().format(record))

    assert "sk-abcdefghijklmnop1234" not in payload["msg"]
    assert "***" in payload["msg"]


def test_formatter_keeps_normal_message():
    record = _record("对话完成 conv=%s", (12,))

    payload = json.loads(JsonFormatter().format(record))

    assert payload["msg"] == "对话完成 conv=12"


# ── 配置摘要 / /api/meta ───────────────────────────────────


def test_safe_summary_reports_key_status_only():
    summary = Settings(chat_api_key="sk-abcdefghijklmnop1234").safe_summary()

    assert summary["chat_api_key"] == "<set>"
    assert summary["retrieval_strategy"] in ("dense", "bm25", "hybrid")
    assert "sk-abcdefghijklmnop1234" not in json.dumps(summary)


def test_meta_does_not_expose_key(monkeypatch):
    fake = Settings(chat_api_key="sk-abcdefghijklmnop1234")
    monkeypatch.setattr(main_module, "get_settings", lambda: fake)

    # 不用 with：跳过 lifespan，避免启动校验去找真实密钥
    body = TestClient(main_module.app).get("/api/meta").json()

    assert body["chat_api_key"] == "<set>"
    assert "sk-abcdefghijklmnop1234" not in json.dumps(body)


# ── 静态守卫 ────────────────────────────────────────────────


def test_app_has_no_print():
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if "print(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
