# Ch04 B 出口 实施计划

> **For agentic workers:** 按 task 顺序执行，每个 task 完成即 commit。步骤用 `- [ ]` 追踪。

**Goal:** 把 `data` 出口从占位换成 5 状态数据查询子图：订单 / 物流 / 库存，**B 出口内部零模型调用**。

**Architecture:** LangGraph 子图，5 状态 / 8 转移；参数用正则 + 数据库匹配，回答用模板；物流固定两级链。

**Tech Stack:** 沿用 Ch01–Ch03（LangGraph / async SQLAlchemy / FastAPI），不引入新依赖。

**Spec:** [ch04-data-exit-design.md](../specs/ch04-data-exit-design.md)
**前置:** Ch01–Ch03 已交付；主图 `data` 节点仍是占位

---

## Global Constraints

- **技术选型不变**：不加新依赖。
- **B 出口内部零模型调用**：参数正则 + 数据库匹配，回答模板化。
- **金额 / 库存逐字来自工具返回**：模板不做二次计算。
- **越权与不存在同一句**：不泄露订单是否存在。
- **物流固定两级链**：订单 → `tracking_no` → 轨迹，不让模型选工具。
- **失败不猜**：查不到说查不到，没有物流信息就明说。
- 每个 task 完成即 commit。

---

### Task 1: 共享代码抽取（纯搬移）

**Files:**
- Create: `app/core/format.py`、`app/graph/order_select.py`、`app/graph/params.py`、`app/graph/tracing.py`
- Modify: `app/graph/rules_exit.py`
- Test: `tests/core/test_format.py`、`tests/graph/test_params.py`、`tests/graph/test_order_select.py`

**Step 1: `app/core/format.py`**

```python
def yuan_text(cents: int) -> str:
    """分 → 「1249.00 元」。整数运算，不用浮点。"""
```

从 `rules_exit.yuan_text` 原样搬过来。

**Step 2: `app/graph/params.py`**

```python
ORDER_ID_RE = re.compile(r"(?<!\d)(\d{4,})(?!\d)")
SKU_RE = re.compile(r"[A-Za-z]{2}-\d{2}")

def extract_order_id(text: str) -> str | None
def extract_sku(text: str) -> str | None      # 统一转大写
```

**Step 3: `app/graph/order_select.py`**

```python
def candidate(order: Order) -> dict            # 原 rules_exit._candidate
async def ask_order(state, *, session=None) -> dict
```

`ask_order` 只返回**追问载荷**（`order_candidates` / `pending_question` / `answer` / `actions`），
**不写 `validate_retry`**——那是 C 出口自己的计数，B 不关心。

**Step 4: `app/graph/tracing.py`**

```python
def traced(prefix: str, name: str, node):     # 原 rules_exit._traced
    """给子图节点套 span，名字为 f"{prefix}:{name}"，如 `rules:judge`。"""
```

`rules_exit` 改成 `traced("rules", name, ...)`，span 名不变。
`name` 是节点键名（如 `intake`），必须与图的节点名一致，否则 trace 里对不上边表。

**Step 5: 改 `rules_exit` 的引用**

四处改成 import 共享模块，**行为不变**。现有 C 出口测试是这次搬移的安全网。
（`tests/graph/rules/` 下的测试一条都不改，全绿即说明搬移没改坏行为。）

**测试：**
- `yuan_text`：0 / 1 / 100 / 123456
- `extract_order_id` / `extract_sku`：带上下文、裸值、大小写、无匹配
- `ask_order`：默认；无 `user_id`；用户无订单
- `traced`：span 名 = `prefix:node`，返回值不变

**验收**：`pytest` 全绿（数量与搬移前一致，只多新增的单测）。

**Commit:** `refactor(graph): 抽取共享的金额/候选/参数/span 工具`

---

### Task 2: 工具层（只读查询）

**Files:**
- Create: `app/tools/orders.py`、`tests/tools/test_orders.py`
- Modify: `app/db/repo_order.py`（加 `list_stock_by_sku`）

**Step 1: `ToolResult`**

```python
@dataclass(frozen=True)
class ToolResult:
    ok: bool
    code: str | None      # not_found / invalid_arg / timeout / internal
    data: dict | None
```

**Step 2: 四个查询**

```python
async def query_order(order_id: str, *, user_id: str, session=None) -> ToolResult
async def query_logistics(tracking_no: str, *, user_id: str, session=None) -> ToolResult
async def query_stock(sku_id: str, spec: str | None = None, *, session=None) -> ToolResult
async def list_stock_skus(*, session=None) -> ToolResult
async def list_orders(*, user_id: str, session=None) -> ToolResult
```

| 函数 | 逻辑 | `data` 形状 |
|---|---|---|
| `query_order` | `repo.get_order`；`NotFound` → `not_found` | `{order_id,status,amount,quantity,created_at,tracking_no}` |
| `query_logistics` | `repo.get_tracking`；`nodes` 坏 JSON 降级为 `[]` | `{tracking_no,latest_status,nodes}` |
| `query_stock(spec=None)` | 列出该型号全部规格 | `{sku_id, rows:[{spec,stock,price,status}]}` |
| `query_stock(spec=...)` | 精确查一行 | `{sku_id,spec,stock,price,status}` |
| `list_stock_skus` | 去重的型号列表（缺型号时列候选） | `{skus:["ST-20",...]}` |
| `list_orders` | 最近 3 单，转候选形状 | `{candidates:[...]}` |

**Step 3: 错误映射**

```python
except NotFound:            return ToolResult(False, "not_found", None)
except (TimeoutError, asyncio.TimeoutError): return ToolResult(False, "timeout", None)
except Exception:           logger.warning(...); return ToolResult(False, "internal", None)
```

`internal` 要打日志（带 `order_id` / `sku_id`），但不把堆栈透给上层。

**Step 4: `repo_order.list_stock_by_sku`**

```python
async def list_stock_by_sku(sku_id: str, *, session=None) -> list[Stock]
```

按 `sku_id` 查全部规格，价格升序（展示稳定）。**不需要 user_id**：库存是公开数据。

**测试**（用临时库的 `session` 夹具）：
- 订单：本人可查；他人 / 不存在都是 `not_found`（同一结果）
- 物流：正常轨迹；`nodes` 是坏 JSON → `ok` 且 `nodes=[]`；无轨迹 → `not_found`
- 库存：有货 / 售罄 / 预售；`spec` 不给时返回全部规格
- 超时 / 未知异常 → `internal`，不抛

**验收**：错误码只有 4 个；任何输入都不抛异常。

**Commit:** `feat(tools): 订单/物流/库存只读查询`

---

### Task 3: 5 状态子图骨架

**Files:**
- Create: `app/graph/data_exit.py`、`tests/graph/data/test_graph.py`
- Modify: `app/graph/state.py`

**Step 1: 状态字段**

`ConversationState` 增加：

```python
sku_id: str
sku_candidates: list[dict]
spec_candidates: list[dict]
data_result: dict
tool_code: str            # ok / not_found / invalid_arg / timeout / internal
```

**Step 2: 5 节点全用 `_stub`**

`NODE_NAMES = ("extract_params", "validate", "query", "need_info", "format")`。

**Step 3: 边与守卫**

```python
graph.add_edge("extract_params", "validate")     # 无条件
VALIDATE_NEXT = {"query": "query", "need_info": "need_info"}
QUERY_NEXT = {"format": "format", "need_info": "need_info", "human": END}
```

| 守卫 | 判断 |
|---|---|
| `after_validate` | `pending_question` 非空 → `need_info`；否则 `query` |
| `after_query` | `tool_code`：`ok` → `format`；`not_found` / `invalid_arg` → `need_info`；其余 → `human` |
| `after_need_info` | 总是 `end` |

**为什么用 `pending_question` 而不是各判各的**：`validate` 写一次，守卫和 `need_info`
都读它，避免"守卫说缺订单号、追问却问型号"这种漂移。

**Step 4: 空状态能终止**

全部 stub 时：`extract_params` 空 → `validate` 空 → 守卫看不到 `pending_question` → `query` 空
→ 守卫看不到 `tool_code` → **转人工（fail-closed）** → END。

Task 4/5 实现后，同样的空状态会先停在 `need_info`（问订单号）。
**骨架阶段只要求"能终止"**，不要求走对分支。

**测试：**
- 5 个节点都存在；8 条边与 spec 逐条相等
- 每个守卫的返回值都在映射里
- 空状态能终止（不进入死循环）

**Commit:** `feat(data): 5 状态子图骨架`

---

### Task 4: 抽参数 + 按意图校验

**Files:** Modify `app/graph/data_exit.py`；Test `tests/graph/data/test_extract_params.py`

**Step 1: `extract_params`**

```python
async def extract_params(state):
    query = state.get("resolved_query") or state.get("raw_query") or ""
    updates = {}
    if order_id := extract_order_id(query):
        updates["order_id"] = order_id
    if sku := extract_sku(query):
        updates["sku_id"] = sku
    return updates
```

**Step 2: `validate`**

只判断"缺什么"，**不查库**——存在性与归属交给 `query` 的工具层：

```python
async def validate(state):
    intent = state.get("intent")
    if intent in {"订单查询", "物流查询"} and not state.get("order_id"):
        return {"pending_question": "order_id"}
    if intent == "库存价格" and not state.get("sku_id"):
        return {"pending_question": "sku_id"}
    return {}          # 留空 = 参数齐全
```

**测试：**
- 订单号 / SKU / 大小写 / 无参数
- 三类意图各缺一参 → `pending_question` 正确
- 参数齐全 → `pending_question` 不设置

**Commit:** `feat(data): 抽参数与校验`

---

### Task 5: `need_info` 列候选

**Files:** Modify `app/graph/data_exit.py`；Test `tests/graph/data/test_need_info.py`

| `pending_question` | 行为 |
|---|---|
| `order_id` | 复用 `order_select.ask_order` |
| `sku_id` | `list_stock_skus()` → `sku_candidates` + `actions.type=select_sku` |
| `spec` | `query_stock(sku_id)` 拿全部规格 → `spec_candidates` + `actions.type=select_spec` |

- 无候选时退化成文字追问（例如库里没有库存行）
- `is_placeholder=False`
- `need_info` 结尾 `return`，守卫走 END

**测试：**
- 缺单号 → 3 个候选 + `select_order`
- 缺型号 → 型号候选 + `select_sku`
- 缺规格 → 规格候选 + `select_spec`
- 无候选 → 纯文本

**Commit:** `feat(data): 缺参列候选`

---

### Task 6: `query` 查询链 + 结果校验

**Files:** Modify `app/graph/data_exit.py`；Test `tests/graph/data/test_query.py`

**Step 1: 按意图分派**

```python
if intent == "订单查询":
    result = await query_order(order_id, user_id=uid)
elif intent == "物流查询":
    order = await query_order(order_id, user_id=uid)
    if not order.ok: ...
    if not order.data["tracking_no"]:
        data_result = {"order": order.data, "tracking": None}
    else:
        tracking = await query_logistics(order.data["tracking_no"], user_id=uid)
elif intent == "库存价格":
    listed = await query_stock(sku_id)            # 一次拿到该型号全部规格
    rows = listed.data["rows"]
    spec = match_spec(query, rows)
    if spec is None:
        return {"pending_question": "spec"}       # 多规格且没提 → 追问
    result = ToolResult(True, None, {"sku_id": sku_id, **next(r for r in rows if r["spec"] == spec)})
```

**Step 2: 规格匹配**

```python
def match_spec(query: str, rows: list[dict]) -> str | None:
    hits = [r["spec"] for r in rows if r["spec"] and r["spec"] in query]
    if hits:
        return max(hits, key=len)      # 多个命中取最长，防"灰"匹配到"星光灰"
    if len(rows) == 1:
        return rows[0]["spec"]         # 只有一个规格，用户不说也能确定
    return None                        # 多个规格且没提 → need_info
```

**Step 3: 写 `data_result` 与 `tool_code`**

| 意图 | `data_result` |
|---|---|
| 订单 | `{order_id, status, amount, quantity, created_at, tracking_no}` |
| 物流 | `{tracking_no, latest_status, nodes}` 或 `{order, tracking: None}` |
| 库存 | `{sku_id, spec, stock, price, status}` |

**Step 4: 结果校验**

守卫 `after_query` 检查必需字段（见 spec），缺字段 → `human`，写 `transfer_reason="data_incomplete"`。

**测试：**
- 订单 happy path / 他人单 / 不存在
- 物流两级链；`tracking_no` 为空；`nodes` 坏 JSON
- 库存：规格命中 / 多规格未提 → need_info / 单一规格自动选
- 工具 `timeout` → 转人工
- 缺字段 → 转人工

**Commit:** `feat(data): 查询链与结果校验`

---

### Task 7: `format` 模板化回答

**Files:** Modify `app/graph/data_exit.py`；Test `tests/graph/data/test_format.py`

**Step 1: 模板**

| 意图 | 模板 |
|---|---|
| 订单 | `订单 {id}：状态 {status}；实付 {amount}；下单时间 {created}。` + 有单号时 `物流单号 {no}。` |
| 物流 | `快递 {no}：最新状态 {latest}。` + `最近轨迹：{time} {status} {desc}；…` |
| 无物流 | `订单 {id} 当前状态：{status}，暂无物流信息。` |
| 库存 | `{sku} {spec}：{货态}，售价 {price}。` |

**Step 2: 货态**

| `status` | `stock` | 文案 |
|---|---|---|
| `on_sale` | >0 | 有货（N 件） |
| `on_sale` | 0 | 暂时无货 |
| `sold_out` | — | 已售罄 |
| `presale` | — | 预售中 |

**Step 3: 格式细节**

- 金额用 `yuan_text`，**逐字**来自 `data_result`
- 时间：`2026-10-01 10:00`（去掉 ISO 的 `T` 和秒）
- 轨迹最多 3 条，按时间倒序

**测试：**
- 三个模板逐字断言
- 订单无物流单号
- `nodes` 超过 3 条只取最近 3 条
- 四种货态
- 金额等于数据库值

**Commit:** `feat(data): 模板化回答`

---

### Task 8: 接入主图、删除占位

**Files:**
- Modify: `app/graph/build.py`、`app/graph/nodes.py`
- Modify: `tests/graph/test_graph_smoke.py`、`tests/graph/test_routing.py`

**Step 1: 接线**

```python
g.add_node("data", build_data_graph())
```

**Step 2: 删占位**

- 删除 `nodes.data_exit`
- 删除 `PLACEHOLDER_TEXT` 与 `_placeholder()`（此时已无消费者）
- `EXIT_NODES` 不变

**Step 3: 改测试**

- `test_graph_smoke.py`：`REAL_EXITS` 加入 `data`，`PLACEHOLDER_EXITS` 变为空
- `test_routing.py`：删除占位行为的三组测试（机制已不存在）
- **离线夹具要打桩数据工具**：`test_every_intent_lands_on_its_route` 会用
  `raw_query="测试"` 走一遍所有意图，`库存价格` 会触发 `list_stock_skus`。
  在 `offline` 夹具里 monkeypatch `app.graph.data_exit.list_stock_skus` 和
  `order_select.list_user_orders` 返回空结果，保证不碰真实库。

**验收**：`pytest` 全绿；主图 5 个出口都是真实实现。

**Commit:** `feat(data): 接主图并删除占位`

---

### Task 9: 验收

**Files:** Create `docs/reports/ch04-acceptance.md`；Modify `docs/superpowers/plans/00-tasks.md`（P4）

**Step 1: 端到端**

```bash
uv run python scripts/try.py graph "订单1001 到哪了"
uv run python scripts/try.py graph "ST-20 还有货吗"
uv run python scripts/try.py graph "订单1002 到哪了"
```

**Step 2: 延迟**

B 出口零模型调用（分类除外），预期 < 1s。

**Step 3: 验收清单**

- [ ] `pytest` 全绿
- [ ] 三个意图各一条 happy path
- [ ] 缺单号列候选
- [ ] 越权与不存在同一句
- [ ] 四种货态文案正确
- [ ] 待发货查物流不报错
- [ ] 每个节点一行 span
- [ ] B 出口模型调用次数 = 0（分类除外）
- [ ] 更新 P4 勾选
- [ ] 写 `ch04-acceptance.md`

**Commit:** `test: Ch04 验收（三类查询 + 转人工路径）`

---

## 与 spec 的对应

| spec 验收项 | 落在哪个 Task |
|---|---|
| 三类意图 happy path | Task 6 / 9 |
| 物流两级链 | Task 6 |
| 缺单号列候选 | Task 5 |
| 越权与不存在同响应 | Task 2 |
| 货态文案 | Task 7 |
| 结果校验 | Task 6 |
| 每节点 span | Task 3 |
| 零模型调用 | 全章（无模型节点） |

## 风险

| 风险 | 应对 |
|---|---|
| 规格匹配误判（"灰"匹配到"星光灰"） | 取最长命中；多规格且无命中 → 追问，不猜 |
| 离线测试碰真实库 | Task 8 在 `offline` 夹具里打桩 `list_stock_skus` / `list_user_orders` |
| 物流为空的语义被当成错误 | 明确区分 `not_found`（无轨迹）与"订单还没发货"（`tracking_no=None`） |
| 模板里的数字被二次加工 | `format` 只读 `data_result`，不做除法；金额统一 `yuan_text` |
| 删除占位导致其他测试引用 | Task 8 同步改 `test_routing` / `test_graph_smoke`，`pytest` 兜底 |
