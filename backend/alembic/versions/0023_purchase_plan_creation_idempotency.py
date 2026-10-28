"""采购计划创建请求幂等与结果恢复

Revision ID: 0023
Revises: 0022
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "purchase_plan_creation_request",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("failure_message", sa.String(length=255), nullable=True),
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
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["purchase_plan.id"]),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_plan_creation_request")),
        sa.UniqueConstraint(
            "company_id",
            "staff_id",
            "request_id",
            name="uq_purchase_plan_creation_scope",
        ),
    )
    op.create_index(
        "ix_purchase_plan_creation_staff_status",
        "purchase_plan_creation_request",
        ["staff_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_purchase_plan_creation_staff_status",
        table_name="purchase_plan_creation_request",
    )
    op.drop_table("purchase_plan_creation_request")
