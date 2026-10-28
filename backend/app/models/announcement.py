"""Company-scoped published announcements shown in the shared workbench."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base, TimestampMixin


class Announcement(Base, TimestampMixin):
    __tablename__ = "announcement"
    __table_args__ = (
        Index(
            "ix_announcement_company_status_published",
            "company_id",
            "status",
            "published_at",
        ),
        Index("ix_announcement_effective_period", "starts_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    audience_roles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    audience_department_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
