"""与私有附件同范围保存的识别任务及可恢复结果。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BIGINT_ID, TimestampMixin


class AssistantExtraction(Base, TimestampMixin):
    __tablename__ = "assistant_extraction"
    __table_args__ = (
        UniqueConstraint("company_id", "staff_id", "request_id", name="uq_assistant_extraction_request"),
        Index("ix_assistant_extraction_attachment", "company_id", "staff_id", "attachment_id", "created_at"),
        Index("ix_assistant_extraction_conversation", "company_id", "staff_id", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_conversation.id"), nullable=False)
    attachment_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_attachment.id"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    input_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    selections: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error_category: Mapped[str | None] = mapped_column(String(64))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime)
