# Ch01 · 数据地基 · 改动记录

> 对应 spec：[ch01-data-foundation-design.md](../superpowers/specs/ch01-data-foundation-design.md)
> 对应 plan：[ch01-data-foundation.md](../superpowers/plans/ch01-data-foundation.md)
> 格式说明见 [00-change-log-overview.md](./00-change-log-overview.md)

| # | 日期 | 任务 | commit |
|---|---|---|---|
| 1 | 2026-10-28 | 依赖与测试配置 | `1e2abe5` |
| 2 | 2026-10-28 | engine + session 工厂 + PRAGMA | `6b3761c` |
| 3 | 2026-10-28 | 10 张表 ORM 模型 | `e47c4d8` |
| 4 | 2026-10-28 | 建表脚本 + schema.sql 比对 | `7974892` |
| 5 | 2026-10-28 | 订单仓库（越权校验） | `eb614e8` |
| 6 | 2026-10-28 | 会话与消息仓库 | `c76fbc8` |
| 7 | 2026-10-28 | seed 脚本 | `2507d50` |
| 8 | 2026-10-28 | 验收与收尾 | `bf8b503` |

---

## 1 · 依赖与测试配置（`1e2abe5`）

### 新增

| 文件 | 内容 |
|---|---|
| `.python-version` | 锁 3.12 |
| `tests/__init__.py` | 空文件，让 `tests` 成为包 |
| `uv.lock` | 依赖锁定 |
| `pyproject.toml` `[tool.pytest.ini_options]` | `asyncio_mode=auto` / `testpaths` / `pythonpath` / `asyncio_default_fixture_loop_scope` |
| `pyproject.toml` `[tool.ruff]` | `line-length=100`、`target-version=py311` |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `pyproject.toml` · dependencies | 只有 fastapi / uvicorn / pydantic-settings / sqlalchemy | 追加 `aiosqlite` | 没有异步驱动，`sqlite+aiosqlite://` 连不上 |
| `pyproject.toml` · dev 组 | 只有 pytest / ruff | 追加 `pytest-asyncio` | `async def test_*` 需插件才能被收集，否则跳过 |

### 验证
- `uv run pytest -q` → 无测试可跑（配置生效）

---

## 2 · engine + session 工厂 + PRAGMA（`6b3761c`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/db/base.py` | `Base(DeclarativeBase)` |
| `app/db/session.py` | `_ensure_sqlite_dir()` / `_install_sqlite_pragmas()` / `make_engine()` / `engine` / `async_session` / `get_session()` |
| `tests/conftest.py` | `engine`、`session` fixture（临时库，不碰 `data/app.db`） |
| `tests/test_session.py` | 4 条：连通 / `foreign_keys=1` / `journal_mode=wal` / 第二条连接同样生效 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `pyproject.toml` · dependencies | `sqlalchemy` | `sqlalchemy[asyncio]` | 不带 extras 时缺 `greenlet`，`from sqlalchemy.ext.asyncio import …` 直接 `ImportError` |
| `session.py` · engine 创建 | 计划里的做法：模块级 `create_async_engine(...)` 一把建完 | 抽出 `make_engine(url)` 工厂，模块级 `engine = make_engine(settings.url)` | PRAGMA 必须挂在**真实建 engine 的路径**上。测试要指向临时库，若另写一份 engine，就测不到真实的 PRAGMA 逻辑 |
| `session.py` · PRAGMA 时机 | 直觉做法：启动时执行一次 | 挂 `@event.listens_for(engine.sync_engine, "connect")` | PRAGMA 是**连接级**设置。启动时执行一次只对那一条连接生效，连接池后续新建的连接会回到 `foreign_keys=OFF` |

### 验证
- `uv run pytest -q` → 5 passed

---

## 3 · 10 张表 ORM 模型（`e47c4d8`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/db/models.py` | 10 个模型（`Conversation` / `Message` / `Ticket` / `TraceSpan` / `Faq` / `LowConfQuestion` / `Order` / `Logistics` / `Stock` / `Coupon`）+ `utcnow()` |
| `tests/test_models.py` | 6 条：表齐 / 索引齐 / 孤儿消息被拒 / 重复幂等键被拒 / 多 NULL 可共存 / `sku+spec` 唯一 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `tests/conftest.py` · 顶部 import | 只 `from app.db.base import Base` | 增加 `import app.db.models` | 模型未 import 时 `Base.metadata` 是空的，`create_all` 建出 **0 张表**，测试全在空库上跑 |
| `app/db/session.py` · `_set_pragmas` | 带 `# noqa: ANN001` | 去掉 | ruff 未启用 ANN 规则，`RUF100` 报"无用 noqa"；留着会掩盖真正需要的抑制 |

### 验证
- `uv run pytest -q` → 11 passed
- 反向用例：插 `conversation_id=999` 抛 `IntegrityError`（证明 `foreign_keys=ON` 真生效）

---

## 4 · 建表脚本 + schema.sql 比对（`7974892`）

### 新增

| 文件 | 内容 |
|---|---|
| `scripts/init_db.py` | `main()`（`create_all`）+ `schema_sql_objects()` / `orm_objects()` / `diff()`：表 / 列 / 索引三项比对，不一致退出码 1 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `.gitignore` · 数据段 | 只忽略 `*.db` | 追加 `*.db-wal` / `*.db-shm` | WAL 模式会生成两个伴生文件，`*.db` 匹配不到，会误提交进仓库 |
| `scripts/init_db.py` · 对齐检查 | 计划 Task 4 Step 2：**人工比对** `schema.sql` 与 ORM 字段 | 建表后自动比对，不一致退出码 1 | 双写必然漂移。人工比对会漏，**漏了也没人发现**——等出错时已经晚了几周 |

### 验证
- `uv run python scripts/init_db.py` → 10 张表、schema.sql 与 ORM 一致
- 重复执行 → 幂等
- `sqlite3 .indexes messages` → `idx_msg_conv` / `uk_msg_idem`

---

## 5 · 订单仓库（越权校验）（`eb614e8`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/db/errors.py` | `NotFound`（不存在与无权限共用，防枚举） |
| `app/db/repo_order.py` | `get_order` / `list_user_orders` / `get_tracking` / `get_stock` / `get_coupons` |
| `tests/test_repo_order.py` | 4 条：漏参 `TypeError` / 越权 `NotFound` / 不存在同异常 / 正常返回 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/db/session.py` · 末尾 | 每个 repo 函数自己 `async with async_session() as s`，自开自提交（参考实现的做法） | 新增 `session_scope(session=None)`：给了就复用，没给才自开 | K3 要求"主表事务"——一次退费要同时写 `messages` 和 `conversations`。各函数自开 session 会让这两步变成**两个事务**，中间失败就是"消息写了、流程状态没清" |

### 验证
- `uv run pytest -q` → 15 passed

---

## 6 · 会话与消息仓库（`c76fbc8`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/db/repo_conversation.py` | `create_conversation` / `get_conversation`（归属 + 软删）/ `set_flow` / `clear_flow` / `list_conversations` / `_load` |
| `app/db/repo_message.py` | `append_message`（K4 幂等）/ `list_messages` / `_find_by_client_id` |
| `tests/test_repo_message.py` | 6 条：会话越权 / 流程往返 / 幂等去重 / 无幂等键累加 / 共享 session 回滚 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `repo_message.py` · `append_message` 冲突处理 | 撞唯一索引后 `await s.rollback()`，再按幂等键查回 | `async with s.begin_nested()` 包住插入，冲突只回滚 SAVEPOINT | `session.rollback()` 回滚的是**整个事务**——调用方（K3）可能已在同一事务里写了别的表，全被一起撤销 |
| `repo_message.py` · 重试语义 | 计划选项：抛唯一约束 | 返回首次写入的 id | 客户端重试不是错误，抛异常会让一次网络抖动变成 **500**；且破坏"同一 `client_message_id` 只用一次"的契约 |
| `repo_conversation.py` · `set_flow` | 直觉做法：三个字段分别写 | 三字段整组写入，`updated_at` 一起更新 | 分开写会出现"流程清了、草稿还在"的中间态；`updated_at` 漏更新会让超时清理（M7）算错 |

### 验证
- `uv run pytest -q` → 21 passed

---

## 7 · seed 脚本（`2507d50`）

### 新增

| 文件 | 内容 |
|---|---|
| `scripts/seed.py` | `STOCK`(6) / `ORDERS`(20) / `LOGISTICS`(6) / `COUPONS`(5) / `DISCOUNT`；`_order_rows` / `_logistics_rows` / `_coupon_rows`；`main()`（先清空 4 张业务表，prod 拒绝执行） |
| `tests/test_seed.py` | 8 条：SKU 覆盖 / 6 种状态 / 每人 3–5 单 / 订单号前缀=归属 / 有超 7 天单 / 券含过期·已用·高门槛 / 实付不超标价 / 券码唯一 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/db/models.py`、`schema.sql` · `Order.status` 注释 | 5 种：待付款 / 已付款 / 已发货 / 已签收 / 已关闭 | 6 种：待付款 / **待发货** / 已发货 / 已签收 / **退款中** / 已关闭 | 与数据设计 §五 不符。少了"待发货"就分不清"已付款未发"与"已发货"——**改地址/取消的规则判断依赖这个区别**；少了"退款中"则 C 出口无法表达进行中的退款 |
| `scripts/seed.py` · 幂等策略 | 计划选项：按主键 upsert | 先 `DELETE` 4 张业务表再插入（`conversations` / `messages` 不动） | upsert 删不掉"上一次 seed 有、这次没有"的旧行，数据集会逐渐腐化；而业务表是纯种子数据，清空安全 |
| `scripts/seed.py` · 环境守卫 | 无 | `app_env == "prod"` 直接拒绝执行 | 清空业务表的脚本一旦在 prod 跑，就是数据事故 |

### 验证
- `uv run python scripts/seed.py` → 6 / 20 / 6 / 5
- 重复执行 → 数量不翻倍
- `group by user_id` → u1:5 u2:4 u3:4 u4:4 u5:3

---

## 8 · 验收与收尾（`bf8b503`）

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `README.md` · 新增「快速开始」 | 无运行说明，读者不知道要先建库再跑 | `uv sync` → `init_db` → `seed` → `pytest` → `uvicorn` | 少一步 `init_db` 就会报 `no such table`，看起来像代码坏了 |
| `plans/00-tasks.md` · P1 表 | 写着"6 张表""mock 源""`tests/test_repo.py`" | 10 张表 / 落表 / 四个测试文件 | 任务表是后续执行的依据，与实现不符会误导下一步（尤其"mock 源"会让人以为不用建表） |
| `plans/ch01-data-foundation.md` · Task 8 | 5 个未勾选项 | 全部勾选 | 不勾选无法判断本章是否收尾 |

### 验证
- 从零建库 + seed → 10 表 / 7 索引 / 6 SKU / 20 单 / 5 券
- `uv run pytest -q` → 30 passed
- 越权手工验证：`get_order("2001", user_id="u1")` → `NotFound`；漏参 → `TypeError`
