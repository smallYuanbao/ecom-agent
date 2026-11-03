# Ch01 设计：数据地基（表结构 + seed + 越权校验）

> 实施计划：[ch01-data-foundation.md](../plans/ch01-data-foundation.md)

## 目标

跑通"数据能存、能查、查不错别人的"这条流。**不做检索、不做状态机、不调模型、不做前端。**

验收标准：

1. 10 张表建好（6 张系统表 + 4 张业务表），两个唯一索引生效。
2. 跑 `scripts/seed.py` 后能查到：6 个 SKU、20 笔订单、5 张券，且订单归属正确。
3. `get_order(order_id="1002", user_id="u2")` 抛 `NotFound`（不是自己的单）；不带 `user_id` 时直接 `TypeError`。
4. `pytest` 全绿，覆盖：外键、唯一索引、必填参数、越权。

## 技术栈（定死，不可自行更换）

- Python 3.11+ + FastAPI + SQLAlchemy 2.x（async）+ SQLite（aiosqlite）
- 依赖管理：uv
- 测试：pytest + pytest-asyncio
- **本章不需要任何模型密钥**（不调 LLM）

## 架构：单进程

一条命令起 FastAPI（:8000），SQLite 单文件 `data/app.db`，SQLAlchemy 管理 ORM。

本章数据流只有一条：`init_db.py` 建表 → `seed.py` 灌数据 → SQL / ORM 查询验证。业务链路从 Ch03 开始。

## 目录结构

```
app/
  main.py                # FastAPI 入口（/api/health、/api/meta 已存在）
  config.py              # pydantic-settings（已存在）
  db/
    session.py           # engine + async_session 工厂
    base.py              # DeclarativeBase
    models.py            # 10 张表 ORM
    repo_conversation.py # 会话读写（active_flow 三字段）
    repo_message.py      # 消息读写（status、client_message_id）
    repo_order.py        # 订单/物流/库存/券（user_id 必填）
scripts/
  init_db.py             # 建表（执行 schema.sql 或 create_all）
  seed.py                # 6 SKU / 20 订单 / 5 券
tests/
  test_models.py
  test_repo.py
data/app.db              # gitignore
```

## 数据模型（10 张表）

### 关系

```
conversations ──1:N──▶ messages
      │
      ├──1:N──▶ tickets
      └──1:N──▶ low_conf_questions

orders ──1:N──▶ logistics

trace_span      （独立，按 trace_id 聚成调用树）
faq             （独立，飞轮写回目标）
stock / coupons （独立业务表）
```

### 通用约定

| 约定 | 说明 |
|---|---|
| 时间 | 一律 `DATETIME`，应用层写 UTC |
| JSON | SQLite 无原生 JSON 类型，以 `TEXT` 存序列化字符串 |
| 金额 | 单位为**分**（整数），展示时除以 100 |
| 软删 | 仅 `conversations` 有 `deleted_at`；`messages` 随会话隐藏，不单独删 |

---

### 1. conversations（会话）

**用途**：一次对话的容器，同时是跨轮状态的锚点（LangGraph 的 `thread_id` 即 `id`）。

| 字段 | 类型 | 约束 | 含义 | 依据 |
|---|---|---|---|---|
| `id` | INTEGER | PK 自增 | 会话 id，即图的 `thread_id` | D5 |
| `user_id` | TEXT | NOT NULL | 所属用户；会话归属校验的依据 | S2 |
| `created_at` | DATETIME | NOT NULL | 创建时间 | — |
| `updated_at` | DATETIME | NOT NULL | 最后活动时间；乐观锁与超时清理用 | C4 / M7 |
| `deleted_at` | DATETIME | NULL | 软删时间；非空即视为已删除 | K5 |
| `summary` | TEXT | NULL | 早期对话的滚动摘要 | M2 |
| `summary_upto_msg_id` | INTEGER | NULL | 摘要覆盖到哪条消息（`messages.id`），窗口起点 | M2 |
| `active_flow` | TEXT | NULL | 进行中的流程：`refund` / `price_protection` / NULL | M4 |
| `pending_question` | TEXT | NULL | 正在等用户补充什么：`order_id` / `confirm` / NULL | M4 |
| `draft` | TEXT(JSON) | NULL | 已收集的要素，如 `{"reason":"尺寸不合适"}` | M4 |

---

### 2. messages（消息）

**用途**：完整对话记录——回放、审计、窗口构建的数据源。

| 字段 | 类型 | 约束 | 含义 | 依据 |
|---|---|---|---|---|
| `id` | INTEGER | PK 自增 | 消息 id；窗口/摘要锚点写作 `db-{id}` | M1 |
| `conversation_id` | INTEGER | NOT NULL, FK→conversations.id | 所属会话 | M1 |
| `role` | TEXT | NOT NULL | `user` / `assistant` / `tool` | M1 |
| `content` | TEXT | NULL | 文本内容；`tool` 消息可为空 | M1 |
| `tool_calls` | TEXT(JSON) | NULL | assistant 发起的工具调用数组 | 回放 |
| `tool_call_id` | TEXT | NULL | `tool` 消息对应哪一次调用 | 回放 |
| `status` | TEXT | NULL | 助手消息状态：`completed` / `cancelled` / `error` | C1 |
| `client_message_id` | TEXT | NULL | 客户端生成的幂等键，同一条消息重试时复用 | K4 |
| `meta` | TEXT(JSON) | NULL | 附注：`intent` / `route` / `citation_ids` | L1 |
| `created_at` | DATETIME | NOT NULL | 写入时间 | M1 |

**索引**

| 名称 | 定义 | 用途 |
|---|---|---|
| `idx_msg_conv` | `(conversation_id, created_at)` | 按会话拉历史 |
| `uk_msg_idem` | `(conversation_id, client_message_id)` **WHERE client_message_id IS NOT NULL** | 幂等去重 |

**为什么用部分索引**：SQLite 里多个 `NULL` 不算冲突，但语义上"没有幂等键"本就不该被约束；加 `WHERE` 后只对真正带键的消息去重。

---

### 3. tickets（工单）

**用途**：转人工时创建；一次请求只建一张（`request_id` 保证）。

| 字段 | 类型 | 约束 | 含义 | 依据 |
|---|---|---|---|---|
| `id` | INTEGER | PK 自增 | 工单 id | — |
| `conversation_id` | INTEGER | NOT NULL | 来自哪个会话 | D 出口 |
| `order_id` | TEXT | NULL | 关联订单（可空，咨询类没有单号） | D 出口 |
| `ticket_type` | TEXT | NULL | 投诉 / 退款 / 咨询 … | D 出口 |
| `description` | TEXT | NULL | 用户原话或摘要 | D 出口 |
| `request_id` | TEXT | **NOT NULL, UNIQUE** | 幂等键，重试不重复建单 | D11 |
| `status` | TEXT | NOT NULL, 默认 `open` | open / processing / closed | — |
| `created_at` | DATETIME | NOT NULL | 创建时间 | — |

---

### 4. trace_span（调用链）

**用途**：一次请求内每个节点/工具的执行记录，用于排障与回放；结构对齐 OpenTelemetry 的 span 模型。

| 字段 | 类型 | 约束 | 含义 | 依据 |
|---|---|---|---|---|
| `id` | INTEGER | PK 自增 | 行 id | — |
| `trace_id` | TEXT | NOT NULL | 一次请求的 id，与日志里的 `trace_id` 一致 | L7 |
| `span_id` | TEXT | NOT NULL | 单个节点/工具调用的 id | L7 |
| `parent_span_id` | TEXT | NULL | 父 span，用于还原调用树 | L7 |
| `name` | TEXT | NOT NULL | 如 `node:compute` / `tool:query_order` | L7 |
| `start_at` | DATETIME | NOT NULL | 开始时间 | L7 |
| `end_at` | DATETIME | NULL | 结束时间；NULL 表示仍在执行或异常中断 | C1 |
| `attrs` | TEXT(JSON) | NULL | 附加信息：tokens / 结果摘要 / 错误码 | L7 |

**索引**：`idx_span_trace(trace_id)`。

---

### 5. faq（飞轮写回的知识）

**用途**：审核通过的低置信问题沉淀成的问答条目；写回后进入检索范围。

| 字段 | 类型 | 约束 | 含义 | 依据 |
|---|---|---|---|---|
| `id` | INTEGER | PK 自增 | 条目 id | — |
| `question` | TEXT | NOT NULL | 标准问题 | 飞轮 |
| `answer` | TEXT | NOT NULL | 审核后的答案 | 飞轮 |
| `source_type` | TEXT | NOT NULL | `flywheel`（飞轮写的）/ `manual`（手工录的） | K5 |
| `source_question_id` | INTEGER | NULL | 来自哪条低置信问题（`low_conf_questions.id`） | K5 |
| `reviewed_at` | DATETIME | NULL | 审核时间；NULL 表示未经人工确认 | K5 |
| `kb_version` | TEXT | NULL | 写入时的知识库版本哈希 | K6 |
| `sync_status` | TEXT | NOT NULL, 默认 `pending` | 向量库同步状态：`pending` / `synced` | K1 |
| `created_at` | DATETIME | NOT NULL | 创建时间 | — |

---

### 6. low_conf_questions（低置信池）

**用途**：飞轮的入口——记录"系统答不好"的问题与当时的证据。

| 字段 | 类型 | 约束 | 含义 | 依据 |
|---|---|---|---|---|
| `id` | INTEGER | PK 自增 | 条目 id | — |
| `conversation_id` | INTEGER | NULL | 来自哪个会话（回放用） | 飞轮 |
| `question` | TEXT | NOT NULL | 用户原话 | 飞轮 |
| `normalized` | TEXT | NULL | 归一化后的标准问题（合并同类用） | 飞轮 |
| `source` | TEXT | NULL | 入口：`retrieval_low_conf` / `feedback` / `rule_conflict` | 意图 §七 |
| `reason` | TEXT | NULL | 为什么判为低置信（分数、触发的守卫） | 意图 §七 |
| `retrieved` | TEXT(JSON) | NULL | **三态**：`[...]` 有召回但分低 / `[]` 零命中 / NULL 没走检索 | 意图 §七 |
| `status` | TEXT | NOT NULL, 默认 `pending` | pending / merged / approved / rejected / written_back | 飞轮 |
| `merged_into` | INTEGER | NULL | 合并到哪一条（同义问题去重） | 飞轮 |
| `occurrence` | INTEGER | NOT NULL, 默认 1 | 出现次数，合并时累加 | 飞轮 |
| `kb_ref` | TEXT | NULL | 写回后的 FAQ 条目 id | K5 |
| `created_at` | DATETIME | NOT NULL | 落池时间 | — |
| `reviewed_at` | DATETIME | NULL | 审核时间；驳回即终审（不复活） | 飞轮 |

**`retrieved` 三态的意义**：`[]` = 知识库真缺内容（优先补），`[...]` = 召回质量差（调阈值或改检索），`NULL` = 这条问题根本没走检索（不是知识缺口）。

---

### 7. orders（订单）

**用途**：归属校验的载体；C 出口计算退费的输入。

| 字段 | 类型 | 约束 | 含义 | 依据 |
|---|---|---|---|---|
| `id` | TEXT | PK | 订单号（如 `1002`），业务主键 | — |
| `user_id` | TEXT | NOT NULL | 归属人；越权校验的核心字段 | S1 |
| `sku_id` | TEXT | NOT NULL | 商品型号（如 `ST-60`） | 数据设计 §四 |
| `spec` | TEXT | NULL | 规格（如 `标准装`） | 数据设计 §四 |
| `status` | TEXT | NOT NULL | 待付款 / 待发货 / 已发货 / 已签收 / 退款中 / 已关闭（6 种，见数据设计 §五） | 规则依赖 |
| `amount` | INTEGER | NOT NULL | 实付金额（分） | 退费计算基数 |
| `created_at` | DATETIME | NOT NULL | 下单时间；**价保**从这天起算 | 规则 §六 |
| `paid_at` | DATETIME | NULL | 支付时间 | 规则 |
| `shipped_at` | DATETIME | NULL | 发货时间；决定能否改地址/取消 | 规则 |
| `tracking_no` | TEXT | NULL | 物流单号，关联 `logistics` | B 出口 |

**索引**：`idx_order_user(user_id)`。

> **Ch03 增补**：`signed_at`（签收时间，7 天窗口起算）、`quantity`（件数，部分退款）、
> `category`（商品类别，第六条/第七条判定）。字段定义见
> [ch03-rules-exit-design.md](ch03-rules-exit-design.md) 的 Task 1 / Task 8。

---

### 8. logistics（物流）

**用途**：B 出口查询物流轨迹。

| 字段 | 类型 | 约束 | 含义 |
|---|---|---|---|
| `id` | INTEGER | PK 自增 | 行 id |
| `tracking_no` | TEXT | NOT NULL | 物流单号 |
| `order_id` | TEXT | NOT NULL, FK→orders.id | 关联订单 |
| `latest_status` | TEXT | NOT NULL | 最新状态（运输中 / 已签收 / 异常） |
| `nodes` | TEXT(JSON) | NULL | 节点数组：`[{time, status, desc}]` |
| `updated_at` | DATETIME | NULL | 最后更新时间 |

**索引**：`idx_logistics_no(tracking_no)`。

---

### 9. stock（库存与价格）

**用途**：B 出口答"有没有货""多少钱"。

| 字段 | 类型 | 约束 | 含义 |
|---|---|---|---|
| `id` | INTEGER | PK 自增 | 行 id |
| `sku_id` | TEXT | NOT NULL | 商品型号 |
| `spec` | TEXT | NOT NULL | 规格 |
| `stock` | INTEGER | NOT NULL, 默认 0 | 可售库存数量 |
| `price` | INTEGER | NOT NULL | 当前售价（分） |
| `status` | TEXT | NOT NULL, 默认 `on_sale` | on_sale / sold_out / presale |

**唯一约束**：`UNIQUE(sku_id, spec)`——一个型号+规格只有一条库存记录。

---

### 10. coupons（优惠券）

**用途**：A 出口回答券规则；规则判断门槛与过期。

| 字段 | 类型 | 约束 | 含义 |
|---|---|---|---|
| `id` | INTEGER | PK 自增 | 行 id |
| `user_id` | TEXT | NOT NULL | 持券人 |
| `code` | TEXT | NOT NULL | 券码 |
| `threshold` | INTEGER | NOT NULL, 默认 0 | 使用门槛（分），0 表示无门槛 |
| `discount` | INTEGER | NOT NULL | 优惠金额（分） |
| `expires_at` | DATETIME | NOT NULL | 过期时间 |
| `used` | INTEGER | NOT NULL, 默认 0 | 0 未使用 / 1 已使用 |

**索引**：`idx_coupon_user(user_id)`。

---

**为什么业务数据落表而不是写 mock 函数**：归属校验要能在 SQL 层做（`WHERE user_id = ?`），写在函数里会退化成"遍历列表判断"，和 S1 的设计冲突。

## 核心逻辑：越权校验下沉到数据层

```python
async def get_order(order_id: str, *, user_id: str) -> Order:
    """user_id 是必填关键字参数——不传直接 TypeError。"""
    row = await session.get(Order, order_id)
    if row is None or row.user_id != user_id:
        raise NotFound()          # 不区分"不存在"与"不是你的"（S1/S2）
    return row
```

**约束**：

- 所有查询函数的 `user_id` 必须是 **keyword-only + 无默认值**
- 越权与不存在 **抛同一个异常**

## 接口契约

本章不新增业务接口，仅沿用已存在的：

### GET /api/health
```json
{"status": "ok"}
```

### GET /api/meta
```json
{"version": "0.1.0", "env": "dev", "chat_model": "...", "prompt_version": "v1",
 "kb_version": "", "chat_api_key": "<set>", "langfuse": "off"}
```

## 测试与验证

**TDD（先红后绿）**：

| # | 测试 | 断言 |
|---|---|---|
| 1 | 建表 | 10 张表存在；`uk_msg_idem` / `uk_ticket_idem` 生效 |
| 2 | 外键 | 插入 `conversation_id=999` 的 message 抛 `IntegrityError`（需 `PRAGMA foreign_keys=ON`） |
| 3 | 必填参数 | `get_order("1002")` 抛 `TypeError` |
| 4 | 越权 | `get_order("1002", user_id="u2")` 抛 `NotFound` |
| 5 | 幂等索引 | 同一 `(conversation_id, client_message_id)` 插两次：库里仍是 1 行（`append_message` 返回首次的 id） |
| 6 | seed 完整性 | 6/20/5 条；6 种订单状态全覆盖；每人 3–5 单；订单号首位与归属一致（`1001`→`u1`）；至少 1 单超 7 天；券含已过期 / 已使用 / 未达门槛 |

**手工验证**：

```bash
uv run python scripts/init_db.py
uv run python scripts/seed.py
sqlite3 data/app.db "select count(*) from orders;"        # 20
sqlite3 data/app.db "select user_id, count(*) from orders group by 1;"
```

## 本章不做

- 检索与知识库（Ch02）
- 状态机与 LLM 调用（Ch03）
- 消息窗口/摘要（Ch05）
- 安全中间件、prompt 结构（Ch06）
- 评测集（Ch07）
- 前端（Ch08）
- **任何模型调用**（本章零密钥）

## 关键决策记录

| 决策 | 选择 | 备选与理由 |
|---|---|---|
| 数据库 | SQLite（aiosqlite） | Postgres：数据量几十条，零运维；需要并发写再换 |
| ORM | SQLAlchemy 2.x async | 裸 sqlite3：与 EvalFlow 一致，加字段省心 |
| 业务数据 | **落表**，不用 mock 函数 | mock 函数（参考实现做法）：归属校验要能在 SQL 层做，函数里判断会退化 |
| 越权返回 | 与"不存在"同异常 | 分开返回：会泄露"这个订单存在"（枚举 oracle） |
| `user_id` 参数 | keyword-only、无默认值 | 可选参数：漏传时会静默放行 |
| 幂等索引 | 部分索引 `WHERE client_message_id IS NOT NULL` | 普通唯一索引：**SQLite/PG/MySQL 也允许多 NULL**，但语义不显式，且 SQL Server 只允许一个 NULL；部分索引把"只对带键消息去重"写进 DDL |
| 外键 | 连接时 `PRAGMA foreign_keys=ON` | 默认关闭：DDL 声明也不拦，必须显式开 + 反向测试验证 |
| 表结构来源 | `schema.sql` 与 ORM 双写 | 只用 `create_all`：DDL 不可审阅；只用 SQL：ORM 类型对不上 |
| 双写一致性 | `init_db.py` 自动比对表/列/索引 | 人工比对：会漏，且漏了没人发现 |
| 幂等重复 | `append_message` **返回首次 id** | 抛唯一约束：客户端重试会变成 500，而重试不是错误 |
| 幂等竞态 | 插入包 SAVEPOINT，冲突时只回滚这一步 | `session.rollback()`：会连累调用方的跨表事务（K3） |
| `set_flow` 归属 | 不在本函数校验，由调用方先 `get_conversation` | 加 `user_id` 参数：与 `get_conversation` 重复，且容易误以为它可以独立使用 |
| seed 覆盖检查 | 订单号首位 = 归属人（`1001`→`u1`） | "订单 user_id 能在 conversations 找到"：`conversations` 由运行时创建，seed 时为空，这条断言不成立 |
| 时间字段 | `DATETIME` + 应用层写入 | SQLite 无原生时区类型：统一用 UTC 字符串 |
