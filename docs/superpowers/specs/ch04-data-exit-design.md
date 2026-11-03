# Ch04 设计：B 出口（数据查询 5 状态流水线）

> 依据：[意图与出口设计](../../design/电商客服-意图与出口设计.md) §二/§三/§四、
> 技术决策 D7（身份注入）/ D9（四类错误码）/ A2（权限校验必须在确定性节点）、
> 参考实现 `query_order` / `query_logistics` / `query_product`。
> 范围：**订单查询 / 物流查询 / 库存价格** 三类意图。

---

## 目标

1. 三个意图各有一条 happy path：订单状态、物流轨迹、库存与价格
2. **B 出口内部零模型调用**：参数用正则 + 数据库匹配，回答用模板（见「为什么不用模型」）
3. 缺 `order_id` → 列最近订单让用户点选（复用 C 出口的候选逻辑）
4. **越权与不存在返回同一句话**，不泄露订单是否存在
5. 金额逐字等于数据库值，不由模型复述
6. 物流两级依赖固定：先订单拿 `tracking_no`，再查轨迹
7. 每个节点落一行 `trace_span`（`data:<node>`）
8. 失败一律明确告知，**不猜**：查不到说查不到，没有物流信息说没有

### 验收标准

1. 三类意图各一条端到端请求，答案字段完整
2. 「订单 1001 到哪了」走完 `订单 → tracking_no → 轨迹` 两步链
3. 无单号时返回 `order_candidates` + `actions.type=select_order`（不是文字追问）
4. 越权与不存在返回同一句「没有找到这笔订单」
5. `sold_out` / `presale` / `on_sale` 三种库存状态文案正确
6. 待发货订单查物流 → 明确「暂无物流信息」，不报错
7. 每个节点一行 span，共享 `trace_id`
8. B 出口模型调用次数 = 0（意图分类在上游，不算）

---

## 技术栈

沿用 Ch01–Ch03：LangGraph 子图、async SQLAlchemy、FastAPI。**本章不引入新依赖。**

---

## 为什么 B 出口不用模型

| 判据 | B 出口 | 结论 |
|---|---|---|
| 参数是什么 | 订单号（数字）/ SKU（`ST-20`）/ 规格（颜色） | 正则 + 数据库匹配足够 |
| 答案是什么 | 字段读数（状态、金额、轨迹、库存） | 模板输出更准 |
| 答错代价 | 报错金额/库存 = 可信度崩塌 | 不让模型复述数字 |
| 失败怎么办 | 查不到就明说，用户能自己核对单号 | 不需要语言理解 |

对比：

| 出口 | 模型做什么 | 为什么 |
|---|---|---|
| A 知识 | 组织语言 + 引用 | 政策是自然语言，答案需要解释 |
| **B 数据** | **只在上游做意图分类** | 参数与答案都是结构化的 |
| C 规则 | 抽要素 + 判资格 | 政策理解与责任判断需要模型 |

**代价**：措辞不标准时会退回追问（例如「我上周买的那个推车」没有单号 → 列候选）。
这是可接受的——比编一个错误答案便宜。

---

## 架构

```
主图 classify_intent ──route=data──► data 子图

  extract_params ─► validate ─┬─► query ─┬─► format ─► END
                              │          ├─► need_info ─► END
                              └─► need_info       └─► 转人工（工具异常）
```

**没有 judge / compute / compliance**：B 出口只读，不涉及资金与合规判定。

---

## 状态与转移

**5 状态 / 8 转移 / 2 个守卫函数**（`after_validate` 2 条分支、`after_query` 3 条分支）：

| # | 当前 | 条件 | 下一 | 类型 |
|---|---|---|---|---|
| 1 | extract_params | 抽取完成 | validate | 无条件 |
| 2 | validate | 参数齐全 | query | 守卫 |
| 3 | validate | 缺参数 / 参数不合法 | need_info | 守卫 |
| 4 | query | 查到且字段完整 | format | 守卫 |
| 5 | query | 订单查不到 / 规格不存在 | need_info | 守卫 |
| 6 | query | 工具超时 / 数据异常 | 转人工 | 守卫 |
| 7 | need_info | 已追问 | 结束 | 无条件 |
| 8 | format | 总是 | 结束 | 无条件 |

**第 7 条为什么无条件结束**：本轮只负责"把问题问出去"，用户回答后是**下一次图执行**，
从 `extract_params` 重新走。跨轮恢复靠 DB（`pending_question`），与 C 出口一致。

**「转人工」不是节点**：主图已有 `human` 出口，子图把转人工写成一条边到 `END`，
由触发它的节点先写好 `transfer_reason` / `answer` / `actions`。

---

## 节点名对照

| 节点 | 中文 | 职责 |
|---|---|---|
| `extract_params` | 抽参数 | 正则抽订单号 / SKU；规格用数据库匹配 |
| `validate` | 校验 | 按意图检查必填参数 |
| `query` | 查询 | 调工具；物流走两级链 |
| `need_info` | 补充信息 | 缺单号列候选；缺规格列可选规格 |
| `format` | 组装回答 | 模板输出，金额逐字来自数据库 |

---

## 各节点契约

### extract_params · 抽参数（代码）

| 项 | 内容 |
|---|---|
| 读 | `resolved_query` / `raw_query`、`intent` |
| 写 | `order_id`、`sku_id` |
| 逻辑 | ① 正则 `(?<!\d)(\d{4,})(?!\d)` 抽订单号；② 正则 `[A-Za-z]{2}-\d{2}` 抽 SKU（统一转大写）；③ 规格不做正则，交给 `query` 用数据库行匹配 |
| 失败 | 抽不到就是缺，交给 `validate` |

**为什么规格不在这里抽**：规格是开放集合（颜色、容量），正则枚举不完；
从数据库取出该 SKU 的所有规格，再用子串匹配用户原话，更可靠。

### validate · 校验（代码）

| 意图 | 必填 | 缺了怎么办 |
|---|---|---|
| 订单查询 | `order_id` | `need_info`（列候选） |
| 物流查询 | `order_id` | `need_info`（列候选） |
| 库存价格 | `sku_id` | `need_info`（列出可选 SKU） |

**`validate` 不查库**：是否存在、是否属于本人，交给 `query` 的工具层——
仓库层的 `NotFound` 同时覆盖"不存在"与"越权"，在这里分开查会泄露信息。

### query · 查询（代码）

按意图分派，**物流是固定两级链**：

```
订单查询：query_order(order_id, user_id)
物流查询：query_order(order_id, user_id) → tracking_no → query_logistics(tracking_no, user_id)
库存价格：query_stock(sku_id, spec)
```

| 结果 | 下一步 |
|---|---|
| `ok` 且字段完整 | `format` |
| `not_found`（订单/规格） | `need_info`（列候选 / 列规格） |
| `invalid_arg` | `need_info` |
| `timeout` / `internal` | 转人工（写 `transfer_reason`） |

**待发货订单没有 `tracking_no` 不是错误**：`format` 输出「暂无物流信息，当前状态：待发货」。

### need_info · 补充信息（代码）

| 缺什么 | 行为 |
|---|---|
| 订单号 | `list_user_orders` 取最近 3 单，写 `order_candidates` + `actions=[{type:select_order}]` |
| SKU | 从 `stock` 表取可选型号，写 `sku_candidates` + `actions=[{type:select_sku}]` |
| 规格 | 从该 SKU 的库存行取规格列表，写 `spec_candidates` + `actions=[{type:select_spec}]` |

**复用 C 出口的候选逻辑**：`_ask_order` / `_candidate` 抽到共享模块，
两处共用一份，避免"两个出口的话术不一样"。

### format · 组装回答（代码，模板）

**模板里所有数字都来自工具返回值，不做二次计算。**

| 意图 | 模板 |
|---|---|
| 订单 | `订单 {id}：状态 {status}；实付 {amount}；下单时间 {created}。[{tracking}]` |
| 物流 | `快递 {no}：最新状态 {latest}。{最近 3 条轨迹}` |
| 库存 | `{sku} {spec}：{货态}，售价 {price}。` |

**货态**：

| `stock.status` | 文案 |
|---|---|
| `on_sale` 且有货 | 「有货（N 件）」 |
| `on_sale` 且 0 件 | 「暂时无货」 |
| `sold_out` | 「已售罄」 |
| `presale` | 「预售中」 |

**物流轨迹**：`nodes` 是 JSON 数组，取最近 3 条；解析失败只显示 `latest_status`，
**不抛异常**（数据问题不该让整轮失败）。

---

## 状态字段（新增）

| 字段 | 写入者 | 用途 |
|---|---|---|
| `order_id` | `extract_params` | C 出口已有，B 复用 |
| `sku_id` | `extract_params` | 库存查询的型号 |
| `sku_candidates` | `need_info` | 缺型号时列可选 SKU |
| `spec_candidates` | `need_info` | 缺规格时列该 SKU 的可选规格 |
| `data_result` | `query` | 工具返回的结构化数据，供 `format` 使用 |
| `pending_question` | `need_info` | 已有字段，值扩展为 `order_id` / `sku_id` / `spec` |

`data_result` 存**工具返回的原始字段**（不做二次加工），`format` 只负责拼文案。
这样"数据"和"表达"分开，模板单测可以直接喂 `data_result`。

---

## 工具层（D7 / D9）

> **术语说明**：这里的 "tool" 指**图内固定调用的只读查询函数**，
> 不是"模型选择调用 → 代码执行 → 结果回灌"的 Agent tool calling。
> 本项目的 function calling 目前只用于**结构化输出**
> （`emit_intent` / `emit_refund_draft` / `emit_judgement`，见 `app/core/llm.py`）；
> 真正的工具循环留给 P5-9（E 出口只读 ReAct）。

新增 `app/tools/orders.py`，四个函数：

```python
async def query_order(order_id: str, *, user_id: str) -> ToolResult
async def query_logistics(tracking_no: str, *, user_id: str) -> ToolResult
async def query_stock(sku_id: str, spec: str | None = None) -> ToolResult
async def list_stock_skus() -> ToolResult
```

**订单候选不在这里重复提供**：`need_info` 复用 `order_select.ask_order`
（它已经封装了 `list_user_orders` 与候选形状），工具层只负责三类查询。

**为什么还要工具层，而不直接调 repo**：统一错误语义。
repo 抛 `NotFound`，工具层转成 `ToolResult(code=...)`，图里只做分支，不做异常处理。

```python
@dataclass(frozen=True)
class ToolResult:
    ok: bool
    code: str | None      # not_found / invalid_arg / timeout / internal
    data: dict | None
```

**需要给 `repo_order` 增加 `list_stock_by_sku(sku_id)`**：`get_stock` 要求同时给规格，
而"用户只报了型号"时需要先列出该型号的所有规格，再匹配原话。

**`session` 可注入**：工具函数加 `session: AsyncSession | None = None`（keyword-only），
测试传临时库的 session，生产不传。沿用 `session_scope()` 的约定。

**身份注入**：`user_id` 是 keyword-only 必填，由**图传入**，不是模型传入。
B 出口没有模型参与工具选择，所以不存在"模型伪造 user_id"的路径；
这条规则保留是为了与 D7 一致，也为将来若接 Agent 做兜底。

**越权与不存在**：仓库层已经返回同一个 `NotFound`，工具层统一映射成 `not_found`，
上层文案只有一句「没有找到这笔订单」。

---

## 结果校验（在 `after_query` 守卫里）

`query` 只负责拿到数据；`after_query` 在放行前检查必需字段，
缺字段按系统异常处理（转人工），不硬拼：

| 意图 | 必需字段 |
|---|---|
| 订单 | `order_id`、`status`、`amount`、`created_at` |
| 物流 | `tracking_no`、`latest_status` |
| 库存 | `sku_id`、`spec`、`stock`、`price`、`status` |

**为什么不在 `format` 里兜底**：`format` 的职责是拼文案；如果它还要判断"数据能不能用"，
模板测试就得覆盖两种失败路径。分开之后，`format` 的输入永远是完整的 `data_result`，
数据异常会被明确记成 `internal`，而不是拼出一句「None 元」。

---

## 共享代码抽取（对 Ch03 的小重构）

| 现在在哪 | 抽到哪 | 谁用 |
|---|---|---|
| `rules_exit.yuan_text` | `app/core/format.py` | B / C |
| `rules_exit._candidate` / `_ask_order` | `app/graph/order_select.py` | B / C |
| `rules_exit.extract_order_id` | `app/graph/params.py` | B / C |
| `rules_exit._traced` | `app/graph/tracing.py` | B / C（span 命名加前缀） |

**为什么现在抽**：B 出口要复用同样的"列候选"和"金额格式"，复制会立刻产生两份话术。
这是**纯搬移**，C 的行为不变，由现有测试兜住。

---

## 与数据的一致性

| 数据 | 用法 | 注意 |
|---|---|---|
| `orders.tracking_no` | 物流链的中间键 | NULL = 还没发货，不是错误 |
| `logistics.nodes` | JSON 文本 | 解析失败降级为只显示最新状态 |
| `stock.status` | 货态文案 | `sold_out` / `presale` / `on_sale` 三态 |
| `stock.price` | 售价（分） | 用 `yuan_text`，不手写除法 |
| `orders.amount` | 实付（分） | 与 Ch03 的金额格式一致 |

**库存是实时数据，不走检索**：意图文档已明确——库存价格走 B，走检索会被证据闸误拦。

---

## 测试与验证

| # | 测试 | 断言 |
|---|---|---|
| 1 | 参数抽取 | 订单号、SKU、大小写、无参数 |
| 2 | 按意图校验 | 三类意图各缺一参；不缺时通过 |
| 3 | 工具：订单 | 本人可查；他人/不存在同 `not_found` |
| 4 | 工具：物流 | 两级链拿到轨迹；`tracking_no` 为空时不报错 |
| 5 | 工具：库存 | 有货/售罄/预售；规格不存在 |
| 6 | 模板 | 三个模板逐字比对；金额 = 数据库值 |
| 7 | 结果校验 | 缺字段 → `internal` → 转人工 |
| 8 | 集成：happy path | 三类意图各一条，从 `extract_params` 到 `format` |
| 9 | 集成：缺单号 | `order_candidates` + `actions`，本轮结束 |
| 10 | 集成：工具超时 | 转人工，带 `transfer_reason` |
| 11 | 主图接线 | `data` 节点是子图；占位函数删除 |
| 12 | trace | 每个节点 `data:<node>` |

**测试用临时库**（沿用 `tests/conftest.py` 的 `session` 夹具），不碰 `data/app.db`。

---

## 本章不做

| 不做 | 原因 |
|---|---|
| 价保 / 订单变更 | 属 C 出口，Ch03 已明确只做退款 |
| 多轮追问（用户答"第一单"） | Ch05 的槽位填充；本章只支持用户回传订单号 |
| `interrupt` / park | 需要 checkpointer，P5-12 |
| 写操作（下单 / 改地址） | D10 要求确认 + 幂等，属后续章节 |
| ReAct 工具选择 | B 是固定链，不需要模型选工具；ReAct 留给 E 出口长尾（P5-9） |
| 商品名 → SKU 的模糊匹配 | 先要求用户给型号；模糊匹配要商品目录，数据没有 |

---

## 关键决策记录

| # | 决策 | 选择 | 放弃的方案 | 理由 |
|---|---|---|---|---|
| B1 | B 出口是否用模型 | **不用**（仅上游分类） | 模型抽参数 + 生成回答 | 参数结构化、答案是读数；模型只会增加延迟与误报风险 |
| B2 | 图结构 | 5 状态子图 | 单节点一把梭 | 参数/查询/组装分开，失败点可测 |
| B3 | 物流两级链 | 固定：订单 → tracking_no → 轨迹 | 让模型决定调哪个工具 | 只有一条正确路径，不值得上 Agent |
| B4 | 缺单号 | 列候选 + `actions` | 文字追问 | 用户记不住单号；点选顺带完成归属提示 |
| B5 | 错误语义 | 工具层统一 `ToolResult` | 图里 try/except | 分支与异常处理分离，图更好测 |
| B6 | 回答生成 | **模板** | 模型复述 | 金额/库存必须逐字正确；模板可单测 |
| B7 | 结果校验位置 | `format` 之前独立检查 | 在 `query` 里顺手判 | 数据异常要能被明确记成 `internal` |

---

## 与参考实现（MewHelp）的差异

| MewHelp | 本设计 | 理由 |
|---|---|---|
| Agent 自主选工具 + 生成回答 | 固定链 + 模板 | 数据查询只有一条正确路径；确定性更好测 |
| `query_order` 只查订单，物流另调 | 子图内固定两级链 | 用户问"到哪了"时不该让模型自己想到先取 tracking_no |
| 商品查询按名称随机生成 | 按 SKU + 规格查真实库存 | 数据落表，答案可对拍 |
| 工具错误返回 `NOT_OWNED` | 统一 `ToolResult(code=not_found)` | 与 D9 的错误语义一致，图里只做分支 |
