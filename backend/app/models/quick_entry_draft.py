"""与 AI 会话解耦的本人私有快捷录入草稿。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base, TimestampMixin


class QuickEntryDraft(Base, TimestampMixin):
    __tablename__ = "quick_entry_draft"
    __table_args__ = (
        UniqueConstraint("company_id", "staff_id", "request_id", name="uq_quick_entry_draft_request"),
        Index("ix_quick_entry_draft_owner_updated", "company_id", "staff_id", "updated_at"),
        Index("ix_quick_entry_draft_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=False)
    module_key: Mapped[str] = mapped_column(String(32), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    draft_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="EDITING")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_version: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
