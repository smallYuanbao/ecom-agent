"""意图分类：12 类各一例（真实模型）+ 降级路径（mock，不走网络）。

## 这个文件是怎么被执行的

**没有显式调用方**——`uv run pytest` 是调用方。收集规则来自三处约定：

| 层级 | 约定 | 出处 |
|---|---|---|
| 目录 | `tests/` | `pyproject.toml` 的 `testpaths` |
| 文件 | `test_*.py` | pytest 默认约定（改成 `check_*.py` 就收不到，且不报错） |
| 函数 | `test_` 开头 | 同上 |

收集到之后：
- `@pytest.mark.parametrize` 在**收集阶段**把 `CASES` 的 12 行展开成 12 个独立用例，
  各自有 id，一个失败不影响其他
- `async def` 由 pytest-asyncio 插件包装成同步调用（`pyproject` 设了 `asyncio_mode="auto"`，
  不用逐个加 `@pytest.mark.asyncio`）
- 带 `@requires_api_key` 的用例在无密钥机器上 **skip**，不是 fail

## 两类测试，不要混

| 类型 | 用例 | 要密钥 | 验证什么 |
|---|---|---|---|
| 集成 | `test_intent_classification` ×12、`test_node_writes_route...` | ✅ | 模型判得对不对 / 真实调用链通不通 |
| 单元 | `test_format_error...`、`test_out_of_vocabulary...`、`test_node_falls_back...` | ❌ | 异常分支怎么降级 |

**能用真实调用验证的，就别 mock**；只有"现实中难以复现的异常"才值得 mock。

验收标准：12 例中 ≥11 命中（允许 1 例失败并记录）。实测 12/12。
"""
import pytest

from app.config import get_settings
from app.core.llm import LLMFormatError
from app.graph.nodes import classify, classify_intent
from app.graph.routing import DEFAULT_INTENT, FALLBACK_ROUTE, INTENT_TO_ROUTE

requires_api_key = pytest.mark.skipif(
    not get_settings().chat_api_key, reason="需要 CHAT_API_KEY"
)

# 每类一句典型问法
CASES: list[tuple[str, str]] = [
    ("你们的退货政策是什么？", "政策问答"),
    ("ST-60 有多重，折叠后多大？", "商品规格"),
    ("满 300 减 50 的券能跟会员折扣一起用吗？", "优惠券活动"),
    ("会员积分怎么算，可以抵现吗？", "会员权益"),
    ("帮我看看订单 1001 的状态", "订单查询"),
    ("我的快递到哪了？", "物流查询"),
    ("ST-20 还有货吗？多少钱？", "库存价格"),
    ("订单 1001 我要退货", "退款申请"),
    ("我上周买的推车降价了，能补差价吗？", "价保申请"),
    ("订单 1001 能改一下收货地址吗？", "订单变更"),
    ("你们这什么破服务，等了三天都没人管", "投诉"),
    ("在吗", "闲聊"),
]


@requires_api_key
@pytest.mark.parametrize(("query", "expected"), CASES)
async def test_intent_classification(query: str, expected: str):
    intent, confidence = await classify(query)
    assert intent == expected, f"「{query}」判成 {intent}（期望 {expected}）"
    assert 0.0 <= confidence <= 1.0


@requires_api_key
async def test_node_writes_route_consistent_with_routing_table():
    """节点写的 route 必须与路由表一致，否则 state 与图的分支会打架。"""
    result = await classify_intent({"resolved_query": "订单 1001 我要退货"})
    assert result["route"] == INTENT_TO_ROUTE.get(result["intent"], FALLBACK_ROUTE)
    assert result["route"] == "rules"


# ─────────────────────────────────────────────────────────────────────
# 以下三条是单元测试：用 monkeypatch 拦住外部依赖，完全不发网络请求。
#
# 【为什么不走网络】
#   `classify()` 里写的是 `structured_call(...)`，调用时去 `app.graph.nodes`
#   这个模块的全局字典里查这个名字。把那个变量换成假函数，就根本走不到
#   AsyncOpenAI，自然也不会发 HTTP。
#
# 【为什么 patch `app.graph.nodes.structured_call` 而不是 `app.core.llm.structured_call`】
#   `from app.core.llm import structured_call` 会在 nodes 模块里**新建一个同名变量**，
#   两者指向同一个函数对象，但是两个独立的绑定。改定义处（llm）不会影响
#   使用处（nodes）已经绑好的名字。
#   口诀：**patch 使用处，不是定义处。**
#
# 【monkeypatch 是 pytest 内置夹具】
#   它记录每次修改，测试结束（无论通过还是失败）自动还原。若直接
#   `nodes.structured_call = boom` 手工赋值，脏数据会漏给后面的测试，
#   而且报错位置离真正原因很远，极难排查。
# ─────────────────────────────────────────────────────────────────────


async def test_format_error_degrades_to_default_intent(monkeypatch):
    """模型没按格式返回 → 降级成「其他」，不抛异常。"""

    async def boom(*_args, **_kwargs):
        """假的结构化调用：直接抛格式错误，模拟模型没返回 tool_calls。

        参数名带下划线前缀 = 明确表示"接收但故意不用"（要兼容真实函数的签名）。
        """
        raise LLMFormatError("模型未返回 tool_calls")

    monkeypatch.setattr("app.graph.nodes.structured_call", boom)
    intent, confidence = await classify("随便问点什么")
    assert (intent, confidence) == (DEFAULT_INTENT, 0.0)


async def test_out_of_vocabulary_intent_degrades(monkeypatch):
    """模型返回了清单外的意图词 → 也降级成「其他」，不让 KeyError 冒出来。"""

    async def weird(*_args, **_kwargs):
        """假的结构化调用：返回一个语法正确、但**不在白名单里**的意图词。"""
        return {"intent": "退款", "confidence": 0.9}   # 「退款」不在 12 类里

    monkeypatch.setattr("app.graph.nodes.structured_call", weird)
    intent, _ = await classify("我要退款")
    assert intent == DEFAULT_INTENT


async def test_node_falls_back_to_raw_query_when_resolved_empty(monkeypatch):
    """`resolved_query` 为空时回落原话，不能把空串送去分类。

    这里 patch 的是 `classify`（不是 `structured_call`）——测的是**节点**的取值逻辑，
    不需要走到模型那一层。patch 得越靠上，测试越快、越稳。
    """
    seen: list[str] = []       # 记录节点实际传下去的 query，用于断言

    async def capture(query: str):
        """假的 classify：只记录入参，返回固定结果。"""
        seen.append(query)
        return "闲聊", 0.9

    monkeypatch.setattr("app.graph.nodes.classify", capture)
    result = await classify_intent({"raw_query": "在吗", "resolved_query": ""})
    assert seen == ["在吗"]
    assert result["route"] == "fallback"
