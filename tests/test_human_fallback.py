"""D 转人工 与 E 兜底三脚本。

两个出口都是**零模型**的：纯关键词 + 固定话术。
不调 LLM 是刻意的——这类输入不值得一次调用，且模型容易"过度发挥"，
把一句「谢谢」答成一段推销。
"""
import pytest

from app.graph.nodes import (
    SOCIAL_REPLY,
    THANKS_REPLY,
    UNKNOWN_REPLY,
    fallback_exit,
    human_exit,
)

# ── D 出口 ──────────────────────────────────────────────────


async def test_human_exit_offers_transfer():
    result = await human_exit({})
    assert "转接人工" in result["answer"]
    assert {"type": "transfer_human"} in result["actions"]
    assert result["is_placeholder"] is False


async def test_human_exit_promises_nothing():
    """没有工单就不能承诺处理时效——否则用户会拿这句话当真。"""
    answer = (await human_exit({}))["answer"]
    for word in ("分钟", "小时", "工作日", "一定"):
        assert word not in answer


# ── E 出口：三个脚本 ────────────────────────────────────────


@pytest.mark.parametrize("text", ["在吗", "你好", "在不在", "有人吗", "hello"])
async def test_social_script(text: str):
    result = await fallback_exit({"raw_query": text})
    assert result["answer"] == SOCIAL_REPLY
    assert result["is_placeholder"] is False


@pytest.mark.parametrize("text", ["谢谢", "多谢", "好的", "收到", "辛苦了"])
async def test_thanks_script(text: str):
    result = await fallback_exit({"raw_query": text})
    assert result["answer"] == THANKS_REPLY


@pytest.mark.parametrize("text", ["你们老板是谁", "能加盟吗", "asdfghjkl"])
async def test_unknown_script_offers_transfer(text: str):
    result = await fallback_exit({"raw_query": text})
    assert result["answer"] == UNKNOWN_REPLY
    assert {"type": "transfer_human"} in result["actions"]


# ── 顺序：先排除业务意图，再走脚本 ──────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "谢谢你，那退货怎么弄",
        "在吗？我要查订单",
        "好的，帮我看看发票",
        "收到，物流到哪了",
    ],
)
async def test_greeting_mixed_with_business_does_not_use_script(text: str):
    """寒暄 + 业务混在一句里时，走兜底而不是答「不客气」。

    这是关键词方案最容易翻车的地方：只要出现业务词，就不能按寒暄处理。
    """
    result = await fallback_exit({"raw_query": text})
    assert result["answer"] == UNKNOWN_REPLY
    assert {"type": "transfer_human"} in result["actions"]


async def test_uses_resolved_query_first():
    """有 `resolved_query` 时以它为准（Ch05 指代消解补全后的句子更完整）。"""
    result = await fallback_exit({"raw_query": "在吗", "resolved_query": "谢谢你"})
    assert result["answer"] == THANKS_REPLY


async def test_empty_query_falls_to_unknown():
    result = await fallback_exit({})
    assert result["answer"] == UNKNOWN_REPLY
