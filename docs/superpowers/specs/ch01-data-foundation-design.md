# Ch01 设计：数据地基（表结构 + seed + 越权校验）

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

### 系统表（6）

```
conversations ──1:N──▶ messages
      │
      ├──1:N──▶ tickets
      └──1:N──▶ low_conf_questions

trace_span        （独立，按 trace_id 索引）
faq               （独立，飞轮写回目标）
```

| 表 | 关键字段 | 说明 |
|---|---|---|
| conversations | id, user_id, deleted_at, summary, summary_upto_msg_id, **active_flow, pending_question, draft** | 后三个是 M4 的跨轮锚点 |
| messages | id, conversation_id, role, content, tool_calls, tool_call_id, **status**, **client_message_id**, meta | status=C1 三态；client_message_id=K4 幂等 |
| tickets | id, conversation_id, order_id, ticket_type, description, **request_id**, status | request_id 唯一，D11 |
| trace_span | trace_id, span_id, parent_span_id, name, start_at, end_at, attrs | L7 span 模型 |
| faq | id, question, answer, **source_type, source_question_id, reviewed_at, kb_version**, sync_status | K5 溯源 + K1 同步状态 |
| low_conf_questions | id, conversation_id, question, normalized, source, reason, **retrieved**, status, merged_into, occurrence | retrieved 三态：`[...]` / `[]` / NULL |

### 业务表（4，mock 数据落表而非硬编码）

| 表 | 关键字段 | 说明 |
|---|---|---|
| orders | id, **user_id**, status, amount, created_at, paid_at, shipped_at, tracking_no, sku_id, spec | 归属校验的载体 |
| logistics | id, tracking_no, order_id, nodes(JSON), latest_status | 物流节点 |
| stock | sku_id, spec, stock, price, status | 有货/无货/预售 |
| coupons | id, **user_id**, code, threshold, discount, expires_at, used | 门槛与过期 |

**为什么落表而不是写 mock 函数**：归属校验要能在 SQL 层做（`WHERE user_id = ?`），写在函数里会退化成"遍历列表判断"，和 S1 的设计冲突。

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
| 5 | 幂等索引 | 同一 `(conversation_id, client_message_id)` 插两次，第二次冲突 |
| 6 | seed 完整性 | 6/20/5 条；订单的 `user_id` 都能在 conversations 里找到 |

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
| 幂等索引 | 部分索引 `WHERE client_message_id IS NOT NULL` | 普通唯一索引：多条 `NULL` 在 SQLite 里视为不同，需部分索引才符合语义 |
| 外键 | 连接时 `PRAGMA foreign_keys=ON` | 默认关闭：DDL 声明也不拦，必须显式开 + 反向测试验证 |
| 表结构来源 | `schema.sql` 与 ORM 双写 | 只用 `create_all`：DDL 不可审阅；只用 SQL：ORM 类型对不上 |
| 时间字段 | `DATETIME` + 应用层写入 | SQLite 无原生时区类型：统一用 UTC 字符串 |
