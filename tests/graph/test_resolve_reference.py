"""`resolve_reference` 节点：改写、resume 透传、trace。"""
from app.graph import nodes

HISTORY = [{"role": "user", "content": "订单1001 到哪了"}]


async def test_node_uses_coref_and_writes_span(monkeypatch):
    seen: dict = {}

    async def fake_resolve(query, history, *, summary=None):
        seen["summary"] = summary
        return "订单1001 的物流到哪了", "rewrite"

    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(nodes.coref, "resolve", fake_resolve)
    monkeypatch.setattr(nodes, "record_span", fake_record)

    result = await nodes.resolve_reference(
        {
            "raw_query": "那单呢",
            "history": HISTORY,
            "summary": "用户之前问过订单1001",
            "trace_id": "t-1",
        }
    )

    assert result == {"resolved_query": "订单1001 的物流到哪了", "resumed": False}
    assert seen["summary"] == "用户之前问过订单1001"
    assert recorded[0]["name"] == "coref"
    assert recorded[0]["attrs"]["mode"] == "rewrite"
    assert recorded[0]["attrs"]["history_count"] == 1
    assert recorded[0]["attrs"]["summary_used"] is True


async def test_resume_short_circuits_without_coref(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("resume 轮不应该改写")

    monkeypatch.setattr(nodes.coref, "resolve", boom)

    result = await nodes.resolve_reference(
        {"raw_query": "1001", "resume_route": "rules"}
    )

    assert result == {"resolved_query": "1001", "resumed": True}


async def test_no_trace_id_does_not_write_span(monkeypatch):
    async def fake_resolve(query, history, *, summary=None):
        return "改写后", "rewrite"

    async def boom(**kwargs):
        raise AssertionError("没有 trace_id 不应写 span")

    monkeypatch.setattr(nodes.coref, "resolve", fake_resolve)
    monkeypatch.setattr(nodes, "record_span", boom)

    result = await nodes.resolve_reference({"raw_query": "那单呢"})

    assert result["resolved_query"] == "改写后"
