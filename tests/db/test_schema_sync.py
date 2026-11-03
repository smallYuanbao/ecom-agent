"""schema.sql 与 ORM 的一致性守卫。

`scripts/init_db.py` 里已经有比对逻辑，但它只在**手动跑脚本**时执行。
这条测试把它接进 CI：任何人改了 `models.py` 或 `schema.sql` 却忘了另一边，
`pytest` 会直接失败。

**为什么不连真实数据库**：`schema_sql_objects()` 在内存库里执行 DDL，
`orm_objects()` 只读 `Base.metadata`。两边都不需要 `data/app.db`，
所以测试不依赖本地库状态，也不会污染它。
"""
from scripts.init_db import diff, orm_objects, schema_sql_objects


def test_schema_sql_matches_orm():
    """表 / 列 / 索引三项都必须一致。"""
    problems = diff(schema_sql_objects(), orm_objects())
    assert problems == [], "schema.sql 与 ORM 漂移：\n" + "\n".join(problems)


def test_both_sides_define_all_tables():
    """防止某一侧被整体清空——空对空也能让 diff 通过。"""
    sql_tables, _ = schema_sql_objects()
    orm_tables, _ = orm_objects()
    assert len(sql_tables) == 10
    assert set(sql_tables) == set(orm_tables)
