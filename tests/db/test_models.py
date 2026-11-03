"""模型层验证：表齐、索引齐、约束真的在工作。

第 3 组用「反向用例」——故意写非法数据，确认被拦住。
只测"合法数据能插"证明不了约束存在（PRAGMA 关闭时非法数据一样能插）。
"""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Conversation, Message, Stock

EXPECTED_TABLES = {
    "conversations",
    "messages",
    "tickets",
    "trace_span",
    "faq",
    "low_conf_questions",
    "orders",
    "logistics",
    "stock",
    "coupons",
}


async def test_all_ten_tables_exist(engine):
    async with engine.connect() as conn:
        names = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    assert names == EXPECTED_TABLES


async def test_unique_indexes_exist(engine):
    async with engine.connect() as conn:
        indexes = await conn.run_sync(
            lambda c: {i["name"] for i in inspect(c).get_indexes("messages")}
        )
        ticket_indexes = await conn.run_sync(
            lambda c: {i["name"] for i in inspect(c).get_indexes("tickets")}
        )
    assert "uk_msg_idem" in indexes
    assert "idx_msg_conv" in indexes
    assert "uk_ticket_idem" in ticket_indexes


async def test_foreign_key_rejects_orphan_message(session):
    """外键真在拦：插一条指向不存在会话的消息必须失败。"""
    session.add(Message(conversation_id=999, role="user", content="孤儿"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_idempotent_index_rejects_duplicate_client_message_id(session):
    """K4：同一条消息重试时复用 client_message_id，第二次要冲突。"""
    conv = Conversation(user_id="u1")
    session.add(conv)
    await session.flush()
    cid = conv.id

    session.add(Message(conversation_id=cid, role="user", client_message_id="cm-1"))
    await session.commit()

    session.add(Message(conversation_id=cid, role="user", client_message_id="cm-1"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_partial_index_allows_multiple_null_client_message_id(session):
    """没带幂等键的消息可以有任意多条。

    注意：SQLite 的**普通唯一索引也允许多条 NULL**（`NULL != NULL`）。
    部分索引的价值在语义显式与跨库一致（SQL Server 只允许一个 NULL），
    不是"让 NULL 能共存"。
    """
    conv = Conversation(user_id="u1")
    session.add(conv)
    await session.flush()

    session.add_all(
        [
            Message(conversation_id=conv.id, role="user", client_message_id=None),
            Message(conversation_id=conv.id, role="user", client_message_id=None),
        ]
    )
    await session.commit()

    count = (await session.execute(text("SELECT count(*) FROM messages"))).scalar()
    assert count == 2


async def test_stock_unique_per_sku_and_spec(session):
    """一个型号+规格只能有一条库存记录。"""
    session.add(Stock(sku_id="ST-60", spec="标准装", stock=3, price=9900))
    await session.commit()

    session.add(Stock(sku_id="ST-60", spec="标准装", stock=1, price=9800))
    with pytest.raises(IntegrityError):
        await session.commit()
