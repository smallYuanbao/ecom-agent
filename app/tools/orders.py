"""B 出口的只读查询工具。

**这里的 "tool" 不是模型选择的工具**：图按意图直接调用，模型不参与选择。
它是一层薄封装，统一错误语义，让图里只做分支、不写 try/except。

错误码（D9）：

| code | 含义 |
|---|---|
| `not_found` | 查不到 / 不是本人的——两者共用，防枚举 |
| `invalid_arg` | 参数缺失 |
| `timeout` / `internal` | 系统问题 |
"""
import json
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.errors import NotFound
from app.db.models import Order
from app.db.repo_order import (
    get_order,
    get_stock,
    get_tracking,
    list_stock,
    list_stock_by_sku,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolResult:
    """工具的返回形状：图里只读 `code` / `data`，不捕获异常。"""

    ok: bool
    code: str | None = None
    data: dict | None = None


def _order_data(order: Order) -> dict:
    return {
        "order_id": order.id,
        "status": order.status,
        "amount": order.amount,
        "quantity": order.quantity,
        "created_at": order.created_at.isoformat(),
        "tracking_no": order.tracking_no,
    }


def _stock_row(row) -> dict:
    return {
        "sku_id": row.sku_id,
        "spec": row.spec,
        "stock": row.stock,
        "price": row.price,
        "status": row.status,
    }


def _parse_nodes(raw: str | None) -> list[dict]:
    """物流轨迹是 JSON 文本；坏数据降级为空列表，不让整轮失败。"""
    if not raw:
        return []
    try:
        nodes = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("logistics.nodes 不是合法 JSON，降级为空轨迹")
        return []
    return nodes if isinstance(nodes, list) else []


async def query_order(
    order_id: str, *, user_id: str, session: AsyncSession | None = None
) -> ToolResult:
    """查订单状态 / 金额 / 下单时间。不存在与越权返回同一个 `not_found`。"""
    if not order_id:
        return ToolResult(False, "invalid_arg")
    try:
        order = await get_order(order_id, user_id=user_id, session=session)
    except NotFound:
        return ToolResult(False, "not_found")
    except TimeoutError:
        return ToolResult(False, "timeout")
    except Exception:
        logger.warning("query_order 失败 order_id=%s", order_id, exc_info=True)
        return ToolResult(False, "internal")
    return ToolResult(True, None, _order_data(order))


async def query_logistics(
    tracking_no: str, *, user_id: str, session: AsyncSession | None = None
) -> ToolResult:
    """按物流单号查轨迹。归属校验在 SQL 层（join orders）。"""
    if not tracking_no:
        return ToolResult(False, "invalid_arg")
    try:
        row = await get_tracking(tracking_no=tracking_no, user_id=user_id, session=session)
    except TimeoutError:
        return ToolResult(False, "timeout")
    except Exception:
        logger.warning("query_logistics 失败 tracking_no=%s", tracking_no, exc_info=True)
        return ToolResult(False, "internal")
    if row is None:
        return ToolResult(False, "not_found")
    return ToolResult(
        True,
        None,
        {
            "tracking_no": row.tracking_no,
            "latest_status": row.latest_status,
            "nodes": _parse_nodes(row.nodes),
        },
    )


async def query_stock(
    sku_id: str,
    spec: str | None = None,
    *,
    session: AsyncSession | None = None,
) -> ToolResult:
    """查库存与价格。

    - `spec` 不给：返回该型号的全部规格（供调用方匹配原话）
    - `spec` 给了：精确查一行
    """
    if not sku_id:
        return ToolResult(False, "invalid_arg")
    try:
        if spec is None:
            rows = await list_stock_by_sku(sku_id, session=session)
            if not rows:
                return ToolResult(False, "not_found")
            return ToolResult(
                True, None, {"sku_id": sku_id, "rows": [_stock_row(r) for r in rows]}
            )
        row = await get_stock(sku_id=sku_id, spec=spec, session=session)
    except TimeoutError:
        return ToolResult(False, "timeout")
    except Exception:
        logger.warning("query_stock 失败 sku_id=%s spec=%s", sku_id, spec, exc_info=True)
        return ToolResult(False, "internal")

    if row is None:
        return ToolResult(False, "not_found")
    return ToolResult(True, None, _stock_row(row))


async def list_stock_skus(*, session: AsyncSession | None = None) -> ToolResult:
    """列出库里全部型号，缺型号时给用户选。"""
    try:
        rows = await list_stock(session=session)
    except TimeoutError:
        return ToolResult(False, "timeout")
    except Exception:
        logger.warning("list_stock_skus 失败", exc_info=True)
        return ToolResult(False, "internal")

    skus = sorted({row.sku_id for row in rows})
    if not skus:
        return ToolResult(False, "not_found")
    return ToolResult(True, None, {"skus": skus})
