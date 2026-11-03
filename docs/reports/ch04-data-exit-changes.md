# Ch04 · B 出口（数据查询） · 改动记录

> 对应 spec：[ch04-data-exit-design.md](../superpowers/specs/ch04-data-exit-design.md)
> 对应 plan：[ch04-data-exit.md](../superpowers/plans/ch04-data-exit.md)
> 格式说明见 [00-change-log-overview.md](./00-change-log-overview.md)

| # | 日期 | 任务 | commit |
|---|---|---|---|
| 1 | 2026-11-03 | Task 1 · 共享代码抽取 | `6563952` |
| 2 | 2026-11-03 | Task 2 · 工具层（只读查询） | `bf512c6` |
| 3 | 2026-11-03 | Task 3 · 5 状态子图骨架 | `3998ed6` |
| 4 | 2026-11-03 | Task 4 · 抽参数与按意图校验 | `c43e1d2` |

---

## 1 · Task 1 · 共享代码抽取（`6563952`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/format.py` | `yuan_text(cents)`：分 → 「1249.00 元」，整数运算 |
| `app/graph/params.py` | `ORDER_ID_RE` / `SKU_RE`、`extract_order_id` / `extract_sku` |
| `app/graph/order_select.py` | `CANDIDATE_LIMIT`、`candidate(order)`、`ask_order(state)` |
| `app/graph/tracing.py` | `traced(prefix, name, node)`：子图节点的 span 包装 |
| `tests/core/test_format.py` | 7 条：金额格式 + 大数精度 |
| `tests/graph/test_params.py` | 16 条：订单号 / SKU 提取 |
| `tests/graph/test_order_select.py` | 5 条：候选形状、降级、不写 `validate_retry` |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `rules_exit.py` · `ORDER_ID_RE` / `extract_order_id` | 定义在 C 出口模块里 | 搬到 `graph/params.py` | B 出口也要抽订单号；留在 C 里会让 B import C，形成反向依赖 |
| `rules_exit.py` · `yuan_text` | 定义在 C 出口模块里 | 搬到 `core/format.py` | 金额格式是跨出口的展示约定，不是退款逻辑 |
| `rules_exit.py` · `_candidate` / `_ask_order` | 私有函数，B 用不到 | `graph/order_select.py` 的 `candidate` / `ask_order` | B 和 C 都要"缺单号列候选"，复制会立刻产生两套话术 |
| `rules_exit.py` · `_traced` | 每个子图各写一份 | `graph/tracing.py` 的 `traced(prefix, name, node)` | B 子图也要逐节点 span；复制粘贴容易把 span 名写错 |
| `tests/graph/rules/*` | patch `rules_exit.list_user_orders` / `record_span` | patch `order_select.list_user_orders` / `tracing.record_span` | 函数搬家后，patch 旧位置不会生效——测试会静默失效 |

### 两个搬移时的细节

**1. `ask_order` 不写 `validate_retry`。** 那是 C 出口自己的重试计数，B 不关心。
共享函数只返回追问载荷，C 在外面加自己的字段。

**2. `traced` 多一个 `name` 参数。** 原 `_traced(name, node)` 里的 `name` 是节点键名；
共享版需要区分前缀（`rules` / `data`）和节点名，所以签名是 `traced(prefix, name, node)`。
span 名仍是 `rules:intake`，没有变化。

### 验证

- `uv run pytest -q` → 618 passed, 42 skipped
- `uv run ruff check app tests` → All checks passed
- C 出口测试一条未改（只改了 import 与 patch 位置），全绿说明搬移没改行为

---

## 2 · Task 2 · 工具层（`bf512c6`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/tools/orders.py` | `ToolResult` + `query_order` / `query_logistics` / `query_stock` / `list_stock_skus` |
| `tests/tools/test_orders.py` | 19 条：三个查询的 happy path / 越权 / 缺参 / 坏数据 / 内部异常 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `repo_order.py` · 库存查询 | 只有 `get_stock(sku_id, spec)`，必须同时给规格 | 加 `list_stock_by_sku` 与 `list_stock` | 用户只报型号时要先列全部规格；缺型号时要列全部 SKU |
| `rules_exit` / `data_exit` 的错误处理 | 各自 try/except | 工具层统一 `ToolResult` | 图里只做分支；错误码只有四个，可枚举、可测试 |
| spec / plan 的工具清单 | 列了 `list_orders` | 删掉，注明复用 `order_select.ask_order` | 两处实现同一套候选形状，迟早话术不一致 |

### 三个设计点

**1. 越权与不存在共用 `not_found`。** 仓库层对两者都抛同一个 `NotFound`（S1）；
工具层不再细分，上层只有一句「没有找到这笔订单」。

**2. 坏 JSON 降级，不抛异常。** 物流 `nodes` 是 JSON 文本，
解析失败返回 `[]`，只显示 `latest_status`——数据问题不该让整轮失败。

**3. `session` 可注入。** 沿用 `session_scope` 的约定：生产不传，测试传临时库。
否则测试只能打桩 repo，测不到真实的 SQL 路径。

### 验证

- `uv run pytest tests/tools -q` → 19 passed
- `uv run pytest -q` → 637 passed, 42 skipped
- `uv run ruff check app tests` → All checks passed

---

## 3 · Task 3 · 5 状态子图骨架（`3998ed6`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/graph/data_exit.py` | 5 个空节点、3 个守卫、3 张条件边映射、`build_data_graph()` |
| `tests/graph/data/test_graph.py` | 17 条：节点数 / 边表 / 守卫键 / 可达性 / 空走终止 / span |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `state.py` · `ConversationState` | 没有 B 出口字段 | 加 `sku_id` / `sku_candidates` / `spec_candidates` / `data_result` / `tool_code` | 子图与主图共用 schema；不声明字段，节点写入会被 LangGraph 拒绝 |

### 三个设计点

**1. 5 状态 / 8 转移 / 3 个守卫。** 比 C 出口简单得多——没有判定、没有算钱、没有合规，
只有"抽参数 → 校验 → 查询 → 组装"。

**2. `query` 的默认分支是转人工（fail-closed）。** 没有 `tool_code`、或者码不在
`ok/not_found/invalid_arg` 里，一律走 human。骨架阶段空状态就是这样终止的。

**3. span 前缀是 `data:`。** 复用 Task 1 的 `traced(prefix, name, node)`，
和 C 出口的 `rules:` 区分开。

### 验证

- `uv run pytest tests/graph/data -q` → 17 passed
- `uv run pytest -q` → 654 passed, 42 skipped
- `uv run ruff check app tests` → All checks passed

---

## 4 · Task 4 · 抽参数与按意图校验（`c43e1d2`）

### 新增

| 文件 | 内容 |
|---|---|
| `tests/graph/data/test_extract_params.py` | 16 条：抽取 / 校验 / 图上的追问效果 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `data_exit.py` · `extract_params` | `_stub` 返回空 | 正则抽 `order_id` / `sku_id`（复用 `graph/params.py`） | 没有参数，后面的查询没有输入 |
| `data_exit.py` · `validate` | `_stub` 返回空 | 按意图写 `pending_question`；齐全时写 `None` | 守卫与 `need_info` 需要一个**单一来源**判断"缺什么" |
| `data_exit.py` · `build_data_graph` | 5 个节点全是 `_stub` | `real_nodes` 映射，逐个替换 | 与 C 出口相同的渐进替换方式 |

### 两个细节

**1. 两个节点都不查库。** 存在性、归属、规格匹配都在 `query` 的工具层——
那里才能同时覆盖"不存在"与"越权"，且错误码统一。

**2. 参数齐全时显式写 `None`。** 如果 state 里残留上一轮的 `pending_question`，
不写 `None` 会被守卫误判成"还在追问"。`None` 是"已清空"，不是"没写过"。

### 验证

- `uv run pytest tests/graph/data -q` → 33 passed
- `uv run pytest -q` → 670 passed, 42 skipped
- `uv run ruff check app tests` → All checks passed
