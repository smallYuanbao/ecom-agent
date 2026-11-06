"""`llm.tool_chat`：解析 tool_calls、空工具时强制回答、坏 JSON 降级。"""
from types import SimpleNamespace

from app.core import llm


class _Completions:
    def __init__(self, message):
        self.message = message
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self.message)]
        )


def _client(message) -> tuple[SimpleNamespace, _Completions]:
    completions = _Completions(message)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


async def test_parses_tool_calls(monkeypatch):
    message = SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(
                    name="query_order", arguments='{"order_id": "1001"}'
                ),
            )
        ],
    )
    client, _ = _client(message)
    monkeypatch.setattr(llm, "get_client", lambda: client)

    reply = await llm.tool_chat(
        [{"role": "user", "content": "查订单"}],
        [{"type": "function", "function": {"name": "query_order"}}],
    )

    assert reply.content == ""
    assert reply.tool_calls[0].id == "call-1"
    assert reply.tool_calls[0].name == "query_order"
    assert reply.tool_calls[0].args == {"order_id": "1001"}


async def test_empty_tools_omits_tool_fields(monkeypatch):
    message = SimpleNamespace(content="直接回答", tool_calls=None)
    client, completions = _client(message)
    monkeypatch.setattr(llm, "get_client", lambda: client)

    reply = await llm.tool_chat([{"role": "user", "content": "你好"}], [])

    assert reply.content == "直接回答"
    assert reply.tool_calls == []
    assert "tools" not in completions.kwargs
    assert "tool_choice" not in completions.kwargs


async def test_bad_arguments_degrade_to_empty(monkeypatch):
    message = SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name="query_order", arguments="{bad json"),
            )
        ],
    )
    client, _ = _client(message)
    monkeypatch.setattr(llm, "get_client", lambda: client)

    reply = await llm.tool_chat(
        [{"role": "user", "content": "查订单"}],
        [{"type": "function", "function": {"name": "query_order"}}],
    )

    assert reply.tool_calls[0].args == {}
