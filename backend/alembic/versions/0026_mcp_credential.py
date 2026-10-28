"""个人 MCP 查询凭据。"""

from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ids = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "mcp_credential",
        sa.Column("id", ids, primary_key=True, autoincrement=True),
        sa.Column("staff_id", ids, sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("company_id", ids, sa.ForeignKey("company.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("permission_scopes", sa.JSON(), nullable=False),
        sa.Column("tool_scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("token_digest", name="uq_mcp_credential_token_digest"),
    )
    op.create_index("ix_mcp_credential_staff_id", "mcp_credential", ["staff_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_credential_staff_id", table_name="mcp_credential")
    op.drop_table("mcp_credential")
