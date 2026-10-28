from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, CompanyOwnedBase as Base, TimestampMixin


class Category(Base, TimestampMixin):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("category.id"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Brand(Base, TimestampMixin):
    __tablename__ = "brand"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Unit(Base, TimestampMixin):
    __tablename__ = "unit"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)


class UnitGroup(Base, TimestampMixin):
    __tablename__ = "unit_group"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    main_unit_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("unit.id"), nullable=False
    )


class UnitGroupItem(Base):
    __tablename__ = "unit_group_item"
    __table_args__ = (
        UniqueConstraint("unit_group_id", "unit_id", name="uq_unit_group_item_group_unit"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    unit_group_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("unit_group.id"), nullable=False
    )
    unit_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("unit.id"), nullable=False)
    # 该单位相对主单位的换算系数（1 主单位 = conversion_rate 该单位）。
    conversion_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)


class SpecType(Base, TimestampMixin):
    __tablename__ = "spec_type"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("category.id"), nullable=True
    )


class SpecValue(Base, TimestampMixin):
    __tablename__ = "spec_value"
    __table_args__ = (
        UniqueConstraint("spec_type_id", "code", name="uq_spec_value_type_code"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    spec_type_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("spec_type.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
