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


class Product(Base, TimestampMixin):
    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("code", name="uq_product_code"),
        Index("ix_product_art_no", "art_no"),
        Index("ix_product_barcode", "barcode"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    art_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("category.id"), nullable=True
    )
    brand_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("brand.id"), nullable=True
    )
    default_supplier_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("supplier.id"), nullable=True
    )
    measure_type: Mapped[str] = mapped_column(String(16), default="SINGLE", nullable=False)
    unit_group_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("unit_group.id"), nullable=True
    )
    main_unit_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("unit.id"), nullable=True
    )
    online_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tag_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    min_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warn_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    tax_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_gift: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    use_float_cost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ProductSku(Base, TimestampMixin):
    __tablename__ = "product_sku"
    __table_args__ = (
        UniqueConstraint("spec_code", name="uq_product_sku_spec_code"),
        UniqueConstraint("barcode", name="uq_product_sku_barcode"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    spec_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    spec_code: Mapped[str] = mapped_column(String(64), nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tag_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ComboProduct(Base, TimestampMixin):
    """组合商品：主商品 + 配件清单。

    规则（2026-09-23 需求确认）：不允许嵌套组合；销售时按组成项逐项扣库存与核算成本；
    不参与采购/入库/调拨；打印时配件金额不计入小计与合计。
    """

    __tablename__ = "combo_product"
    __table_args__ = (UniqueConstraint("combo_no", name="uq_combo_product_combo_no"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    combo_no: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    main_product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    main_sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )


class ComboProductItem(Base):
    __tablename__ = "combo_product_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    combo_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("combo_product.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product.id"), nullable=False
    )
    sku_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=True
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
