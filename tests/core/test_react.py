"""只读 ReAct harness：收敛、步数封顶、错误回灌、白名单、参数归一、结果截断。"""
from app.core import react
from app.core.llm import ToolCall, ToolChatReply
from app.tools.orders import ToolResult


def _reply(content: str = "", calls: list[ToolCall] | None = None) -> ToolChatReply:
    return ToolChatReply(content=content, tool_calls=calls or [])


def _call(name: str, args: dict) -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, args=args)


async def test_immediate_answer_without_tools(monkeypatch):
    seen: list[list[dict]] = []

    async def fake_chat(messages, tools, *, temperature=0.2):
        seen.append(tools)
        return _reply("你好，有什么可以帮您？")

    monkeypatch.setattr(react, "tool_chat", fake_chat)

    result = await react.run("你好", user_id="u1")

    assert result.answer == "你好，有什么可以帮您？"
    assert result.steps == 1
    assert result.code == "ok"
    assert seen[0] == react.TOOLS


async def test_one_tool_then_answer(monkeypatch):
    replies = [
        _reply(calls=[_call("query_order", {"order_id": "1001"})]),
        _reply("订单 1001 已签收。"),
    ]

    async def fake_chat(messages, tools, *, temperature=0.2):
        return replies.pop(0)

    async def fake_execute(name, args, *, user_id):
        assert name == "query_order"
        assert args == {"order_id": "1001"}
        return {"code": "ok", "data": {"order_id": "1001"}}

    monkeypatch.setattr(react, "tool_chat", fake_chat)
    monkeypatch.setattr(react, "execute_tool", fake_execute)

    result = await react.run("订单1001 到哪了", user_id="u1")

    assert result.answer == "订单 1001 已签收。"
    assert result.steps == 2
    assert result.code == "ok"
    assert result.tool_calls == [{"name": "query_order", "code": "ok"}]


async def test_step_limit_forces_answer(monkeypatch):
    seen_tools: list[list[dict]] = []

    async def fake_chat(messages, tools, *, temperature=0.2):
        seen_tools.append(tools)
        if len(seen_tools) < react.MAX_STEPS:
            return _reply(calls=[_call("query_order", {"order_id": "1001"})])
        return _reply("基于已有结果回答。")

    async def fake_execute(name, args, *, user_id):
        return {"code": "ok", "data": {}}

    monkeypatch.setattr(react, "tool_chat", fake_chat)
    monkeypatch.setattr(react, "execute_tool", fake_execute)

    result = await react.run("查一下", user_id="u1")

    assert result.steps == react.MAX_STEPS
    assert result.code == "step_limit"
    assert result.answer == "基于已有结果回答。"
    assert seen_tools[-1] == []          # 最后一步不带工具


async def test_tool_error_is_fed_back(monkeypatch):
    replies = [
        _reply(calls=[_call("query_order", {"order_id": "1001"})]),
        _reply("暂时查不到。"),
    ]

    async def fake_chat(messages, tools, *, temperature=0.2):
        return replies.pop(0)

    async def fake_execute(name, args, *, user_id):
        return {"code": "timeout", "message": "工具执行超时"}

    monkeypatch.setattr(react, "tool_chat", fake_chat)
    monkeypatch.setattr(react, "execute_tool", fake_execute)

    result = await react.run("查一下", user_id="u1")

    assert result.answer == "暂时查不到。"
    assert result.tool_calls == [{"name": "query_order", "code": "timeout"}]


async def test_model_error_returns_error(monkeypatch):
    async def boom(messages, tools, *, temperature=0.2):
        raise TimeoutError("model timeout")

    monkeypatch.setattr(react, "tool_chat", boom)

    result = await react.run("查一下", user_id="u1")

    assert result.code == "error"
    assert result.answer == ""


async def test_search_kb_collects_citations(monkeypatch):
    replies = [
        _reply(calls=[_call("search_kb", {"query": "退货政策"})]),
        _reply("七天无理由[1]。"),
    ]

    async def fake_chat(messages, tools, *, temperature=0.2):
        return replies.pop(0)

    async def fake_execute(name, args, *, user_id):
        return {"code": "ok", "data": {"hits": [{"id": "returns#七天"}]}}

    monkeypatch.setattr(react, "tool_chat", fake_chat)
    monkeypatch.setattr(react, "execute_tool", fake_execute)

    result = await react.run("退货政策", user_id="u1")

    assert result.citations == ["returns#七天"]


async def test_whitelist_has_no_write_tools():
    assert set(react.TOOL_WHITELIST) == {
        "query_order",
        "query_logistics",
        "query_stock",
        "search_kb",
    }
    assert "create_ticket" not in react.TOOL_WHITELIST
    assert "submit_refund" not in react.TOOL_WHITELIST


async def test_execute_drops_user_id_and_normalizes(monkeypatch):
    seen: dict = {}

    async def fake_stock(sku_id, spec=None, *, session=None):
        seen["sku_id"] = sku_id
        seen["spec"] = spec
        return ToolResult(True, None, {})

    monkeypatch.setattr(react.order_tools, "query_stock", fake_stock)

    result = await react.execute_tool(
        "query_stock",
        {"sku_id": "st-20", "spec": 123, "user_id": "u2"},
        user_id="u1",
    )

    assert result["code"] == "ok"
    assert seen == {"sku_id": "ST-20", "spec": "123"}


async def test_missing_required_argument():
    result = await react.execute_tool("query_order", {}, user_id="u1")

    assert result["code"] == "invalid_arg"
    assert "order_id" in result["message"]


async def test_int_field_is_clamped(monkeypatch):
    seen: dict = {}

    async def fake_search(query, *, top_k):
        seen["top_k"] = top_k
        return []

    monkeypatch.setattr(react, "search_kb", fake_search)

    await react.execute_tool(
        "search_kb", {"query": "退货", "top_k": 99}, user_id="u1"
    )

    assert seen["top_k"] == 5


async def test_bad_int_field_uses_default(monkeypatch):
    seen: dict = {}

    async def fake_search(query, *, top_k):
        seen["top_k"] = top_k
        return []

    monkeypatch.setattr(react, "search_kb", fake_search)

    await react.execute_tool(
        "search_kb", {"query": "退货", "top_k": "abc"}, user_id="u1"
    )

    assert seen["top_k"] == 3


def test_every_tool_spec_has_handler():
    for name, spec in react.TOOL_SPECS.items():
        assert spec.name == name
        assert callable(spec.handler)


async def test_unknown_tool():
    result = await react.execute_tool("create_ticket", {}, user_id="u1")
    assert result["code"] == "unknown_tool"


def test_amount_is_converted_to_text():
    assert react._with_amount_text({"amount": 124900}) == {
        "amount_text": "1249.00 元"
    }


def test_price_rows_are_converted_to_text():
    out = react._with_price_text({"rows": [{"price": 129900, "stock": 42}]})
    assert out["rows"][0] == {"price_text": "1299.00 元", "stock": 42}


def test_result_is_truncated_and_wrapped():
    payload = {"text": "x" * (react.MAX_RESULT_CHARS + 100)}
    content = react._tool_content(payload)
    assert content.startswith("<tool_result>")
    assert content.endswith("</tool_result>")
    assert "…(截断)" in content
    assert len(content) <= (
        react.MAX_RESULT_CHARS + len("…(截断)") + len("<tool_result></tool_result>")
    )
