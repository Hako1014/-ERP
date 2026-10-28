from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base, TimestampMixin


class Company(Base, TimestampMixin):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legal_person: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tax_no: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Department(Base, TimestampMixin):
    __tablename__ = "department"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("department.id"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Position(Base, TimestampMixin):
    __tablename__ = "position"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Staff(Base, TimestampMixin):
    __tablename__ = "staff"
    __table_args__ = (
        UniqueConstraint("staff_no", name="uq_staff_staff_no"),
        UniqueConstraint("login_account", name="uq_staff_login_account"),
        UniqueConstraint("phone", name="uq_staff_phone"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_storage_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_size: Mapped[int | None] = mapped_column(BIGINT_ID, nullable=True)
    avatar_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staff_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    login_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    department_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("department.id"), nullable=True
    )
    position_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("position.id"), nullable=True
    )
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_distribution: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
