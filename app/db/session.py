"""数据库连接：engine + session 工厂 + SQLite PRAGMA。

设计依据：T7（async SQLAlchemy + aiosqlite）。
两个参数都不是优化项，是必需项：

- `expire_on_commit=False`：commit 后再读对象属性会触发隐式懒加载，
  async 下直接 `MissingGreenlet` 崩掉。
- `pool_pre_ping=True`：SQLite 下无害；将来换 PG/MySQL 时，空闲连接被
  服务端掐断后首个请求会报 "server has gone away"，有此参数则自动重连。

PRAGMA 说明（SQLite 默认值对我们不利）：

- `foreign_keys` 默认 **OFF**：建表时写的 `REFERENCES` 只是元数据，
  不设此开关则外键形同注释，能插进指向不存在会话的消息。
- `journal_mode` 默认 `delete`：写事务期间锁全库，读写不能并发；WAL 可并发。
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_SQLITE_PREFIX = "sqlite+aiosqlite:///"


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite 不会自动建目录，`data/` 不存在时连库直接报 unable to open database file。"""
    if not url.startswith(_SQLITE_PREFIX):
        return
    db_path = Path(url[len(_SQLITE_PREFIX) :])
    if db_path.name and db_path.name != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """PRAGMA 是连接级设置：每条物理连接新建时都要重设一次。

    挂 `connect` 事件而不是启动时执行一次——后者只对那一条连接生效，
    连接池后续新建的连接仍会回到默认值（外键关闭）。
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


def make_engine(url: str) -> AsyncEngine:
    """按 URL 造一个配置齐全的 engine。测试用它指向临时库，生产用模块级 engine。"""
    _ensure_sqlite_dir(url)
    engine = create_async_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        _install_sqlite_pragmas(engine)
    return engine


engine = make_engine(get_settings().database_url)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    """FastAPI 依赖用；一次请求一个 session。业务层优先用 `session=` 参数传入（K3 跨表事务）。"""
    async with async_session() as session:
        yield session


@asynccontextmanager
async def session_scope(session: AsyncSession | None = None) -> AsyncIterator[AsyncSession]:
    """repository 的统一入口：给了 session 就复用，没给就自开一个并托管事务。

    - **传入 session**：事务归调用方，这里既不提交也不回滚（K3 跨表事务靠这个）
    - **不传**：自开事务，正常退出 commit、异常 rollback

    参考实现是"每个函数自开自提交"，做不到跨表事务；这里加 `session` 参数就是为了补这个洞。
    """
    if session is not None:
        yield session
        return
    async with async_session() as s, s.begin():
        yield s
