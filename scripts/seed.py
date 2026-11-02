"""业务数据种子：6 SKU / 20 订单 / 5 券（+ 若干物流轨迹）。

**全部是合成数据**，与真实平台无关（数据卫生约束）。

幂等策略：先清空 4 张业务表再插入。`conversations / messages` 属于运行时数据，
不在清理范围。生产环境拒绝执行。

数据设计要点（对应 Ch01 plan Task 7）：
- 6 个 SKU = 3 系列 × 2 型号，含 1 个无货、1 个预售
- 20 笔订单覆盖全部 6 种状态，每人 3–5 单，含超 7 天的单（退费超期考点）
- 5 张券含：已过期 / 未达门槛 / 已使用
- 「不可叠加」是知识库里的政策规则，不是券表字段，故 seed 不体现

订单号前缀即归属：`1xxx` → u1，`2xxx` → u2 …（u2 的单专供越权测试）。
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/seed.py` 直接跑

from sqlalchemy import delete

from app.config import get_settings
from app.db.models import Coupon, Logistics, Order, Stock
from app.db.session import async_session

# 固定"现在"，让相对时间（7 天退货期、价保期）在每次 seed 后都可复现
NOW = datetime(2026, 10, 28, 9, 0)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


# ---------- 6 个 SKU ----------

STOCK = [
    # sku_id, 规格, 库存, 售价(分), 状态
    ("ST-20", "石墨灰", 42, 129_900, "on_sale"),      # 婴儿推车 · 轻便伞车
    ("ST-60", "雾霾蓝", 18, 269_900, "on_sale"),      # 婴儿推车 · 高景观
    ("CS-04", "星光灰", 0, 189_900, "sold_out"),      # 安全座椅 0–18kg（无货）
    ("CS-12", "深空黑", 25, 229_900, "on_sale"),      # 安全座椅 9–36kg
    ("FB-10", "奶油白", 60, 59_900, "on_sale"),       # 辅食机 基础款
    ("FB-30", "薄荷绿", 0, 89_900, "presale"),        # 辅食机 蒸煮一体（预售）
]

PRICE = {sku: price for sku, _spec, _stock, price, _status in STOCK}

# 商品类别：下单时快照进 orders.category。
# 不从 sku 前缀推——定制款（刻字）与奶瓶都没有专属前缀，
# 类目是业务数据，应该显式维护。
SKU_CATEGORY = {
    "ST-20": "婴儿推车",
    "ST-60": "婴儿推车",
    "CS-04": "安全座椅",
    "CS-12": "安全座椅",
    "FB-10": "辅食机",
    "FB-30": "辅食机",
}


# ---------- 20 笔订单 ----------
# user_id, order_id, sku, 规格, 状态, 下单距今天数, 物流单号

ORDERS = [
    # u1（主角）：5 单，含 1 单已超 7 天（1001）——退货超期/价保的考点
    ("u1", "1001", "ST-60", "雾霾蓝", "已签收", 30, "SF1001001"),
    ("u1", "1002", "ST-20", "石墨灰", "已发货", 3, "SF1001002"),
    ("u1", "1003", "CS-04", "星光灰", "待发货", 1, None),
    ("u1", "1004", "FB-10", "奶油白", "已签收", 12, "YT1001004"),
    ("u1", "1005", "ST-20", "石墨灰", "已关闭", 40, None),
    # u2：4 单，越权测试用
    ("u2", "2001", "ST-60", "雾霾蓝", "已签收", 5, "SF2002001"),
    ("u2", "2002", "CS-12", "深空黑", "退款中", 8, "JD2002002"),
    ("u2", "2003", "FB-30", "薄荷绿", "待付款", 0, None),
    ("u2", "2004", "ST-20", "石墨灰", "已发货", 4, "SF2002004"),
    # u3：4 单
    ("u3", "3001", "CS-04", "星光灰", "已签收", 20, "YT3003001"),
    ("u3", "3002", "ST-60", "雾霾蓝", "已发货", 1, "SF3003002"),
    ("u3", "3003", "FB-10", "奶油白", "已关闭", 60, None),
    ("u3", "3004", "CS-12", "深空黑", "待发货", 1, None),
    # u4：4 单
    ("u4", "4001", "FB-30", "薄荷绿", "已签收", 15, "YT4004001"),
    ("u4", "4002", "ST-20", "石墨灰", "退款中", 9, "SF4004002"),
    ("u4", "4003", "CS-04", "星光灰", "待付款", 0, None),
    ("u4", "4004", "ST-60", "雾霾蓝", "已签收", 45, "SF4004004"),
    # u5：3 单
    ("u5", "5001", "CS-12", "深空黑", "已发货", 3, "JD5005001"),
    ("u5", "5002", "FB-10", "奶油白", "已签收", 25, "YT5005002"),
    ("u5", "5003", "ST-20", "石墨灰", "待发货", 1, None),
]

# 优惠抵扣（分）：让部分订单实付低于标价，退费计算才有区分度
DISCOUNT = {"1002": 5_000, "2001": 20_000, "4001": 8_000}

# 件数：多数为 1；挑三单为 2，用于「部分退款」用例
QUANTITY = {"1002": 2, "2003": 2, "4002": 2}


# ---------- 物流：4 种典型形态 ----------
# 订单号 → (最新状态, 轨迹节点)
LOGISTICS = {
    # 正常运输
    "1002": (
        "运输中",
        [
            {"time": "10-26 09:12", "status": "已揽收", "desc": "杭州转运中心已收件"},
            {"time": "10-27 18:40", "status": "运输中", "desc": "已到达上海分拨中心"},
        ],
    ),
    # 停滞超 3 天（Ch04 要能识别并给出对应话术）
    "2004": (
        "运输中",
        [
            {"time": "10-24 10:05", "status": "已揽收", "desc": "广州转运中心已收件"},
            {"time": "10-24 22:30", "status": "运输中", "desc": "已到达长沙分拨中心"},
        ],
    ),
    # 已签收未收到（用户声称没收到）
    "3002": (
        "已签收",
        [
            {"time": "10-27 11:20", "status": "派送中", "desc": "快递员正在派送"},
            {"time": "10-27 15:02", "status": "已签收", "desc": "已存放至丰巢柜"},
        ],
    ),
    # 异常件
    "5001": (
        "异常",
        [
            {"time": "10-26 08:30", "status": "已揽收", "desc": "北京转运中心已收件"},
            {"time": "10-26 20:11", "status": "异常", "desc": "包裹破损，已退回始发站"},
        ],
    ),
    "1001": (
        "已签收",
        [
            {"time": "09-30 09:00", "status": "已揽收", "desc": "杭州转运中心已收件"},
            {"time": "10-01 14:22", "status": "已签收", "desc": "本人签收"},
        ],
    ),
    "2001": (
        "已签收",
        [
            {"time": "10-25 09:00", "status": "已揽收", "desc": "深圳转运中心已收件"},
            {"time": "10-26 16:10", "status": "已签收", "desc": "本人签收"},
        ],
    ),
}


# ---------- 5 张券 ----------
# user_id, 券码, 门槛(分), 优惠(分), 距今过期天数（负数=已过期）, 是否已用

COUPONS = [
    ("u1", "NEW50", 30_000, 5_000, 10, 0),        # 满 300 减 50，可用
    ("u1", "EXPIRED20", 10_000, 2_000, -5, 0),    # 满 100 减 20，**已过期**
    ("u2", "BIG200", 200_000, 20_000, 30, 0),     # 满 2000 减 200，可用
    ("u3", "USED10", 10_000, 1_000, 20, 1),       # **已使用**
    ("u4", "HIGH500", 500_000, 50_000, 15, 0),    # 满 5000 减 500，**未达门槛**
]


def _order_rows(now: datetime = NOW) -> list[Order]:
    rows: list[Order] = []
    for user_id, oid, sku, spec, status, days, tracking in ORDERS:
        created = now - timedelta(days=days)
        paid = None if status == "待付款" else created + timedelta(minutes=15)
        shipped = (
            created + timedelta(hours=20)
            if status in {"已发货", "已签收", "退款中"}
            else None
        )
        # 签收时间：只有「已签收 / 退款中」才有值；未签收保持 NULL（7 天窗口由此起算）
        signed = (
            shipped + timedelta(days=2)
            if shipped is not None and status in {"已签收", "退款中"}
            else None
        )
        rows.append(
            Order(
                id=oid,
                user_id=user_id,
                sku_id=sku,
                spec=spec,
                category=SKU_CATEGORY[sku],
                status=status,
                amount=PRICE[sku] - DISCOUNT.get(oid, 0),
                created_at=created,
                paid_at=paid,
                shipped_at=shipped,
                signed_at=signed,
                quantity=QUANTITY.get(oid, 1),
                tracking_no=tracking,
            )
        )
    return rows


def _logistics_rows(orders: list[Order]) -> list[Logistics]:
    by_id = {o.id: o for o in orders}
    rows: list[Logistics] = []
    for oid, (latest, nodes) in LOGISTICS.items():
        order = by_id[oid]
        rows.append(
            Logistics(
                tracking_no=order.tracking_no,
                order_id=oid,
                latest_status=latest,
                nodes=json.dumps(nodes, ensure_ascii=False),
                updated_at=order.shipped_at,
            )
        )
    return rows


def _coupon_rows(now: datetime = NOW) -> list[Coupon]:
    return [
        Coupon(
            user_id=user_id,
            code=code,
            threshold=threshold,
            discount=discount,
            expires_at=now + timedelta(days=expires_in),
            used=used,
        )
        for user_id, code, threshold, discount, expires_in, used in COUPONS
    ]


async def main() -> int:
    if get_settings().app_env == "prod":
        print("拒绝在 prod 环境执行 seed（会清空业务表）")
        return 1

    async with async_session() as s, s.begin():
        # 先删子表再删父表，避免外键阻挡
        await s.execute(delete(Logistics))
        await s.execute(delete(Order))
        await s.execute(delete(Stock))
        await s.execute(delete(Coupon))

        stock_rows = [
            Stock(sku_id=sku, spec=spec, stock=stock, price=price, status=status)
            for sku, spec, stock, price, status in STOCK
        ]
        order_rows = _order_rows()
        s.add_all(stock_rows)
        s.add_all(order_rows)
        await s.flush()  # 订单先落库，物流才能引用 orders.id
        s.add_all(_logistics_rows(order_rows))
        s.add_all(_coupon_rows())

    print(f"stock={len(STOCK)}  orders={len(ORDERS)}  "
          f"logistics={len(LOGISTICS)}  coupons={len(COUPONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
