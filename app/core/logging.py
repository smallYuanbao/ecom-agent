"""结构化日志 + trace_id。

设计依据：L7（结构化日志 + trace_id 贯穿）。
"""
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

# 密钥样式的兜底规则。真正的防线是「不把密钥传给 logger」；
# 这里保证即使误传，落盘的文本里也看不到原文。
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|password|token)\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+"),
)


def redact(text: str) -> str:
    """把日志文本里的密钥、密码、token、Bearer 头替换成 `***`。"""
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("***", text)
    return text


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "trace_id": trace_id_var.get(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key in ("node", "tool", "conv", "event", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
