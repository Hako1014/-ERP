"""私人持久会话；短期运行登记簿继续只保存登录期元数据。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.db.base import Base, BIGINT_ID, TimestampMixin


class AssistantConversation(Base, TimestampMixin):
    __tablename__ = "assistant_conversation"
    __table_args__ = (
        Index("ix_assistant_conversation_owner_status_updated", "company_id", "staff_id", "status", "updated_at"),
        Index("ix_assistant_conversation_deleted", "deleted_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="新会话")
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latest_message_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cleared_through_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssistantMessage(Base, TimestampMixin):
    __tablename__ = "assistant_message"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_assistant_message_seq"),
        UniqueConstraint("company_id", "staff_id", "conversation_id", "client_message_id", name="uq_assistant_message_client"),
        UniqueConstraint("company_id", "staff_id", "request_id", "role", name="uq_assistant_message_request_role"),
        Index("ix_assistant_message_owner_conversation_seq", "company_id", "staff_id", "conversation_id", "seq"),
        Index("ix_assistant_message_purge", "purge_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_conversation.id"), nullable=False)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    client_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reply_to_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    run_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False)
    login_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    run_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    page_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visibility_guard: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(5), nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
