from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, CompanyOwnedBase as Base, TimestampMixin


class Supplier(Base, TimestampMixin):
    __tablename__ = "supplier"
    __table_args__ = (UniqueConstraint("code", name="uq_supplier_code"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settlement_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Customer(Base, TimestampMixin):
    __tablename__ = "customer"
    __table_args__ = (UniqueConstraint("code", name="uq_customer_code"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settlement_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Account(Base, TimestampMixin):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    supplier_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("customer.id"), nullable=True
    )
    bank: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bank_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0.00"), nullable=False
    )
    kingdee_account_code: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    kingdee_sync_status: Mapped[str] = mapped_column(
        String(16), default="NOT_SYNCED", nullable=False
    )
    kingdee_sync_message: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    balance_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )


class SupplierPrice(Base, TimestampMixin):
    __tablename__ = "supplier_price"
    __table_args__ = (
        UniqueConstraint("supplier_id", "product_id", "sku_id", name="uq_supplier_price"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
