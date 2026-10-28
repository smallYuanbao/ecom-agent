"""结构化日志 + trace_id。

设计依据：L7（结构化日志 + trace_id 贯穿）。
"""
import json
import logging
import time
import uuid
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "trace_id": trace_id_var.get(),
            "logger": record.name,
            "msg": record.getMessage(),
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
