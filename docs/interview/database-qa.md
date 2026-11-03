# 数据库面试问答（面向前端转 AI 应用）

> 用法：每题先看「一句话」，能说清就过；说不清再看「展开」和「本项目」。
> 所有例子都能在 `app/db/` 和 `scripts/` 里找到对应代码。
> 配套：[ch01-data-foundation.md](./ch01-data-foundation.md)

---

## 一、索引

### Q1. 索引是什么？为什么能加速？

**一句话**：索引是**额外的、排好序的数据结构**，让数据库不用逐行扫描。

**展开**：
- 没有索引 → 全表扫描（`SCAN`），行数越多越慢；
- 有索引 → 按索引定位（`SEARCH ... USING INDEX`），接近 `O(log n)`；
- 代价：占空间；每次插入/更新都要维护索引。

**本项目**：`idx_order_user(user_id)`、`idx_msg_conv(conversation_id, created_at)`。

**追问**：「索引越多越好吗？」→ 不是。写多读少的表加索引会拖慢写入。

---

### Q2. 为什么数据库索引用 B-tree，不用哈希表？

**一句话**：B-tree 支持**范围查询和排序**，哈希只支持等值查询。

**展开**：

| 查询 | B-tree | 哈希 |
|---|---|---|
| `WHERE id = 1` | ✅ | ✅ |
| `WHERE created_at > '2026-01-01'` | ✅ | ❌ 全表扫 |
| `ORDER BY created_at` | ✅ 索引本身有序 | ❌ 还要排序 |

**本项目**：消息列表按 `created_at` 排序，联合索引 `(conversation_id, created_at)` 正好覆盖。

---

### Q3. 联合索引的「最左前缀」是什么？

**一句话**：索引 `(a, b)` 能服务 `WHERE a` 和 `WHERE a AND b`，**单独 `WHERE b` 用不上**。

**展开**：索引先按 `a` 排序，`a` 相同再按 `b` 排序。没有 `a` 就无法定位。

```sql
CREATE INDEX idx ON messages(conversation_id, created_at);

-- ✅ 用得上
WHERE conversation_id = 1
WHERE conversation_id = 1 AND created_at > '...'
WHERE conversation_id = 1 ORDER BY created_at

-- ❌ 用不上（只能全表扫）
WHERE created_at > '...'
```

**追问**：「把顺序反过来行吗？」→ 行，但要先想清楚**最常见的前缀查询**是什么。

---

### Q4. 什么是覆盖索引？

**一句话**：查询需要的列**全在索引里**，不用回表读整行。

```sql
-- 索引 (conversation_id, created_at)
SELECT created_at FROM messages WHERE conversation_id = 1;
-- 只读索引就够，不用回表
```

**追问**：「怎么确认？」→ `EXPLAIN QUERY PLAN`，看是否出现 `COVERING INDEX`。

---

### Q5. 唯一索引和普通索引的区别？

**一句话**：唯一索引多了一条约束——**这些列的组合不能重复**。

| | 普通索引 | 唯一索引 |
|---|---|---|
| 加速查询 | ✅ | ✅ |
| 阻止重复 | ❌ | ✅ |
| 插入时检查 | 不检查 | 冲突则报错 |

**本项目**：`uk_msg_idem` 是唯一索引，用来做消息幂等。

---

### Q6. 部分索引（partial index）是什么？

**一句话**：带 `WHERE` 的索引，**只对满足条件的行**建索引、做约束。

```sql
CREATE UNIQUE INDEX uk_msg_idem
  ON messages(conversation_id, client_message_id)
  WHERE client_message_id IS NOT NULL;
```

**典型场景**：
- 软删除的唯一邮箱：`WHERE deleted_at IS NULL`
- 一个用户只能有一个进行中订单：`WHERE status = '进行中'`
- 消息幂等：只对带幂等键的消息去重

**注意**（容易说错）：
- SQLite / PostgreSQL / MySQL 的**普通唯一索引本来就允许多条 NULL**（`NULL != NULL`）；
- 用部分索引不是为了"让 NULL 能共存"，而是为了**语义显式**、**不依赖各库 NULL 行为**、**索引更小**；
- MySQL **不支持**部分索引；SQL Server 叫 filtered index。

---

## 二、键与约束

### Q7. 主键用自增 ID 还是业务主键？

**一句话**：看业务键是否**稳定且唯一**；稳定就用业务键，否则用自增。

| | 自增 ID | 业务主键（如订单号） |
|---|---|---|
| 优点 | 短、稳定、写入快 | 天然唯一，查询不用再关联 |
| 缺点 | 多一列，外部系统要映射 | 业务键可能变更/较长 |

**本项目**：`orders.id` 直接用订单号（`TEXT`）。因为订单号本身稳定且唯一，
查订单时不需要"先查自增 ID 再查业务号"。

**追问**：「订单号会变吗？」→ 正常不会；如果会，就不该做主键。

---

### Q8. 外键有什么用？为什么 SQLite 默认关闭？

**一句话**：外键保证**引用完整性**（不能插入指向不存在订单的物流记录）。

**为什么默认关**：SQLite 为了向后兼容，默认 `foreign_keys=OFF`——
建表时写的 `REFERENCES` 只是元数据，**不生效**。

**本项目**：

```python
@event.listens_for(engine.sync_engine, "connect")
def _set_pragmas(dbapi_conn, _record):
    cur.execute("PRAGMA foreign_keys=ON")
```

**追问**：「为什么挂在 connect 事件？」→ PRAGMA 是**连接级**设置，
连接池后续新建的连接也要重新设置。

---

## 三、事务

### Q9. 事务是什么？ACID 分别指什么？

**一句话**：一组操作**要么全成功，要么全回滚**。

| 字母 | 含义 | 例子 |
|---|---|---|
| **A** 原子性 | 不可分割 | 转账：扣款和入账一起成功/失败 |
| **C** 一致性 | 约束始终成立 | 外键、唯一索引不被破坏 |
| **I** 隔离性 | 并发事务互不干扰 | 两个用户同时下单不串数据 |
| **D** 持久性 | 提交后不丢 | 断电后已提交的数据还在 |

**本项目**：`session_scope()` 没传 session 时自动 `begin` + commit/rollback；
传了 session 时事务归调用方（跨表事务）。

---

### Q10. 隔离级别有哪些？脏读/不可重复读/幻读是什么？

**一句话**：隔离级别越高，并发问题越少，但性能越差。

| 级别 | 脏读 | 不可重复读 | 幻读 |
|---|---|---|---|
| 读未提交 | ✅ 可能 | ✅ | ✅ |
| 读已提交 | ❌ | ✅ | ✅ |
| 可重复读 | ❌ | ❌ | ✅（MySQL 用间隙锁缓解） |
| 串行化 | ❌ | ❌ | ❌ |

- **脏读**：读到别人**没提交**的数据
- **不可重复读**：同一行，两次读结果不同（别人改了）
- **幻读**：同一条件，两次查**行数**不同（别人插了）

**本项目**：SQLite 在 WAL 下读者看到一致快照，写是串行的；
不需要在应用层处理隔离级别。

---

### Q11. SAVEPOINT 是什么？为什么幂等插入要用它？

**一句话**：事务里的**子事务**，可以只回滚一部分，不影响外层。

**问题**：撞唯一索引后如果 `session.rollback()`，会把**整个事务**回滚——
调用方可能已经在同一事务里写了别的表，全被撤销。

**本项目**：

```python
async with s.begin_nested():      # SAVEPOINT
    s.add(message)
    await s.flush()
```

冲突只回滚这个 SAVEPOINT，外层事务继续。

**追问**：「为什么不直接查一下再插入？」→ 查和插之间有竞态；
唯一索引是数据库层的原子保证。

---

### Q12. 幂等怎么实现？

**一句话**：**唯一索引 + 捕获冲突 + 返回第一次的结果**。

**本项目**（`append_message`）：
1. 客户端带 `client_message_id`；
2. 插入撞 `uk_msg_idem`；
3. 捕获 `IntegrityError`，按幂等键查回第一条；
4. 返回首次的 id，**不报错**（重试不是错误）。

**追问**：「不传幂等键呢？」→ 允许重复，因为部分索引只约束非 NULL 行。

---

## 四、SQLite 特性

### Q13. SQLite 和外部的 MySQL/PostgreSQL 有什么不同？

**一句话**：SQLite 是**嵌入式**数据库，没有服务进程，整个库就是一个文件。

| | SQLite | MySQL / PG |
|---|---|---|
| 部署 | 一个 `.db` 文件 | 独立服务 |
| 并发 | 单写者 | 多写者 |
| 适用 | 单机、演示、测试 | 多用户、高并发 |

**本项目**：演示项目，20 单数据，SQLite 足够；省掉运维。

---

### Q14. WAL 是什么？为什么用它？

**一句话**：Write-Ahead Log，写操作先追加到日志文件，**读不阻塞写、写不阻塞读**。

**没有 WAL**（默认 `journal_mode=delete`）：写事务期间锁住整个库，读写不能并发。

**有 WAL**：
- 读者看快照，不会被写者挡住；
- 写者往 `-wal` 文件追加；
- 仍然**只有一个写者**；
- 会产生 `-wal` / `-shm` 文件，需要 checkpoint 合并。

**本项目**：`PRAGMA journal_mode=WAL`。

**追问**：「WAL 有什么代价？」→ 多两个文件；不适合网络文件系统；写并发仍受限于单写者。

---

### Q15. PRAGMA 是什么？

**一句话**：SQLite 的**运行时开关**，比如开外键、设日志模式。

**本项目**：

```sql
PRAGMA foreign_keys=ON;      -- 每个连接都要设
PRAGMA journal_mode=WAL;     -- 会写进库文件，但也在 connect 时设一次
```

**注意**：`foreign_keys` 是**连接级**；`journal_mode` 是**持久化**的
（写进数据库头），但仍在 connect 里设置，保证新库/新连接行为一致。

**追问**：「不设会怎样？」→ 外键不生效；写事务锁全库。

---

### Q16. 时间字段怎么存？为什么用 UTC？

**一句话**：**统一存 naive UTC**，展示时再转本地时区。

**本项目**：

```python
def utcnow() -> datetime:
    """naive UTC——SQLite 不存时区，统一用 UTC 墙钟时间。"""
    return datetime.now(UTC).replace(tzinfo=None)
```

**为什么**：
- SQLite 的 `DATETIME` 不保存时区；
- 存本地时间会在跨时区/夏令时出错；
- 统一 UTC，比较和排序才有意义。

**追问**：「为什么是 naive（不带 tzinfo）？」→ 避免"有时区/无时区"混用导致比较报错；
约定全库统一 UTC。

---

### Q17. 并发写会报 `database is locked` 吗？

**一句话**：会。SQLite 单写者，写冲突时默认**立即报错**。

**现状**：项目没有设置 `busy_timeout`（等锁的超时）。演示场景单请求，问题不大；
并发上来后应该加：

```sql
PRAGMA busy_timeout=5000;   -- 最多等 5 秒
```

> 这是一处已知缺口，面试可以主动说："单机演示够用，上并发要补 busy_timeout 和重试。"

---

## 五、ORM 与 async

### Q18. 为什么 async SQLAlchemy 会报 `MissingGreenlet`？

**一句话**：async 下**隐式懒加载**没有可用的 greenlet 上下文。

**典型触发**：
- 调用了没 `await` 的异步方法；
- `expire_on_commit=True` 时，commit 后再读属性会触发一次隐式 SELECT；
- 访问未加载的 relationship。

**本项目**：`expire_on_commit=False`，避免 commit 后的隐式刷新。

**追问**：「relationship 怎么办？」→ 显式 `selectinload` / `joinedload` 预加载。

---

### Q19. `expire_on_commit=False` 是什么？

**一句话**：commit 后**不把对象标记为过期**，避免下次读属性又查一次库。

默认是 `True`：commit 后所有属性过期，下次访问会触发 SELECT。
在 async 里这个隐式查询可能直接崩（`MissingGreenlet`）。

---

### Q20. `pool_pre_ping=True` 是什么？

**一句话**：从连接池取连接前**先 ping 一下**，失效就重连。

**为什么**：MySQL/PG 的连接可能被服务端空闲断开；不复用坏连接直接报
"server has gone away"。SQLite 下无害，但换库时是必需项。

---

### Q21. N+1 查询是什么？

**一句话**：查 N 条主记录后，又为每条记录各查一次关联表 → 1 + N 次查询。

**例子**：列出 20 条消息，每条再查一次用户 → 21 次。

**怎么发现**：开 SQL 日志，看同一形状的查询重复 N 次。

**怎么解决**：`selectinload` / `joinedload` 预加载；或直接写 join。

**本项目**：目前多是单对象查询（按 id 取一单），没有 N+1；列表接口要做时要注意。

---

### Q22. ORM 和原生 SQL 怎么选？

| 场景 | 建议 |
|---|---|
| 简单 CRUD、类型安全 | ORM |
| 复杂聚合、窗口函数 | 原生 SQL |
| 性能敏感的热点查询 | 原生 SQL + 手写索引 |

**本项目**：仓库层用 ORM；`scripts/init_db.py` 用原生 SQL 校验 DDL。

---

## 六、设计取舍（本项目）

### Q23. 为什么 `user_id` 是 keyword-only 且没有默认值？

**一句话**：让"漏传"变成**运行时错误**，而不是静默查到别人的数据。

```python
async def get_order(order_id: str, *, user_id: str) -> Order:
```

- keyword-only：调用时必须写 `user_id=...`，位置参数塞不进去；
- 无默认值：漏传直接 `TypeError`。

**追问**：「可选参数有什么问题？」→ 漏传会静默放行，越权就藏起来了。

---

### Q24. 为什么"不存在"和"无权限"返回同一个异常？

**一句话**：分开返回会泄露"这个订单确实存在"，形成**枚举 oracle**。

```python
if row is None or row.user_id != user_id:
    raise NotFound("order not found")
```

攻击者拿单号一个个试，就能筛出真实订单。

---

### Q25. 为什么 seed 用 DELETE + INSERT，而不是 upsert？

**一句话**：upsert 删不掉"上次有、这次没有"的旧行，数据集会**腐化**。

**本项目**：业务表是纯种子数据，清空安全；`conversations` / `messages` 是运行时数据，不动。

---

### Q26. schema 变更怎么做？

**一句话**：开发期**重建库**；生产期用**迁移工具**（Alembic）。

**本项目**（加 `signed_at` / `quantity` / `category`）：

```bash
rm data/app.db
uv run python scripts/init_db.py
uv run python scripts/seed.py
```

**为什么不用 ALTER**：SQLite 的 `ALTER TABLE` 能力有限（删列/改类型麻烦）；
演示项目直接重建更简单。

**追问**：「生产能这么干吗？」→ 不能，会丢数据；要用 Alembic 写迁移脚本。

---

### Q27. 怎么知道查询有没有走索引？

**一句话**：`EXPLAIN QUERY PLAN`，看 `SCAN` 还是 `SEARCH ... USING INDEX`。

```sql
EXPLAIN QUERY PLAN
SELECT * FROM orders WHERE user_id = 'u1';
-- SEARCH orders USING INDEX idx_order_user (user_id=?)
```

出现 `SCAN` = 全表扫，要考虑加索引或改查询。

---

## 七、速答表（面试前 5 分钟）

| 问题 | 一句话 |
|---|---|
| 索引为什么快？ | 有序结构，避免全表扫 |
| B-tree vs 哈希 | B-tree 支持范围和排序 |
| 最左前缀 | `(a,b)` 服务 `a` 和 `a+b`，不服务单独 `b` |
| 唯一索引 | 加速 + 不许重复 |
| 部分索引 | 只对满足 WHERE 的行建索引/约束 |
| 主键选型 | 业务键稳定就用业务键 |
| 外键默认关 | SQLite 兼容性；PRAGMA 按连接开 |
| 事务 ACID | 原子/一致/隔离/持久 |
| 脏读/幻读 | 读到未提交 / 两次查行数不同 |
| WAL | 读写不互相阻塞；仍单写者 |
| PRAGMA | 运行时开关；foreign_keys 是连接级 |
| UTC | 跨时区可比较；naive 是约定 |
| MissingGreenlet | async 下隐式懒加载 |
| expire_on_commit | False 避免 commit 后隐式查库 |
| pool_pre_ping | 取连接前探活 |
| N+1 | 循环里查关联；用预加载解决 |
| SAVEPOINT | 子事务，局部回滚 |
| 幂等 | 唯一索引 + 捕获冲突 + 返回首次 |
| 越权 | SQL 层过滤 + 同异常防枚举 |
| 迁移 | 开发重建，生产 Alembic |

---

## 八、不要说的话

| 别说 | 为什么 |
|---|---|
| 「加了索引就快」 | 索引有写成本和空间成本；要讲查询模式 |
| 「事务就是回滚」 | 还有隔离性、一致性、持久性 |
| 「SQLite 不能并发」 | 能并发读；单写者，WAL 下读写不阻塞 |
| 「部分索引是为了允许 NULL」 | 普通唯一索引本来就允许多 NULL（SQLite/PG/MySQL） |
| 「改表结构直接 ALTER 就行」 | SQLite 能力有限；生产要迁移工具 |
