"""建表脚本：`create_all` 建库，并校验 `schema.sql` 与 ORM 是否一致。

为什么两边都保留：
- `schema.sql` 是可审阅的 DDL（人能一眼看懂表长什么样）
- ORM 是运行时真源（类型、索引定义都在这里）

漂移是这对组合唯一的风险，所以建完表顺手比对一次——
**不一致就报错，不让人肉比对的活留给记忆。**
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/init_db.py` 直接跑

from app.db import models  # noqa: F401  确保模型注册到 Base.metadata
from app.db.base import Base
from app.db.session import engine

SCHEMA_SQL = ROOT / "app" / "db" / "schema.sql"


def schema_sql_objects() -> tuple[dict[str, set[str]], set[str]]:
    """在内存库里执行 schema.sql，取出 (表→列集合, 索引名集合)。"""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        columns = {
            t: {r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')} for t in tables
        }
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        return columns, indexes
    finally:
        conn.close()


def orm_objects() -> tuple[dict[str, set[str]], set[str]]:
    columns = {t.name: set(t.columns.keys()) for t in Base.metadata.sorted_tables}
    indexes = {i.name for t in Base.metadata.sorted_tables for i in t.indexes if i.name}
    # UNIQUE 约束在建表时会生成为索引，SQL 侧的写法是 inline UNIQUE / 命名约束
    return columns, indexes


def diff(
    sql: tuple[dict[str, set[str]], set[str]],
    orm: tuple[dict[str, set[str]], set[str]],
) -> list[str]:
    problems: list[str] = []
    sql_cols, sql_idx = sql
    orm_cols, orm_idx = orm

    for table in sorted(set(sql_cols) | set(orm_cols)):
        if table not in orm_cols:
            problems.append(f"表 {table}: schema.sql 有，ORM 没有")
            continue
        if table not in sql_cols:
            problems.append(f"表 {table}: ORM 有，schema.sql 没有")
            continue
        if missing := sql_cols[table] - orm_cols[table]:
            problems.append(f"表 {table}: 列 {sorted(missing)} 只在 schema.sql 里")
        if missing := orm_cols[table] - sql_cols[table]:
            problems.append(f"表 {table}: 列 {sorted(missing)} 只在 ORM 里")

    if missing := sql_idx - orm_idx:
        problems.append(f"索引只在 schema.sql 里: {sorted(missing)}")
    return problems


async def main() -> int:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    tables = sorted(t.name for t in Base.metadata.sorted_tables)
    print(f"已建表 {len(tables)} 张: {', '.join(tables)}")

    problems = diff(schema_sql_objects(), orm_objects())
    if problems:
        print("\n[schema.sql 与 ORM 不一致]")
        for p in problems:
            print("  -", p)
        return 1

    print("schema.sql 与 ORM 一致（表 / 列 / 索引）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
