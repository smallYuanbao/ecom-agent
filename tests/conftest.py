"""测试夹具：每个测试用独立临时库，不碰 `data/app.db`。

engine 仍走 `make_engine`，所以测试验证的是**真实的 PRAGMA 配置路径**。
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import models  # noqa: F401  确保所有模型在建表前注册到 Base.metadata
from app.db.base import Base
from app.db.session import make_engine


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture(autouse=True)
async def _close_llm_client():
    """每个测试结束关掉 LLM 客户端。

    `get_client()` 用了 `lru_cache`，而 pytest-asyncio 默认每个测试一个事件循环——
    缓存的 httpx 连接属于上一个循环，下一个测试复用会报 `Event loop is closed`。
    生产环境只有一个循环，缓存是对的；问题只出在测试。
    """
    yield
    from app.core import llm

    if llm.get_client.cache_info().currsize:
        await llm.get_client().close()
        llm.get_client.cache_clear()
