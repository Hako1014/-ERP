from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, CompanyOwnedBase as Base, TimestampMixin


class Platform(Base, TimestampMixin):
    __tablename__ = "platform"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Shop(Base, TimestampMixin):
    __tablename__ = "shop"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    platform_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("platform.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    login_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("supplier.id"), nullable=True)
    default_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_sync_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_sync_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    download_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sms_ship_template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sms_deliver_template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enable_logistics_params: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    message_match: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ShopApi(Base, TimestampMixin):
    __tablename__ = "shop_api"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("shop.id"), nullable=False)
    api_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verify_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    app_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    app_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Logistics(Base, TimestampMixin):
    __tablename__ = "logistics"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    company: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FreightTemplate(Base, TimestampMixin):
    __tablename__ = "freight_template"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    logistics_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("logistics.id"), nullable=False)
    charge_type: Mapped[str] = mapped_column(String(16), default="BY_COUNT", nullable=False)
    first_count: Mapped[int] = mapped_column(default=1, nullable=False)
    continue_count: Mapped[int] = mapped_column(default=1, nullable=False)
    first_weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    continue_weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    base_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    continue_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    deduct_weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    operation_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    consumable_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    ewaybill_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    tax_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    tax_point: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)


class AreaRule(Base, TimestampMixin):
    __tablename__ = "area_rule"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    logistics_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("logistics.id"), nullable=False)
    province: Mapped[str | None] = mapped_column(String(32), nullable=True)
    city: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_weight_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    match_weight_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    freight_template_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("freight_template.id"), nullable=True
    )
