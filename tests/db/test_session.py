"""engine / session / PRAGMA 的连接层验证。"""
from sqlalchemy import text


async def test_engine_can_connect(engine):
    async with engine.connect() as conn:
        assert (await conn.exec_driver_sql("SELECT 1")).scalar() == 1


async def test_foreign_keys_pragma_is_on(engine):
    """不设的话，messages 的 REFERENCES 只是注释，插孤儿数据不报错。"""
    async with engine.connect() as conn:
        value = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
    assert value == 1


async def test_journal_mode_is_wal(engine):
    """默认 delete 模式下写事务锁全库；WAL 允许读写并发。"""
    async with engine.connect() as conn:
        mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
    assert mode.lower() == "wal"


async def test_pragma_applies_to_every_new_connection(engine):
    """PRAGMA 是连接级的——连接池里新建的第二条连接也必须开着外键。

    这条测试防的是「只在启动时执行一次 PRAGMA」的写法。
    """
    async with engine.connect():
        pass
    async with engine.connect() as second:
        assert (await second.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1


async def test_session_factory_yields_usable_session(session):
    assert (await session.execute(text("SELECT 2"))).scalar() == 2
