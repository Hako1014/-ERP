"""AI 助手配置与脱敏调用台账

Revision ID: 0022
Revises: 0021
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistant_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provider", sa.String(24), nullable=False, server_default="deepseek"),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column(
            "api_key_overrides_environment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "concurrent_requests_per_user", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("daily_requests_per_user", sa.Integer(), nullable=True),
        sa.Column("monthly_budget", sa.Numeric(18, 8), nullable=True),
        sa.Column("input_price_per_million", sa.Numeric(18, 8), nullable=True),
        sa.Column("output_price_per_million", sa.Numeric(18, 8), nullable=True),
        sa.Column("updated_by_staff_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.ForeignKeyConstraint(["updated_by_staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_config")),
        sa.UniqueConstraint("company_id", name="uq_assistant_config_company"),
    )
    op.create_table(
        "assistant_usage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_category", sa.String(64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tool_names", sa.JSON(), nullable=False),
        sa.Column("cost_status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("reserved_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("input_price_per_million", sa.Numeric(18, 8), nullable=True),
        sa.Column("output_price_per_million", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_usage")),
        sa.UniqueConstraint("company_id", "request_id", name="uq_assistant_usage_request"),
    )
    op.create_index(
        "ix_assistant_usage_company_started", "assistant_usage", ["company_id", "started_at"]
    )
    op.create_index(
        "ix_assistant_usage_staff_started", "assistant_usage", ["staff_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_usage_staff_started", table_name="assistant_usage")
    op.drop_index("ix_assistant_usage_company_started", table_name="assistant_usage")
    op.drop_table("assistant_usage")
    op.drop_table("assistant_config")
