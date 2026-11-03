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
| 5 | 2026-11-03 | Task 5 · 缺参列候选 | `fa214d0` |
| 6 | 2026-11-03 | Task 6 · 查询链与结果校验 | `09b06ce` |
| 7 | 2026-11-04 | Task 7 · 模板化回答 | `bda6cae` |
| 8 | 2026-11-04 | Task 8 · 接主图并删除占位 | `eaf57f4` |
| 9 | 2026-11-04 | Task 9 · 验收 | `3cf6f1d` |

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

---

## 5 · Task 5 · 缺参列候选（`fa214d0`）

### 新增

| 文件 | 内容 |
|---|---|
| `tests/graph/data/test_need_info.py` | 7 条：三种候选、降级、图上的效果 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `data_exit.py` · `need_info` | `_stub` 返回空 | 按 `pending_question` 分三种追问 | 用户不知道要补什么，本轮只会得到一个空回答 |
| `data_exit.py` · `_ask_sku` | 无 | `list_stock_skus` 列型号 + `select_sku` | 型号是封闭集合，列出来比让用户猜格式可靠 |
| `data_exit.py` · `_ask_spec` | 无 | `query_stock(sku_id)` 列该型号规格 + `select_spec` | 规格因型号而异，只能查库后再列 |

### 三个设计点

**1. 三种追问对应三种 action。** `select_order` / `select_sku` / `select_spec`——
前端按 `type` 渲染不同选择器，不用解析文案。

**2. 查不到候选时退化成文字。** 库是空的、工具失败，都不应该让追问变成 500；
给一句「请提供型号（例如 ST-20）」，用户仍能继续。

**3. 订单候选复用 C 出口的 `ask_order`。** 同一套候选形状与话术，
不另写一份。

### 验证

- `uv run pytest tests/graph/data -q` → 40 passed
- `uv run pytest -q` → 677 passed, 42 skipped
- `uv run ruff check app tests` → All checks passed

---

## 6 · Task 6 · 查询链与结果校验（`09b06ce`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/graph/transfer.py` | `TRANSFER_TEXT` / `TRANSFER_ACTION` / `transfer(reason)`，B / C 共用 |
| `tests/graph/data/test_query.py` | 17 条：三类查询、规格匹配、失败分流 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `data_exit.py` · `query` | `_stub` 返回空 | 按意图分派；物流走两级链；库存先列规格再匹配 | 没有查询，`format` 没有数据可拼 |
| `data_exit.py` · 结果校验 | 计划放在 `after_query` 守卫 | 放在 `query` 内，缺字段置 `internal` + 写转人工载荷 | 守卫只能返回键，写不了话术；放在节点里能同时写码和载荷 |
| `rules_exit.py` · `_transfer` | C 出口私有 | 搬到 `graph/transfer.py`，B / C 共用 | B 也要转人工；从 C import 会形成反向依赖 |
| spec / plan | 结果校验写在守卫 | 改为 `query` 内 | 同上 |

### 三个设计点

**1. 规格匹配取最长命中。** 「我要星光灰」不能匹配成「灰」；
只有一个规格时用户不说也能确定；多规格且没命中 → 追问，不猜。

**2. 失败分流按"用户能不能补"分。**
`not_found` / `invalid_arg` → 追问；`timeout` / `internal` → 转人工。
追问的话术里能点名缺什么，用户补一轮就能继续。

**3. 无物流单号不是错误。** 待发货订单 `tracking_no` 为 NULL，
`data_result.tracking = None`，`format` 会说「暂无物流信息」，而不是 `not_found`。

### 验证

- `uv run pytest tests/graph/data -q` → 57 passed
- `uv run pytest -q` → 694 passed, 42 skipped
- `uv run ruff check app tests` → All checks passed

---

## 7 · Task 7 · 模板化回答（`bda6cae`）

### 新增

| 文件 | 内容 |
|---|---|
| `tests/graph/data/test_format.py` | 11 条：三个模板、四种货态、轨迹裁剪、兜底 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `data_exit.py` · `format` | `_stub` 返回空 | 按意图拼模板 | 流水线跑完没有面向用户的回答 |
| `data_exit.py` · `_time_text` / `_stock_text` / `_recent_nodes` | 无 | 时间格式化、货态映射、轨迹裁剪 | 模板里不能直接塞 ISO 时间和原始状态码 |

### 三个设计点

**1. 模板只读 `data_result`，不做二次计算。** 金额用 `yuan_text`，
库存数量直接来自工具，时间只做格式转换——保证"回答里的数字 = 数据库里的数字"。

**2. 四种货态。** `on_sale` 有货 / `on_sale` 零库存 / `sold_out` / `presale`，
分别对应「有货（N 件）」「暂时无货」「已售罄」「预售中」。

**3. 轨迹取最近 3 条、倒序。** `nodes` 是按时间正序存的（seed 如此），
展示时 `[-3:]` 再 `reversed`——用户关心的是最新的一条。

### 验证

- `uv run pytest tests/graph/data -q` → 68 passed
- `uv run pytest -q` → 705 passed, 42 skipped
- `uv run ruff check app tests` → All checks passed

---

## 8 · Task 8 · 接主图并删除占位（`eaf57f4`）

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `build.py` · `data` 节点 | `nodes.data_exit` 占位 | `build_data_graph()` 编译后子图 | 五个出口里最后一个占位，不接就跑不到真实查询 |
| `nodes.py` · `data_exit` / `PLACEHOLDER_TEXT` / `_placeholder` | 占位机制 | 全部删除 | 已无消费者；留着会让后人以为还有出口没实现 |
| `test_graph_smoke.py` · `offline` 夹具 | 只打桩分类/检索/生成 | 增加 `data_exit.list_stock_skus` 与 `order_select.list_user_orders` | 冒烟测试会用 `raw_query="测试"` 走所有意图；库存价格会触发真实数据库 |
| `test_graph_smoke.py` · `test_no_exit_emits_amounts` | 所有出口都断言"无数字" | 改名并**排除 data** | 数据回答本来就该包含价格 / 库存 / 单号；"无数字"只适用于文字类出口 |

### 两个设计点

**1. 占位机制整体删除，不是逐个删。** `PLACEHOLDER_TEXT` 为空时，
`_placeholder()` 已经没有意义；留着空表比删掉更容易误导。

**2. 端到端三条实测**

| 输入 | 路由 | 回答 |
|---|---|---|
| `订单1001 到哪了` | data / 物流 | 快递 SF1001001：最新状态 已签收。最近轨迹：… |
| `订单1002 什么状态` | data / 订单 | 订单 1002：状态 已发货；实付 1249.00 元；… |
| `ST-20 还有货吗` | data / 库存 | ST-20 石墨灰：有货（42 件），售价 1299.00 元。 |

### 验证

- `uv run pytest -q` → 697 passed, 42 skipped（减少了 8 条占位/数据出口断言）
- `uv run ruff check app tests` → All checks passed
- `scripts/try.py graph` 三条真实请求全部命中 `route=data`

---

## 9 · Task 9 · 验收（`3cf6f1d`）

### 新增

| 文件 | 内容 |
|---|---|
| `docs/reports/ch04-acceptance.md` | 验收清单、三条实测、边界场景、延迟、已知限制 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `order_select.py` · `ask_order` | 只有一种追问话术 | 加 `not_found` 参数，报错单号时先说「没有找到这笔订单」 | 验收要求"越权与不存在同一句"，原话术只列候选，用户不知道是号错了还是系统没查到 |
| `data_exit.py` · `need_info` | 直接 `ask_order(state)` | 按 `tool_code == "not_found"` 传入标记 | 同一函数要区分"用户没给号"与"给的号查不到" |
| `test_graph.py` | 无零模型守卫 | `test_data_exit_does_not_import_llm` 静态断言 | "B 出口零模型调用"是本章核心取舍，必须有守卫 |
| `00-tasks.md` · P4 | 5 项待做 | P4-1/2/4/5/6 完成；P4-3 建单仍待做 | 任务表要与实现一致 |

### 验收结果

| 项 | 结果 |
|---|---|
| pytest | **706 passed, 39 skipped** |
| 三条 happy path | ✅ 全部 `route=data` |
| 缺单号列候选 | ✅ 3 个候选 + `select_order` |
| 越权与不存在同一句 | ✅「没有找到这笔订单…」+ 同一批候选 |
| 四种货态 | ✅ 有货 / 暂时无货 / 已售罄 / 预售中 |
| 待发货查物流 | ✅「暂无物流信息」 |
| span | ✅ `data:extract_params/validate/query/need_info/format` |
| 零模型调用 | ✅ 模块不 import `app.core.llm` |
| 延迟 | median **0.98s**（n=5，预算 30s） |

### 验证

- `uv run pytest -q` → 706 passed, 39 skipped
- `uv run ruff check app tests` → All checks passed
- `scripts/try.py graph` 三条 happy path + 三条边界场景符合预期
