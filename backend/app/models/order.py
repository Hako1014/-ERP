from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, CompanyOwnedBase as Base, TimestampMixin


class Order(Base, TimestampMixin):
    __tablename__ = "order"
    __table_args__ = (
        Index("ix_order_status_audit_time", "status", "audit_status", "order_time"),
        Index("ix_order_sales_time", "pay_status", "pay_time", "order_time"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    shop_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("shop.id"), nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("warehouse.id"), nullable=True)
    logistics_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("logistics.id"), nullable=True)
    buyer_nick: Mapped[str | None] = mapped_column(String(64), nullable=True)
    buyer_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seller_remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    receiver_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receiver_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receiver_province: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receiver_city: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receiver_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="NEW", nullable=False)
    audit_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )
    pay_status: Mapped[str] = mapped_column(String(16), default="UNPAID", nullable=False)
    order_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    freight: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    platform_discount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    seller_discount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    flags: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 逗号分隔标识
    merge_order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logistics_company: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pay_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ship_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OrderItem(Base):
    __tablename__ = "order_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("order.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    sku_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    is_gift: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class OrderShipment(Base, TimestampMixin):
    """订单包裹发货记录：一个订单可拆分为多个包裹分别发货。"""

    __tablename__ = "order_shipment"
    __table_args__ = (
        Index("ix_order_shipment_order_id", "order_id"),
        Index("ix_order_shipment_tracking_no", "tracking_no"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    shipment_no: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    order_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("order.id"), nullable=False
    )
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    stock_out_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("stock_out.id"), nullable=True
    )
    package_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_type: Mapped[str] = mapped_column(
        String(16), default="EXPRESS", nullable=False
    )  # EXPRESS/SELF_PICKUP
    source: Mapped[str] = mapped_column(
        String(16), default="SCAN", nullable=False
    )  # SCAN/ONE_CLICK/SELF_PICKUP
    status: Mapped[str] = mapped_column(
        String(16), default="SCANNING", nullable=False
    )  # SCANNING/SHIPPED/CANCELLED
    expected_weight_g: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    actual_weight_g: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    weight_tolerance_g: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    weight_out_of_range: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    weight_overridden: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    platform_sync_status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False
    )  # PENDING/SYNCED/FAILED/SKIPPED
    platform_sync_message: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    shipped_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OrderShipmentItem(Base, TimestampMixin):
    """包裹商品明细，按订单明细汇总同一包裹内的扫描数量。"""

    __tablename__ = "order_shipment_item"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id",
            "order_item_id",
            name="uq_order_shipment_item_shipment_order_item",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("order_shipment.id"), nullable=False
    )
    order_item_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("order_item.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ReturnOrder(Base, TimestampMixin):
    __tablename__ = "return_order"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    return_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    order_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("order.id"), nullable=False)
    shop_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("shop.id"), nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("warehouse.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    return_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    logistics_company: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_arrived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    freight: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    is_rejected_sign: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    return_person: Mapped[str | None] = mapped_column(String(64), nullable=True)
    goods_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    need_return_goods: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    stock_in_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("stock_in.id"), nullable=True
    )
    audit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReturnOrderItem(Base):
    __tablename__ = "return_order_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    return_order_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("return_order.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)


class ExchangeOrder(Base, TimestampMixin):
    __tablename__ = "exchange_order"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    exchange_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    order_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("order.id"), nullable=False)
    shop_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("shop.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    exchange_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_cod: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_return: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    compensate_order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compensate_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    compensate_direction: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )


class ExchangeOrderItem(Base):
    __tablename__ = "exchange_order_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    exchange_order_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("exchange_order.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("product.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("product_sku.id"), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    exchange_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)


class ReturnRegister(Base, TimestampMixin):
    __tablename__ = "return_register"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    return_order_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("return_order.id"), nullable=True
    )
    exchange_order_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("exchange_order.id"), nullable=True
    )
    logistics_company: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sender_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WaveStrategy(Base, TimestampMixin):
    __tablename__ = "wave_strategy"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    logistics_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("logistics.id"), nullable=True)
    warehouse_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("warehouse.id"), nullable=True)
    shop_ids: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wave_order_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    load_no_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sort_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Wave(Base, TimestampMixin):
    __tablename__ = "wave"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    wave_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    strategy_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("wave_strategy.id"), nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    express_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_order_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)


class WaveOrder(Base, TimestampMixin):
    """波次与订单关联；一单只能进入一个波次。"""

    __tablename__ = "wave_order"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_wave_order_order_id"),
        Index("ix_wave_order_wave_id", "wave_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    wave_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("wave.id"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("order.id"), nullable=False
    )
    load_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sort_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LoadNo(Base):
    __tablename__ = "load_no"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    wave_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("wave.id"), nullable=False)
    load_no: Mapped[str] = mapped_column(String(64), nullable=False)
    express_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PrintTemplate(Base, TimestampMixin):
    __tablename__ = "print_template"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    template_type: Mapped[str] = mapped_column(String(32), nullable=False)
    logistics_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("logistics.id"), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_width: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    paper_height: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Waybill(Base, TimestampMixin):
    __tablename__ = "waybill"
    __table_args__ = (Index("ix_waybill_batch_no", "batch_no"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("order.id"), nullable=False)
    waybill_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    print_type: Mapped[str] = mapped_column(String(16), default="THERMAL", nullable=False)
    batch_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_printed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PickList(Base, TimestampMixin):
    __tablename__ = "pick_list"
    __table_args__ = (Index("ix_pick_list_batch_no", "batch_no"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    wave_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("wave.id"), nullable=True)
    load_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeliveryList(Base, TimestampMixin):
    __tablename__ = "delivery_list"
    __table_args__ = (Index("ix_delivery_list_batch_no", "batch_no"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    wave_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("wave.id"), nullable=True)
    load_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
