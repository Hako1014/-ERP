from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base as GlobalBase, CompanyOwnedBase as Base, TimestampMixin


class AuditFields:
    created_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    auditor_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    audit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StockIn(Base, TimestampMixin, AuditFields):
    __tablename__ = "stock_in"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    stock_in_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    stock_in_type: Mapped[str] = mapped_column(String(16), nullable=False)
    purchase_order_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_order.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StockInItem(Base):
    __tablename__ = "stock_in_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    stock_in_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("stock_in.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class StockOut(Base, TimestampMixin, AuditFields):
    __tablename__ = "stock_out"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    stock_out_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    stock_out_type: Mapped[str] = mapped_column(String(16), nullable=False)
    purchase_return_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_return.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    off_shelf_status: Mapped[str] = mapped_column(String(16), default="NONE", nullable=False)
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StockOutItem(Base):
    __tablename__ = "stock_out_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    stock_out_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("stock_out.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    location_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Transfer(Base, TimestampMixin, AuditFields):
    __tablename__ = "transfer"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    transfer_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    from_warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    to_warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    sync_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TransferItem(Base):
    __tablename__ = "transfer_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("transfer.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TransferOut(Base, TimestampMixin, AuditFields):
    __tablename__ = "transfer_out"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    transfer_out_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    transfer_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("transfer.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("warehouse.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TransferOutItem(Base):
    __tablename__ = "transfer_out_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    transfer_out_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("transfer_out.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    location_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)


class TransferIn(Base, TimestampMixin, AuditFields):
    __tablename__ = "transfer_in"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    transfer_in_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    transfer_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("transfer.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("warehouse.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TransferInItem(Base):
    __tablename__ = "transfer_in_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    transfer_in_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("transfer_in.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    location_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)


class StockCheck(Base, TimestampMixin, AuditFields):
    __tablename__ = "stock_check"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    check_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    lock_warehouse: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StockCheckItem(Base):
    __tablename__ = "stock_check_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    check_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("stock_check.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    location_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=True)
    current_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    actual_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)


class LocationAdjust(Base, TimestampMixin, AuditFields):
    __tablename__ = "location_adjust"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    adjust_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LocationAdjustItem(Base):
    __tablename__ = "location_adjust_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    adjust_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("location_adjust.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    from_location_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=False)
    to_location_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)


class Putaway(Base, TimestampMixin):
    __tablename__ = "putaway"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("warehouse.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)  # STOCK_IN/TRANSFER_IN
    source_id: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    location_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    operator_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)


class StockFlow(Base, TimestampMixin):
    __tablename__ = "stock_flow"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("warehouse.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    location_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("location.id"), nullable=True)
    unit_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("unit.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # IN/OUT
    biz_type: Mapped[str] = mapped_column(String(16), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    source_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flow_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StockWarning(Base, TimestampMixin):
    __tablename__ = "stock_warning"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    current_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    warn_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)


class StockCost(Base, TimestampMixin):
    __tablename__ = "stock_cost"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class StockSettlement(Base, TimestampMixin):
    __tablename__ = "stock_settlement"
    __table_args__ = (
        UniqueConstraint(
            "settlement_date",
            "sku_id",
            name="uq_stock_settlement_date_sku",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    settlement_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class ShopStock(Base, TimestampMixin):
    """店铺独享库存设置：按店铺 + 仓库 + 规格维护独享库存与线上同步开关。"""

    __tablename__ = "shop_stock"
    __table_args__ = (
        UniqueConstraint(
            "shop_id",
            "warehouse_id",
            "sku_id",
            name="uq_shop_stock_shop_warehouse_sku",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("shop.id"), nullable=False
    )
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=False
    )
    exclusive_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    is_sync: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    effective_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )


class StockSetting(GlobalBase, TimestampMixin):
    """库存功能选项：库存同步默认值、店铺库存设置开关、浮动成本与警戒下架。"""

    __tablename__ = "stock_setting"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_stock_setting_company_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    sync_stock_default: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    shop_stock_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    floating_cost_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    off_shelf_below_warning: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class StockCostRewriteLog(Base, TimestampMixin):
    """库存成本回写留痕：记录一次回写中被调整的单据或流水的成本前后值。"""

    __tablename__ = "stock_cost_rewrite_log"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    target_type: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # STOCK_OUT_ITEM / STOCK_FLOW
    target_id: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=True
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False)
    old_cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    new_cost_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    old_total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    new_total_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
