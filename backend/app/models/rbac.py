from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base, TimestampMixin


class Role(Base, TimestampMixin):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_role_company_code"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Permission(Base):
    __tablename__ = "permission"
    __table_args__ = (UniqueConstraint("code", name="uq_permission_code"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    module: Mapped[str | None] = mapped_column(String(32), nullable=True)


class StaffRole(Base):
    __tablename__ = "staff_role"
    __table_args__ = (
        UniqueConstraint("staff_id", "role_id", name="uq_staff_role_staff_role"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=False
    )
    role_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("role.id"), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission_role_permission"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("role.id"), nullable=False)
    permission_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("permission.id"), nullable=False
    )


class DataScope(Base, TimestampMixin):
    __tablename__ = "data_scope"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    department_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("department.id"), nullable=True
    )


class IpRule(Base, TimestampMixin):
    __tablename__ = "ip_rule"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    rule_type: Mapped[str] = mapped_column(String(8), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
