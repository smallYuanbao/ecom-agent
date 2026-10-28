"""ORM 基类。

所有模型继承 `Base`，`Base.metadata` 是建表与测试断言的唯一来源。
设计依据：T7（async SQLAlchemy）、Ch01 spec「schema.sql 与 ORM 双写」。
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
