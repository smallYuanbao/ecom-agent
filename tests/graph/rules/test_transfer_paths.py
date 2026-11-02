"""Ch03 验收：四条"转人工"路径各走一遍整张子图。

这是**集成**测试：从 `intake` 进、到 `transfer_reason` 出，中间经过真实的状态机与守卫。
四个场景分别命中不同节点：

| 场景 | 在哪触发 | 期望 `transfer_reason` |
|---|---|---|
| 模型拿不准 | `judge` | `judge_low_confidence` |
| 模型与代码分歧 | `compliance` | `rule_divergence: ...` |
| 政策检索为空 | `query_data` | `policy_retrieval_empty` |
| 日期倒挂 | `validate` | `date_anomaly` |

模型、数据库、检索全部打桩，不依赖外部服务，CI 可跑。
"""
from datetime import datetime, timedelta

from app.core.prompts import JUDGE_TOOL_NAME
from app.db.errors import NotFound
from app.db.models import Order
from app.graph import rules_exit
from app.graph.rules_exit import build_rules_graph

CREATED = datetime(2026, 10, 29, 10, 0)

HIT = [{"id": "returns#退款时效", "text": "自签收次日起 7 日内", "similarity": 0.8}]

GOOD_JUDGEMENT = {"eligible": True, "reason_code": "ok", "confidence": 0.9}


def make_order(**overrides) -> Order:
    defaults = {
        "id": "1002",
        "user_id": "u1",
        "sku_id": "ST-20",
        "spec": "石墨灰",
        "category": "婴儿推车",
        "status": "已签收",
        "amount": 124900,
        "quantity": 1,
        "created_at": CREATED,
        "paid_at": CREATED,
        "shipped_at": CREATED + timedelta(days=1),
        "signed_at": CREATED + timedelta(days=3),
        "tracking_no": "SF1001002",
    }
    defaults.update(overrides)
    return Order(**defaults)


def patch_env(
    monkeypatch,
    *,
    order: Order | None = None,
    hits: list[dict] | None = None,
    model: dict | None = None,
    extract: dict | None = None,
):
    """把 DB / 检索 / 模型全部钉死。默认走"一切正常"的路径。"""
    resolved_order = order if order is not None else make_order()

    async def _get_order(order_id: str, *, user_id: str):
        if resolved_order is None:
            raise NotFound("order not found")
        return resolved_order

    async def _search(query: str, *, top_k: int = 5, strategy=None):
        return HIT if hits is None else hits

    async def _structured(tool_name, parameters, messages, **kwargs):
        if tool_name == JUDGE_TOOL_NAME:
            return model if model is not None else dict(GOOD_JUDGEMENT)
        return extract if extract is not None else {"reason_code": "no_reason"}

    async def _span(**kwargs):
        return None

    monkeypatch.setattr(rules_exit, "get_order", _get_order)
    monkeypatch.setattr(rules_exit, "search", _search)
    monkeypatch.setattr(rules_exit, "structured_call", _structured)
    monkeypatch.setattr(rules_exit, "record_span", _span)


async def run(query: str = "订单1002 不想要了", **state) -> dict:
    return await build_rules_graph().ainvoke(
        {"raw_query": query, "user_id": "u1", "trace_id": "t-transfer", **state}
    )


# ── 1. 模型拿不准 ──────────────────────────────────────────


async def test_low_confidence_transfers(monkeypatch):
    patch_env(
        monkeypatch,
        model={"eligible": True, "reason_code": "ok", "confidence": 0.4},
    )
    result = await run()

    assert result["transfer_reason"] == "judge_low_confidence"
    assert result["actions"] == [{"type": "transfer_human"}]


# ── 2. 模型与代码分歧 ──────────────────────────────────────


async def test_divergence_transfers(monkeypatch):
    """模型说可退，规则代码按状态判不可退——分歧必须转人工。"""
    patch_env(monkeypatch, order=make_order(status="已关闭"))
    result = await run()

    assert result["compliance_violation"].startswith("rule_divergence:")
    assert "model=ok" in result["transfer_reason"]
    assert "code=wrong_status" in result["transfer_reason"]


# ── 3. 政策检索为空 ────────────────────────────────────────


async def test_empty_policy_transfers(monkeypatch):
    patch_env(monkeypatch, hits=[])
    result = await run()

    assert result["transfer_reason"] == "policy_retrieval_empty"
    assert "refund_judgement" not in result          # 根本没走到 judge


# ── 4. 日期倒挂 ────────────────────────────────────────────


async def test_date_anomaly_transfers(monkeypatch):
    patch_env(
        monkeypatch,
        order=make_order(
            shipped_at=CREATED + timedelta(days=5),
            signed_at=CREATED + timedelta(days=3),
        ),
    )
    result = await run()

    assert result["validate_errors"] == ["date_anomaly"]
    assert result["transfer_reason"] == "date_anomaly"
    assert "refund_judgement" not in result          # 追问和判定都没有发生


# ── 5. 追问次数用尽（第 5 条，附赠）────────────────────────


async def test_retry_exhausted_transfers(monkeypatch):
    patch_env(monkeypatch, extract={})               # 抽不到原因
    result = await run(validate_retry=2)

    assert result["validate_errors"] == ["missing_reason_code"]
    assert result["transfer_reason"] == "validate_retry_exhausted"


# ── 对照：正常路径不转人工 ────────────────────────────────


async def test_happy_path_does_not_transfer(monkeypatch):
    patch_env(monkeypatch)

    async def _chat(messages, **kwargs):
        return "可以退，本次应退 1249.00 元。"

    monkeypatch.setattr(rules_exit, "llm_chat", _chat)

    result = await run()

    assert "transfer_reason" not in result
    assert result["refund_amount"] == 124900
    assert "1249.00 元" in result["answer"]
