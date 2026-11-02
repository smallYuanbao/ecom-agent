"""退款资格判定（规则表 → 代码）。

**这套规则不是最终判定**——资格由模型（`judge`）判。
这里的实现用于 `compliance` 节点的**独立复算**：模型判一遍、代码判一遍，
不一致就转人工（详见 Ch03 spec「模型判定 + 代码否决」）。

依据：[电商客服-规则表.md](../../docs/design/电商客服-规则表.md) §三（10 条判定）。

**顺序即规则**：第一条命中即返回，顺序本身就是业务约定。
改顺序等于改业务，必须同步改规则表。
"""
from dataclasses import dataclass
from datetime import datetime

# 法定一律不适用（第六条）：定作 / 鲜活易腐 / 拆封音像与软件 / 报刊
NOT_APPLICABLE_KEYWORDS = ("定制", "定作", "刻字", "鲜活", "音像", "报刊")

# 第七条三类：拆封后影响安全健康（目前语料只明确了入口喂养用品）
SEVENTH_ARTICLE_KEYWORDS = ("奶瓶", "奶嘴")

WINDOW_DAYS = 7


@dataclass(frozen=True)
class OrderSnapshot:
    """判定所需的订单快照（不依赖 ORM，便于单独测试）。"""

    order_id: str                  # 订单号；只用于日志与追踪，不参与判定
    status: str                    # 6 种状态之一；规则 1–5 直接命中它
    amount: int                    # 实付金额（分），已扣优惠；算钱的基数
    quantity: int                  # 商品总件数；部分退款按「件数/总件数」均摊
    category: str                  # 商品类别（下单时快照）；第六条/第七条的前提
    packaging_opened: bool | None  # 内包装是否拆封；None = 用户没说
    created_at: datetime           # 下单时间；本条规则不用，留给价保
    shipped_at: datetime | None    # 发货时间；判「已发货未签收」与日期倒挂
    signed_at: datetime | None     # 签收时间；NULL = 未签收，7 天窗口的起算点


@dataclass(frozen=True)
class RefundRequest:
    reason_code: str           # 质量问题 quality / 不想要了 no_reason / 其他 other
    item_count: int            # 这次要退几件；部分退款按它均摊
    condition_ok: bool | None  # 商品是否完好；三态，None = 用户没说清


@dataclass(frozen=True)
class EligibilityVerdict:
    eligible: bool        # True = 可退（含拒收退款）；False = 不可退
    reason_code: str      # 结论的理由码，封闭集合：ok / reject_then_refund /
                          # out_of_window / not_applicable / not_intact / wrong_status


def _within_window(signed_at: datetime, now: datetime) -> bool:
    """7 天窗口：自签收**次日**起算，所以签收日 +7 天当天仍可退。"""
    return (now.date() - signed_at.date()).days <= WINDOW_DAYS


def _matches(category: str, keywords: tuple[str, ...]) -> bool:
    return any(word in category for word in keywords)


def judge_eligibility(
    order: OrderSnapshot,
    request: RefundRequest,
    *,
    now: datetime,
) -> EligibilityVerdict | None:
    """按规则表判定，返回 `None` 表示**代码也判不了**（信息不足）。

    返回 `None` 的两种情形：

    1. `condition_ok` 为 `None`（用户没说清完好程度）
    2. 已签收但 `signed_at` 为空（数据不一致，窗口无法起算）

    这两种在 `compliance` 里不参与"与模型对比"——代码都不知道答案，没资格否决。
    """
    status = order.status

    # ── 规则 1–3：状态不允许 ──────────────────────────────
    if status == "待付款":
        return EligibilityVerdict(False, "wrong_status")
    if status == "退款中":
        return EligibilityVerdict(False, "wrong_status")
    if status == "已关闭":
        return EligibilityVerdict(False, "wrong_status")

    # ── 规则 4：待发货，可取消（全额退）──────────────────
    if status == "待发货":
        return EligibilityVerdict(True, "ok")

    # ── 规则 5：已发货未签收 → 拒收退款 ──────────────────
    # 注意：这条排在商品类别之前，即"未签收的定作商品"也走拒收退款。
    # 这是一个未覆盖的组合，规则表 §三 的顺序如此；若要改，先改表。
    if status == "已发货" and order.signed_at is None:
        return EligibilityVerdict(True, "reject_then_refund")

    # ── 规则 6：法定一律不适用（第六条）────────────────────
    if _matches(order.category, NOT_APPLICABLE_KEYWORDS):
        return EligibilityVerdict(False, "not_applicable")

    # ── 规则 7：第七条三类（已拆封的入口喂养用品）──────────
    # 未拆封的奶瓶可以退；只有"已拆封"才不适用
    if _matches(order.category, SEVENTH_ARTICLE_KEYWORDS) and order.packaging_opened:
        return EligibilityVerdict(False, "not_applicable")

    # ── 规则 8：商品不完好 ────────────────────────────────
    if request.condition_ok is None:
        return None                       # 代码判不了，交给模型 + 低置信转人工
    if request.condition_ok is False:
        if request.reason_code == "quality":
            # 质量缺陷本身可能留下使用痕迹（异响要开机才能发现）。
            # 代码分不清"缺陷造成的痕迹"与"用户造成的损坏"，因此不否决，
            # 交由模型结合政策判断。详见 H04 的标注结论。
            return None
        return EligibilityVerdict(False, "not_intact")

    # ── 规则 9：超过 7 天（质量问题不受限）──────────────────
    if order.signed_at is None:
        return None                       # 已签收却没有签收时间 → 数据不一致
    if request.reason_code != "quality" and not _within_window(order.signed_at, now):
        return EligibilityVerdict(False, "out_of_window")

    # ── 规则 10：可退 ────────────────────────────────────
    return EligibilityVerdict(True, "ok")
