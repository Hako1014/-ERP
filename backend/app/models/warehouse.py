from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, CompanyOwnedBase as Base, TimestampMixin


class Warehouse(Base, TimestampMixin):
    __tablename__ = "warehouse"
    __table_args__ = (UniqueConstraint("code", name="uq_warehouse_code"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    warehouse_type: Mapped[str] = mapped_column(
        String(16), default="FINISHED", nullable=False
    )  # FINISHED/DEFECTIVE
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enable_location: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    zero_stock_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_virtual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ship_regions: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lock_type: Mapped[str] = mapped_column(
        String(16), default="NONE", nullable=False
    )  # NONE/MANUAL/CHECK
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Zone(Base, TimestampMixin):
    __tablename__ = "zone"
    __table_args__ = (UniqueConstraint("warehouse_id", "code", name="uq_zone_warehouse_code"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Location(Base, TimestampMixin):
    __tablename__ = "location"
    __table_args__ = (UniqueConstraint("code", name="uq_location_code"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("warehouse.id"), nullable=False
    )
    zone_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("zone.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    row_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    col_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    layer_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = 不限
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_virtual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StockLocation(Base, TimestampMixin):
    __tablename__ = "stock_location"
    __table_args__ = (
        UniqueConstraint("location_id", "product_sku_id", "status", name="uq_stock_location"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("location.id"), nullable=False
    )
    product_sku_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("product_sku.id"), nullable=False
    )
    qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="NORMAL", nullable=False
    )  # NORMAL/DEFECTIVE
