# Ch01 面试要点：数据地基

> 证据来源：[ch01-data-foundation-changes.md](../reports/ch01-data-foundation-changes.md)、
> [ch01-data-foundation-design.md](../superpowers/specs/ch01-data-foundation-design.md)
> 状态：已完成（Ch03 又补了 `signed_at` / `quantity` / `category`）

---

## 0. 30 秒版本

> 「项目的第一章是数据地基：10 张表（6 张系统 + 4 张业务），
> 三件事做得比较刻意：
>
> 1. **schema.sql 和 ORM 双写 + 自动比对**，防止两边漂移；
> 2. **越权校验下沉到 SQL 层**，`user_id` 是 keyword-only 必填参数，漏传直接 TypeError；
> 3. **幂等用部分唯一索引**（`WHERE client_message_id IS NOT NULL`），
>    允许多条 NULL，只对带幂等键的消息去重。
>
> 这些不是为了炫技——每一条都对应一个具体的失败场景。」

---

## 1. 关键决策

### 1.1 为什么 schema.sql 和 ORM 各写一份

| 做法 | 好处 | 风险 |
|---|---|---|
| 只写 ORM | 单一真源 | DDL 藏在 Python 里，review 时看不出表长什么样 |
| 只写 schema.sql | 人一眼能看懂 | 运行时类型、索引定义还得用 ORM |
| **两边都写 + 自动比对** | 可读性与运行时都要 | 漂移 |

**防漂移**：`scripts/init_db.py` 把 `schema.sql` 在内存库执行一遍，
和 `Base.metadata` 的表 / 列 / 索引逐项比对，不一致直接退出码 1。

> **面试可讲**：「漂移是这对组合唯一的风险，所以我把'人肉比对'写成了脚本里的自动比对。
> 但**脚本本身是手动的**——没人跑就不会发现。所以我又加了一条 pytest
> （`tests/db/test_schema_sync.py`），让 CI 每次都跑。不然这个守卫只在'有人记得'时生效。」

### 1.2 PRAGMA 为什么挂在 `connect` 事件上

```python
@event.listens_for(engine.sync_engine, "connect")
def _set_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()
```

PRAGMA 是**连接级**设置。启动时执行一次，只对那一条连接生效；
连接池后续新建的连接会回到默认值（`foreign_keys=OFF`）——外键约束就形同注释。

> **面试可讲**：「测试也走 `make_engine()`，所以测的是真实的 PRAGMA 路径，不是另写一份配置。」

### 1.3 幂等：部分唯一索引

```sql
CREATE UNIQUE INDEX uk_msg_idem
ON messages(conversation_id, client_message_id)
WHERE client_message_id IS NOT NULL;
```

**先纠正一个常见误解**：SQLite / PostgreSQL / MySQL 的普通唯一索引**本来就允许多条 NULL**
（`NULL != NULL`）。实测：普通 `UNIQUE(cid, client_message_id)` 插入 3 条 NULL 全部成功，
第 2 条相同的 `m1` 才冲突。

所以部分索引的价值不是"让 NULL 能共存"，而是：

1. **语义写在 DDL 里**：只有带幂等键的消息参与去重，读 schema 一眼看懂；
2. **不依赖各库的 NULL 行为**：SQL Server 的唯一约束**只允许一个 NULL**，
   换库时普通索引会直接拒绝第二条 NULL；部分索引不受影响；
3. **索引更小**：NULL 行不进去。

> **面试可讲**：「用户重试时带同一个 `client_message_id`，第二次插入撞索引，直接返回第一条。
> 没用普通索引，是因为我想把'只对带键消息去重'写进 DDL，也不想依赖各数据库对 NULL 的不同处理。」

### 1.4 越权：`user_id` keyword-only + 无默认值

```python
async def get_order(order_id: str, *, user_id: str) -> Order:
    ...
    if row is None or row.user_id != user_id:
        raise NotFound("order not found")
```

- **keyword-only + 无默认值**：漏传直接 `TypeError`，不会静默查到别人的单；
- **不存在与无权限共用 `NotFound`**：分开返回会泄露"这单确实存在"，形成枚举 oracle。

> **面试可讲**：「安全不是靠调用方记得传参，是靠签名让漏传变成运行时错误。」

### 1.5 幂等插入为什么用 SAVEPOINT

```python
async with s.begin_nested():      # SAVEPOINT
    s.add(message)
    await s.flush()
```

撞唯一索引后如果 `session.rollback()`，回滚的是**整个事务**——
调用方可能已经在同一事务里写了别的表（K3 跨表事务），全被一起撤销。
`begin_nested()` 只回滚 SAVEPOINT。

> **面试可讲**：「这是'局部失败不该拖垮全局事务'的具体实现。」

### 1.6 seed 为什么用 DELETE 再插入，而不是 upsert

upsert 删不掉"上一次 seed 有、这次没有"的旧行，数据集会逐渐腐化。
业务表是纯种子数据，清空安全；`conversations` / `messages` 是运行时数据，不动。

**种子覆盖度是设计出来的**：

| 覆盖 | 为什么 |
|---|---|
| 6 种订单状态 | 规则分支每一类都要有样本 |
| 4 类物流形态 | B 出口要测异常、运输中、已签收 |
| 每人 3–5 单 | 列候选功能需要"多单"场景 |
| 至少 1 单超 7 天 | 退货期 / 价保都要有窗口外样本 |

---

## 2. 踩坑故事

### 2.1 `sqlalchemy` 装了但异步跑不起来

- **现象**：`ImportError: greenlet` / `MissingGreenlet`
- **原因**：async 驱动需要 `greenlet` 作为桥接，`sqlalchemy` 核心包不带
- **改法**：依赖写成 `sqlalchemy[asyncio]`

### 2.2 `expire_on_commit=False`

async 下 commit 后再读 ORM 对象属性会触发隐式懒加载，
没有 greenlet 上下文直接 `MissingGreenlet` 崩掉。

> 这不是优化项，是 aiosqlite + ORM 的必需项。

---

## 3. 追问清单

> 数据库基础概念（索引、事务、WAL、隔离级别、N+1、MissingGreenlet…）
> 见单独的 [database-qa.md](./database-qa.md)，本节只列项目相关的追问。

| 问题 | 答题要点 |
|---|---|
| 为什么两边都写 DDL？ | 可读性 vs 运行时真源；用自动比对防漂移 |
| 部分索引和普通唯一索引差在哪？ | 语义显式 + 不依赖各库 NULL 行为 + 索引更小（SQLite 普通索引也允许多 NULL） |
| 为什么 `user_id` 不给默认值？ | 默认值会让漏传静默通过，越权就藏起来了 |
| 为什么不存在和无权限同异常？ | 防枚举 oracle |
| 为什么用 WAL？ | 写事务不锁全库，读写可并发 |
| 为什么 seed 要可重复执行？ | 每次评测/演示前要回到同一初始态 |
| 种子数据为什么是 20 单？ | 覆盖 6 状态 × 5 用户 × 部分退款/超期等边界 |
| 事务边界怎么定的？ | 一次请求一个 session；跨表写用传入的同一个 session |
| 怎么发现表结构漂移？ | `init_db` 的 schema.sql ↔ ORM 比对，CI 可跑 |
| Ch03 加了什么？ | `signed_at`（7 天窗口）、`quantity`（部分退款）、`category`（不适用商品） |

---

## 4. 不要说的话

| 别说 | 为什么 |
|---|---|
| 「设计了 10 张表」 | 表多不等于设计好；要讲每张表解决什么 |
| 「用了 SQLAlchemy」 | 框架名没有信息量；要讲 async / SAVEPOINT / 部分索引 |
| 「测试覆盖率很高」 | 没测过覆盖率；讲具体守住了哪个失败场景 |

---

## 5. 数字速查

| 数字 | 含义 | 出处 |
|---|---|---|
| 10 | 表数量（6 系统 + 4 业务） | `app/db/models.py` |
| 7 | 索引数量 | `scripts/init_db.py` 输出 |
| 6 / 20 / 5 | SKU / 订单 / 券 | `scripts/seed.py` |
| 5 用户 3–5 单 | 归属与候选列表的样本分布 | `seed.py` 注释 |
| 1 单超 7 天 | 退货窗口边界 | `tests/db/test_seed.py` |
