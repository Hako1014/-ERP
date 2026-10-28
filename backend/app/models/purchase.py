from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base as GlobalBase, CompanyOwnedBase as Base, TimestampMixin


class AuditFields:
    """单据通用制单/审核留痕字段。"""

    created_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    auditor_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    audit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PurchasePlan(Base, TimestampMixin, AuditFields):
    __tablename__ = "purchase_plan"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    plan_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=False
    )
    plan_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="IN_PROGRESS", nullable=False
    )  # IN_PROGRESS/ABORTED/FINISHED
    audit_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )  # PENDING/APPROVED/REJECTED
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PurchasePlanItem(Base):
    __tablename__ = "purchase_plan_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_plan.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    planned_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    purchased_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class PurchasePlanAttachment(Base, TimestampMixin):
    """按采购计划业务权限保护的附件副本；不依赖私人会话附件生命周期。"""

    __tablename__ = "purchase_plan_attachment"
    __table_args__ = (
        Index("ix_purchase_plan_attachment_plan", "company_id", "plan_id"),
        UniqueConstraint("storage_key", name="uq_purchase_plan_attachment_storage_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("purchase_plan.id"), nullable=False)
    created_by: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=False)
    source_assistant_attachment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assistant_attachment.id", ondelete="SET NULL")
    )
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False)


class PurchasePlanCreationRequest(GlobalBase, TimestampMixin):
    """采购计划创建幂等请求；请求记录与实际单据结果分事务阶段持久化。"""

    __tablename__ = "purchase_plan_creation_request"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "staff_id", "request_id", name="uq_purchase_plan_creation_scope"
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    staff_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_plan.id"), nullable=True
    )
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PurchaseOrder(Base, TimestampMixin, AuditFields):
    __tablename__ = "purchase_order"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    plan_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_plan.id"), nullable=True
    )
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=False
    )
    warehouse_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=True
    )
    purchaser_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )  # PENDING/APPROVED/TERMINATED/RECEIVING/RECEIVED/CLOSED
    audit_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )  # PENDING/APPROVED/REJECTED
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    arrived_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    prepay_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_order.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    brand_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("brand.id"), nullable=True
    )
    unit_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("unit.id"), nullable=True
    )
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    in_transit_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    arrived_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class PurchasePrepay(Base, TimestampMixin, AuditFields):
    __tablename__ = "purchase_prepay"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    prepay_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_order.id"), nullable=True
    )
    order_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    prepay_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    write_off_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    bank: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    pay_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    write_off_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )  # PENDING/APPROVED/REJECTED/TERMINATED
    audit_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )


class PurchaseReturn(Base, TimestampMixin, AuditFields):
    __tablename__ = "purchase_return"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    return_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_order.id"), nullable=True
    )
    warehouse_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=True
    )
    auto_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    return_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    returned_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )  # PENDING/FINANCE_APPROVED/APPROVED/REJECTED
    audit_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )


class PurchaseReturnItem(Base):
    __tablename__ = "purchase_return_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    return_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_return.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    unit_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("unit.id"), nullable=True
    )
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    return_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class PurchaseInvoice(Base, TimestampMixin, AuditFields):
    __tablename__ = "purchase_invoice"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    invoice_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=False
    )
    invoice_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    write_off_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    write_off_status: Mapped[str] = mapped_column(
        String(16), default="NONE", nullable=False
    )  # NONE/PARTIAL/FULL
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    voucher_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )
    stock_in_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_allocation: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_accounted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
