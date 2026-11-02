"""退款金额计算（纯函数）。

只做一件事：把「可退」翻译成一个精确的数字（单位：分）。
资格由 `judge` 判，这里不判——`eligible=False` 时直接返回 0。

**纯函数约束**（spec「compute」）：无 IO、无随机、无时间依赖。
金额必须能逐条对拍，混进 `now()` 或数据库查询就复现不了。

依据：docs/design/电商客服-规则表.md §四（金额计算）。
"""
from dataclasses import dataclass

from app.rules.eligibility import OrderSnapshot, RefundRequest


@dataclass(frozen=True)
class Judgement:
    """`judge` 通过置信度门槛后的结论。

    只保留 compute 需要的两个字段。`confidence` 在 `judge` 节点内就已用于分流
    （低于阈值直接转人工），不传进来——否则 compute 会依赖一个它不该关心的值。

    字段与 `EligibilityVerdict` 同构，所以 `compliance` 可以直接拿两者比较。
    """

    eligible: bool
    reason_code: str


def compute_amount(
    order: OrderSnapshot,
    judgement: Judgement,
    request: RefundRequest,
) -> int:
    """返回应退金额（分）。

    规则表 §4.1：

    | 情形 | 计算 |
    |---|---|
    | 不可退 | 0 |
    | 待发货取消 | 全额（整单取消） |
    | 已发货未签收（`reject_then_refund`） | 全额（用户没见过货，没有"退几件"的概念） |
    | 整单退货 | 全额 |
    | 部分退货 | `floor(实付 × 退货件数 / 总件数)` |

    `floor` 用整数地板除实现：金额在数据库里是**分**，不能出现小数分。

    **待发货为什么是全额而不是按件摊**：取消的对象是整张订单，不是其中几件。
    "取消订单里的部分商品"是另一个业务（部分取消），当前没有建模。
    """
    if not judgement.eligible:
        return 0

    # 这两条是"整单退"：用户要么还没见到货，要么整单取消
    if judgement.reason_code == "reject_then_refund":
        return order.amount
    if order.status == "待发货":
        return order.amount

    # 数据异常（总件数 ≤ 0）不该让纯函数崩——上游 validate 负责拦，
    # 这里只需要给出一个不会误导的值。
    if order.quantity <= 0:
        return 0

    # 件数越界按总件数封顶；退 0 件不退款
    count = max(0, min(request.item_count, order.quantity))
    return order.amount * count // order.quantity
