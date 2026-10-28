# Ch01 数据地基 Implementation Plan

> **For agentic workers:** 按 task 顺序逐个执行。步骤用 checkbox（`- [ ]`）追踪。**每个 task 完成即 commit。**

**Goal:** 跑通"数据能存、能查、查不错别人的"：10 张表 + seed + 越权校验。

**Architecture:** 单进程 FastAPI(:8000) + SQLite 单文件 `data/app.db`，SQLAlchemy 2.x async ORM。本章无业务接口，数据经 `seed.py` 直接写库。

**Tech Stack:** Python 3.11+ / uv / FastAPI / SQLAlchemy 2.x async / aiosqlite / pydantic-settings / pytest-asyncio。

**Spec:** `docs/superpowers/specs/ch01-data-foundation-design.md`
**技术选型:** T7 = async SQLAlchemy + aiosqlite（见 `00-tech-decisions.md`）

---

## Global Constraints

- **技术定死**：FastAPI + SQLAlchemy async + SQLite。走不通停下问，不许换方案。
- **本章零密钥**：不调任何模型，`CHAT_API_KEY` 可以为空。
- **`user_id` 是 keyword-only 且无默认值**：所有业务查询都必须显式传，漏传直接 `TypeError`。
- **越权与不存在同异常**：不让调用方有机会区分。
- **业务数据落表**，不写 mock 函数（理由见 spec 决策记录）。
- **`schema.sql` 与 ORM 双写**：DDL 可审阅，ORM 提供类型。
- 每个 task 完成即 commit，message 说明改了什么。

---

### Task 1: 依赖与测试配置

**Files:**
- Modify: `pyproject.toml`
- Create: `pytest.ini` 或项目内配置

**Interfaces:**
- pytest 能发现 `tests/`，且能 `import app.*`

**Step 1: 加依赖**

```bash
cd /Users/lidong/Desktop/workFile/ecom-agent
uv add "sqlalchemy[asyncio]" aiosqlite    # 必须带 [asyncio]：否则缺 greenlet，import 即报错
uv add --dev pytest pytest-asyncio
```

**Step 2: 配置 pytest**

`pyproject.toml` 追加：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

**验收**：`uv run pytest -q` 能跑（0 tests 也算通过）。

**Commit:** `chore: 加 SQLAlchemy/aiosqlite 与 pytest 配置`

---

### Task 2: 数据库会话（engine + PRAGMA + session 工厂）

**Files:**
- Create: `app/db/base.py`
- Create: `app/db/session.py`
- Test: `tests/test_session.py`

**Interfaces:**

```python
# app/db/base.py
class Base(DeclarativeBase): ...

# app/db/session.py
engine: AsyncEngine
async_session: async_sessionmaker[AsyncSession]

async def get_session() -> AsyncIterator[AsyncSession]:   # 需要时用
```

**Step 1: engine 与 PRAGMA**

```python
# app/db/session.py
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings


def make_engine(url: str) -> AsyncEngine:      # 测试指向临时库也走这条路径
    _ensure_sqlite_dir(url)                    # SQLite 不自建目录
    eng = create_async_engine(url, pool_pre_ping=True)   # T7：换 PG/MySQL 后必需
    _install_sqlite_pragmas(eng)
    return eng


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")     # 不设：外键声明不生效
        cur.execute("PRAGMA journal_mode=WAL")    # 不设：并发读写易 locked
        cur.close()


engine = make_engine(get_settings().database_url)
async_session = async_sessionmaker(engine, expire_on_commit=False)  # T7：async 必设
```

**为什么抽出 `make_engine`**：PRAGMA 必须挂在真实建 engine 的路径上，测试用临时库时若另写一份 engine，就测不到真实的 PRAGMA 逻辑。

**Step 2: 建库目录**

SQLite 不会自动建目录；`data/` 不存在时连接直接报 `unable to open database file`。

```python
from pathlib import Path
Path("data").mkdir(exist_ok=True)   # 放在 engine 创建之前
```

**Step 3: 测试**

`test_session.py`：连一次库，`SELECT 1`；断言 `PRAGMA foreign_keys` 返回 1。

**验收**：`uv run pytest tests/test_session.py -q` 绿。

**Commit:** `feat(db): engine + session 工厂 + SQLite PRAGMA`

---

### Task 3: ORM 模型（10 张表）

**Files:**
- Create: `app/db/models.py`
- Test: `tests/test_models.py`

**Interfaces:** 10 个模型类，字段与 `app/db/schema.sql` 一致。

| 组 | 模型 |
|---|---|
| 系统 | `Conversation` / `Message` / `Ticket` / `TraceSpan` / `Faq` / `LowConfQuestion` |
| 业务 | `Order` / `Logistics` / `Stock` / `Coupon` |

**关键约束（照 spec）**：

```python
class Message(Base):
    __tablename__ = "messages"
    status = mapped_column(String, nullable=True)          # C1：completed/cancelled/error
    client_message_id = mapped_column(String, nullable=True)
    __table_args__ = (
        Index("uk_msg_idem", "conversation_id", "client_message_id",
              unique=True, sqlite_where=text("client_message_id IS NOT NULL")),  # K4
    )
```

**验收**：`test_models.py` 断言 10 张表存在于 metadata；两个唯一索引存在。

**Commit:** `feat(db): 10 张表 ORM 模型（含两个部分唯一索引）`

---

### Task 4: 建表脚本

**Files:**
- Create: `scripts/init_db.py`

**Interfaces:** `python scripts/init_db.py` 建表；重复执行幂等。

**Step 1: 实现**

```python
async def main() -> None:
    from app.db import models  # noqa: F401  确保模型被注册
    from app.db.base import Base
    from app.db.session import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

**Step 2: 与 schema.sql 对齐检查（自动，不靠人肉）**

`init_db.py` 建完表后，把 `schema.sql` 在内存库跑一遍，比对「表 / 列 / 索引」三项，不一致直接退出码 1。

`schema.sql` 是"可审阅的 DDL"，ORM 是"运行时真源"。**不一致时以 spec 为准，两边一起改。**

**验收**：

```bash
uv run python scripts/init_db.py
sqlite3 data/app.db ".tables"            # 10 张表
sqlite3 data/app.db ".indexes messages"  # 含 uk_msg_idem
```

**Commit:** `feat(db): 建表脚本（create_all，幂等）`

---

### Task 5: 订单仓库（user_id 必填 + 越权校验）

**Files:**
- Create: `app/db/repo_order.py`
- Test: `tests/test_repo_order.py`

**Interfaces:**

```python
async def get_order(order_id: str, *, user_id: str) -> Order      # 越权/不存在 → NotFound
async def list_user_orders(*, user_id: str) -> list[Order]
async def get_tracking(*, tracking_no: str, user_id: str) -> Logistics | None
async def get_stock(*, sku_id: str, spec: str) -> Stock | None
async def get_coupons(*, user_id: str) -> list[Coupon]
```

**Step 1: NotFound**

```python
class NotFound(Exception):
    """不存在与无权限共用，调用方无法区分（S1/S2 防枚举）。"""
```

**Step 2: 实现 get_order**

```python
async def get_order(order_id: str, *, user_id: str,
                    session: AsyncSession | None = None) -> Order:
    async def _q(s: AsyncSession) -> Order:
        row = await s.get(Order, order_id)
        if row is None or row.user_id != user_id:
            raise NotFound()
        return row

    if session is not None:
        return await _q(session)
    async with async_session() as s:
        return await _q(s)
```

**Step 3: 测试（4 条）**

| # | 用例 | 断言 |
|---|---|---|
| 1 | `get_order("1002")`（漏 user_id） | `TypeError` |
| 2 | `get_order("1002", user_id="u2")`（不是他的） | `NotFound` |
| 3 | `get_order("9999", user_id="u1")`（不存在） | `NotFound`（与 2 同类型） |
| 4 | `get_order("1002", user_id="u1")` | 返回 Order |

**验收**：4 条测试绿。

**Commit:** `feat(repo): 订单仓库，user_id 必填 + 越权同异常`

---

### Task 6: 会话与消息仓库（session 可传入）

**Files:**
- Create: `app/db/repo_conversation.py`
- Create: `app/db/repo_message.py`
- Test: `tests/test_repo_message.py`

**Interfaces:**

```python
# 会话
async def create_conversation(user_id: str, *, session=None) -> int
async def get_conversation(cid: int, *, user_id: str, session=None) -> Conversation
async def set_flow(cid: int, *, active_flow: str | None,
                   pending_question: str | None, draft: dict | None, session=None) -> None
async def clear_flow(cid: int, *, session=None) -> None

# 消息
async def append_message(cid: int, role: str, content: str | None, *,
                         status: str | None = None, client_message_id: str | None = None,
                         session=None) -> int
async def list_messages(cid: int, *, session=None) -> list[Message]
```

**关键**：所有写函数支持 `session` 传入，方便 K3 的跨表事务。

**测试（2 条）**：

| # | 用例 | 断言 |
|---|---|---|
| 1 | 同一 `(cid, client_message_id)` 插两次 | 第二次抛唯一约束（或返回已有 id，取决于实现） |
| 2 | 传入同一个 session 连续写 conversation + message | 两个写入都在，且 `committed=False` 前可回滚 |

**验收**：2 条测试绿。

**Commit:** `feat(repo): 会话与消息仓库（支持事务复用）`

---

### Task 7: seed 脚本

**Files:**
- Create: `scripts/seed.py`
- Create: `data/seed/`（可选，把种子数据放 JSON）

**数据规模（照 spec）**：

| 表 | 条数 | 要求 |
|---|---|---|
| `stock` | 6 | 3 系列 × 2 型号；含 1 个无货、1 个预售 |
| `orders` | 20 | 覆盖 6 种状态；每个用户 3–5 单；含 1 单已超 7 天 |
| `coupons` | 5 | 含已过期、未达门槛、不可叠加 |
| `conversations` | 0 | 由运行时创建 |

**Step 1: 幂等**

`seed.py` 重复执行不重复插入（按主键 upsert 或先清空业务表）。

**Step 2: 归属分布**

```
u1：订单 1001/1002/1003 …（主角）
u2：订单 2001/2002 …（用于越权测试）
```

**验收**：

```bash
uv run python scripts/seed.py
sqlite3 data/app.db "select count(*) from orders;"   # 20
sqlite3 data/app.db "select user_id,count(*) from orders group by 1;"
```

**Commit:** `feat(seed): 6 SKU / 20 订单 / 5 券`

---

### Task 8: 验收与收尾

- [ ] `uv run python scripts/init_db.py && uv run python scripts/seed.py`
- [ ] `uv run pytest -q` 全绿
- [ ] 手工验证越权：`get_order("2001", user_id="u1")` → NotFound
- [ ] 检查 `schema.sql` 与 `models.py` 字段一致
- [ ] 更新 `docs/superpowers/plans/00-tasks.md` 里 P1 的勾选
- [ ] 提交

**Commit:** `test: Ch01 验收（10 表 + seed + 越权）`

---

## 与 spec 的对应

| spec 验收项 | 落在哪个 task |
|---|---|
| 10 张表建好、两个唯一索引 | Task 3 / 4 |
| seed 6/20/5 | Task 7 |
| `user_id` 必填、越权 NotFound | Task 5 |
| pytest 全绿（外键/索引/必填/越权） | Task 5 / 6 / 8 |

## 风险

| 风险 | 应对 |
|---|---|
| async SQLAlchemy 的 `MissingGreenlet` | 设 `expire_on_commit=False`；测试里覆盖 commit 后访问属性 |
| SQLite 外键默认关闭 | `connect` 事件开 `PRAGMA`，并用"插孤儿样本"反向测试 |
| `schema.sql` 与 ORM 漂移 | Task 8 强制比对；后续改字段两边一起改 |
