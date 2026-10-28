"""个人只读 MCP 凭据；仅持久化高熵令牌摘要。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base, TimestampMixin


class McpCredential(Base, TimestampMixin):
    __tablename__ = "mcp_credential"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), index=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"))
    name: Mapped[str] = mapped_column(String(64))
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    permission_scopes: Mapped[list[str]] = mapped_column(JSON)
    tool_scopes: Mapped[list[str]] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
