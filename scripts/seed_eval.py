"""评测专用订单：时间相对「现在」生成，避免 seed 的固定 NOW 过期。

`scripts/seed.py` 用的是固定 `NOW = 2026-10-28`，目的是让演示可复现。
但退款规则按**真实当前时间**算 7 天窗口，评测隔几天再跑，
「签收 2 天」的订单就会变成「签收 10 天」。

所以评测库在 seed 之后再加 8 笔订单：

| 订单 | 用途 |
|---|---|
| 1101 | 签收 2 天，可退（金额 1299.00） |
| 1102 | 签收 1 天，2 件，部分退款 |
| 1103 | 定制商品，第六条不适用 |
| 1104 | 奶瓶且已拆封，第七条不适用 |
| 1105 | 签收早于发货，日期倒挂 |
| 1106 | 签收 10 天，超 7 天 |
| 1107 | 签收 10 天但有质量问题，窗口例外 |
| 1108 | 已发货的定制商品，拒收退款优先于类别 |

用法（评测库）：

    DATABASE_URL=sqlite+aiosqlite:///./data/eval.db uv run python scripts/init_db.py
    DATABASE_URL=sqlite+aiosqlite:///./data/eval.db uv run python scripts/seed.py
    DATABASE_URL=sqlite+aiosqlite:///./data/eval.db uv run python scripts/seed_eval.py
"""
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete

from app.config import get_settings
from app.db.models import Order, utcnow
from app.db.session import async_session

EVAL_ORDER_IDS = tuple(f"11{i:02d}" for i in range(1, 9))


def _order(
    order_id: str,
    *,
    status: str,
    amount: int,
    category: str = "婴儿推车",
    sku_id: str = "ST-20",
    spec: str = "石墨灰",
    quantity: int = 1,
    created_days: int = 5,
    shipped_days: int = 3,
    signed_days: int | None = 2,
    tracking_no: str | None = None,
) -> Order:
    now = utcnow()
    created = now - timedelta(days=created_days)
    paid = created + timedelta(minutes=15)
    shipped = None if shipped_days is None else now - timedelta(days=shipped_days)
    signed = None if signed_days is None else now - timedelta(days=signed_days)
    return Order(
        id=order_id,
        user_id="u1",
        sku_id=sku_id,
        spec=spec,
        category=category,
        status=status,
        amount=amount,
        quantity=quantity,
        created_at=created,
        paid_at=paid,
        shipped_at=shipped,
        signed_at=signed,
        tracking_no=tracking_no,
    )


def eval_orders() -> list[Order]:
    return [
        _order("1101", status="已签收", amount=129_900, tracking_no="SF1101001"),
        _order(
            "1102",
            status="已签收",
            amount=269_900,
            sku_id="ST-60",
            spec="雾霾蓝",
            quantity=2,
            signed_days=1,
            tracking_no="SF1102002",
        ),
        _order(
            "1103",
            status="已签收",
            amount=289_900,
            category="定制婴儿推车",
            sku_id="ST-60",
            spec="雾霾蓝",
            signed_days=1,
        ),
        _order(
            "1104",
            status="已签收",
            amount=9_900,
            category="奶瓶",
            sku_id="FB-10",
            spec="奶油白",
            signed_days=1,
        ),
        # 日期倒挂：签收早于发货
        _order(
            "1105",
            status="已签收",
            amount=129_900,
            shipped_days=1,
            signed_days=3,
        ),
        _order(
            "1106",
            status="已签收",
            amount=129_900,
            created_days=15,
            shipped_days=12,
            signed_days=10,
        ),
        _order(
            "1107",
            status="已签收",
            amount=129_900,
            created_days=15,
            shipped_days=12,
            signed_days=10,
        ),
        _order(
            "1108",
            status="已发货",
            amount=289_900,
            category="定制婴儿推车",
            sku_id="ST-60",
            spec="雾霾蓝",
            shipped_days=1,
            signed_days=None,
        ),
    ]


async def main() -> int:
    if get_settings().app_env == "prod":
        print("拒绝在 prod 环境执行 seed_eval")
        return 1

    rows = eval_orders()
    async with async_session() as s, s.begin():
        await s.execute(delete(Order).where(Order.id.in_(EVAL_ORDER_IDS)))
        s.add_all(rows)

    print(f"eval orders={len(rows)}  ids={','.join(EVAL_ORDER_IDS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
