"""助手独占附件；不与正式单据附件共用存储或生命周期。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BIGINT_ID, TimestampMixin


class AssistantAttachment(Base, TimestampMixin):
    __tablename__ = "assistant_attachment"
    __table_args__ = (
        Index("ix_assistant_attachment_owner", "company_id", "staff_id", "conversation_id", "status"),
        Index("ix_assistant_attachment_message", "message_id"),
        Index("ix_assistant_attachment_cleanup", "purpose", "purge_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_conversation.id"), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assistant_message.id"))
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="CHAT")
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="READY")
    purge_after: Mapped[datetime | None] = mapped_column(DateTime)

