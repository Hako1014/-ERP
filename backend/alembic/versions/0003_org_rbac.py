"""组织与权限数据模型

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "company",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("short_name", sa.String(64), nullable=True),
        sa.Column("legal_person", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("tax_no", sa.String(64), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company")),
    )

    op.create_table(
        "department",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("department.id"), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department")),
    )

    op.create_table(
        "position",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position")),
    )

    op.create_table(
        "staff",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("staff_no", sa.String(32), nullable=True),
        sa.Column("login_account", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("password_hash", sa.String(128), nullable=True),
        sa.Column("department_id", sa.BigInteger(), sa.ForeignKey("department.id"), nullable=True),
        sa.Column("position_id", sa.BigInteger(), sa.ForeignKey("position.id"), nullable=True),
        sa.Column("gender", sa.String(8), nullable=True),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("is_distribution", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff")),
        sa.UniqueConstraint("staff_no", name="uq_staff_staff_no"),
        sa.UniqueConstraint("login_account", name="uq_staff_login_account"),
        sa.UniqueConstraint("phone", name="uq_staff_phone"),
    )

    op.create_table(
        "role",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
        sa.UniqueConstraint("company_id", "code", name="uq_role_company_code"),
    )

    op.create_table(
        "permission",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("module", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission")),
        sa.UniqueConstraint("code", name="uq_permission_code"),
    )

    op.create_table(
        "staff_role",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("role.id"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_role")),
        sa.UniqueConstraint("staff_id", "role_id", name="uq_staff_role_staff_role"),
    )

    op.create_table(
        "role_permission",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("role.id"), nullable=False),
        sa.Column("permission_id", sa.BigInteger(), sa.ForeignKey("permission.id"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_permission")),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permission_role_permission"),
    )

    op.create_table(
        "data_scope",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("department_id", sa.BigInteger(), sa.ForeignKey("department.id"), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_scope")),
    )

    op.create_table(
        "ip_rule",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("rule_type", sa.String(8), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ip_rule")),
    )


def downgrade() -> None:
    for table in [
        "ip_rule",
        "data_scope",
        "role_permission",
        "staff_role",
        "permission",
        "role",
        "staff",
        "position",
        "department",
        "company",
    ]:
        op.drop_table(table)
