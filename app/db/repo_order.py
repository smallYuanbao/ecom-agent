"""订单 / 物流 / 库存 / 券的读取仓库。

**归属校验下沉到数据层**：`user_id` 一律 keyword-only 且没有默认值，
漏传直接 `TypeError`，而不是静默查到别人的单（Ch01 spec「核心逻辑」）。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.errors import NotFound
from app.db.models import Coupon, Logistics, Order, Stock
from app.db.session import session_scope


async def get_order(
    order_id: str, *, user_id: str, session: AsyncSession | None = None
) -> Order:
    """取订单。不存在与不属于该用户抛同一个 `NotFound`。"""
    async with session_scope(session) as s:
        row = await s.get(Order, order_id)
        if row is None or row.user_id != user_id:
            raise NotFound("order not found")
        return row


async def list_user_orders(*, user_id: str, session: AsyncSession | None = None) -> list[Order]:
    """列出某用户的全部订单，最近下单在前。"""
    async with session_scope(session) as s:
        stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
        )
        return list((await s.scalars(stmt)).all())


async def get_tracking(
    *, tracking_no: str, user_id: str, session: AsyncSession | None = None
) -> Logistics | None:
    """按单号取物流轨迹。归属在 SQL 里用 join 校验——查不到与没权限都返回 None。"""
    async with session_scope(session) as s:
        stmt = (
            select(Logistics)
            .join(Order, Order.id == Logistics.order_id)
            .where(Logistics.tracking_no == tracking_no, Order.user_id == user_id)
            .order_by(Logistics.updated_at.desc())
            .limit(1)
        )
        return (await s.scalars(stmt)).first()


async def get_stock(
    *, sku_id: str, spec: str, session: AsyncSession | None = None
) -> Stock | None:
    """按型号+规格取库存与价格。商品信息是公开的，不需要 user_id。"""
    async with session_scope(session) as s:
        stmt = select(Stock).where(Stock.sku_id == sku_id, Stock.spec == spec)
        return (await s.scalars(stmt)).first()


async def list_stock_by_sku(
    sku_id: str, *, session: AsyncSession | None = None
) -> list[Stock]:
    """列某型号的全部规格，价格升序。

    用户只报型号、没报规格时用：先拿全部规格，再拿原话去匹配。
    """
    async with session_scope(session) as s:
        stmt = (
            select(Stock)
            .where(Stock.sku_id == sku_id)
            .order_by(Stock.price.asc(), Stock.spec.asc())
        )
        return list((await s.scalars(stmt)).all())


async def list_stock(*, session: AsyncSession | None = None) -> list[Stock]:
    """列全部库存行，按型号 + 规格排序。

    缺型号时列候选用；数据量很小（6 行），不需要分页。
    """
    async with session_scope(session) as s:
        stmt = select(Stock).order_by(Stock.sku_id.asc(), Stock.spec.asc())
        return list((await s.scalars(stmt)).all())


async def get_coupons(*, user_id: str, session: AsyncSession | None = None) -> list[Coupon]:
    """取某用户的券，快过期的在前。"""
    async with session_scope(session) as s:
        stmt = (
            select(Coupon)
            .where(Coupon.user_id == user_id)
            .order_by(Coupon.expires_at.asc())
        )
        return list((await s.scalars(stmt)).all())
