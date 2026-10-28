"""10 张表的 ORM 模型。

与 `app/db/schema.sql` 一一对应：schema.sql 是可审阅的 DDL，这里是运行时真源。
**改字段必须两边一起改**（Ch01 spec 决策记录）。
字段释义见 `docs/superpowers/specs/ch01-data-foundation-design.md`「数据模型」。

约定：
- 时间一律 `DateTime`（naive UTC），应用层写入
- JSON 用 `Text` 存序列化字符串（SQLite 无原生 JSON 类型）
- 金额单位为「分」（整数）
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    """naive UTC——SQLite 不存时区，统一用 UTC 墙钟时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


_created_at = {"default": utcnow, "server_default": text("CURRENT_TIMESTAMP")}


# ============ 系统表 ============


class Conversation(Base):
    """会话：一次对话的容器，同时是图的 thread_id 与跨轮状态锚点。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)          # S2 会话归属
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, **_created_at)
    updated_at: Mapped[datetime] = mapped_column(                         # C4 乐观锁 / M7 超时
        DateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)         # K5 软删
    summary: Mapped[str | None] = mapped_column(Text)                     # M2 滚动摘要
    summary_upto_msg_id: Mapped[int | None] = mapped_column(Integer)      # M2 摘要覆盖到哪
    active_flow: Mapped[str | None] = mapped_column(String)               # M4 refund / price_protection
    pending_question: Mapped[str | None] = mapped_column(String)          # M4 order_id / confirm
    draft: Mapped[str | None] = mapped_column(Text)                       # M4 JSON 已收集要素


class Message(Base):
    """消息：完整对话记录，用于回放、审计、窗口构建。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)             # user / assistant / tool
    content: Mapped[str | None] = mapped_column(Text)                     # tool 消息可为空
    tool_calls: Mapped[str | None] = mapped_column(Text)                  # JSON
    tool_call_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)                    # C1 completed/cancelled/error
    client_message_id: Mapped[str | None] = mapped_column(String)         # K4 幂等键
    meta: Mapped[str | None] = mapped_column(Text)                        # JSON intent/route/citation_ids
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, **_created_at)

    __table_args__ = (
        Index("idx_msg_conv", "conversation_id", "created_at"),
        # K4 部分索引：只对带幂等键的消息去重；多个 NULL 不算冲突
        Index(
            "uk_msg_idem",
            "conversation_id",
            "client_message_id",
            unique=True,
            sqlite_where=text("client_message_id IS NOT NULL"),
        ),
    )


class Ticket(Base):
    """工单：转人工时创建；request_id 保证一次请求只建一张。"""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String)
    ticket_type: Mapped[str | None] = mapped_column(String)              # 投诉 / 退款 / 咨询
    description: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String, nullable=False)      # D11 幂等键
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="open", server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, **_created_at)

    __table_args__ = (Index("uk_ticket_idem", "request_id", unique=True),)


class TraceSpan(Base):
    """调用链：一次请求内每个节点/工具一行（对齐 OpenTelemetry span 模型）。"""

    __tablename__ = "trace_span"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String, nullable=False)        # L7
    span_id: Mapped[str] = mapped_column(String, nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, nullable=False)            # node:compute / tool:query_order
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    end_at: Mapped[datetime | None] = mapped_column(DateTime)            # NULL = 未结束/异常中断
    attrs: Mapped[str | None] = mapped_column(Text)                      # JSON tokens/结果/错误码

    __table_args__ = (Index("idx_span_trace", "trace_id"),)


class Faq(Base):
    """飞轮写回的知识：审核通过的低置信问题沉淀成的问答条目。"""

    __tablename__ = "faq"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)     # flywheel / manual
    source_question_id: Mapped[int | None] = mapped_column(Integer)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)       # NULL = 未经人工确认
    kb_version: Mapped[str | None] = mapped_column(String)               # K6
    sync_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, **_created_at)


class LowConfQuestion(Base):
    """低置信池：飞轮入口，记录「系统答不好」的问题与当时证据。"""

    __tablename__ = "low_conf_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    normalized: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String)                   # retrieval_low_conf / feedback / rule_conflict
    reason: Mapped[str | None] = mapped_column(Text)
    retrieved: Mapped[str | None] = mapped_column(Text)                  # 三态：[] / [...] / NULL
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    merged_into: Mapped[int | None] = mapped_column(Integer)
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    kb_ref: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, **_created_at)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


# ============ 业务表 ============
# 落表而非 mock 函数：归属校验要在 SQL 层完成（Ch01 spec 决策记录）


class Order(Base):
    """订单：归属校验的载体，也是退费计算的输入。"""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)            # 订单号，如 1002
    user_id: Mapped[str] = mapped_column(String, nullable=False)         # S1 归属人
    sku_id: Mapped[str] = mapped_column(String, nullable=False)
    spec: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)          # 待付款/待发货/已发货/已签收/退款中/已关闭
    amount: Mapped[int] = mapped_column(Integer, nullable=False)         # 实付（分）
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)   # 价保起算
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime)
    tracking_no: Mapped[str | None] = mapped_column(String)

    __table_args__ = (Index("idx_order_user", "user_id"),)


class Logistics(Base):
    """物流：B 出口查询轨迹。"""

    __tablename__ = "logistics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracking_no: Mapped[str] = mapped_column(String, nullable=False)
    order_id: Mapped[str] = mapped_column(
        String, ForeignKey("orders.id"), nullable=False
    )
    latest_status: Mapped[str] = mapped_column(String, nullable=False)
    nodes: Mapped[str | None] = mapped_column(Text)                      # JSON [{time,status,desc}]
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("idx_logistics_no", "tracking_no"),)


class Stock(Base):
    """库存与价格：B 出口答「有没有货」「多少钱」。"""

    __tablename__ = "stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_id: Mapped[str] = mapped_column(String, nullable=False)
    spec: Mapped[str] = mapped_column(String, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    price: Mapped[int] = mapped_column(Integer, nullable=False)          # 分
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="on_sale", server_default="on_sale"
    )

    __table_args__ = (UniqueConstraint("sku_id", "spec", name="uq_stock_sku_spec"),)


class Coupon(Base):
    """优惠券：A 出口回答券规则；规则判断门槛与过期。"""

    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    discount: Mapped[int] = mapped_column(Integer, nullable=False)       # 分
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (Index("idx_coupon_user", "user_id"),)
