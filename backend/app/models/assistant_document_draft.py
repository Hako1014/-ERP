"""可恢复的 AI 单据草稿；草稿与正式采购计划分开保存。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base, TimestampMixin


class AssistantDocumentDraft(Base, TimestampMixin):
    __tablename__ = "assistant_document_draft"
    __table_args__ = (
        UniqueConstraint("company_id", "staff_id", "request_id", name="uq_assistant_document_draft_request"),
        Index("ix_assistant_document_draft_conversation", "company_id", "staff_id", "conversation_id", "updated_at"),
        Index("ix_assistant_document_draft_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_conversation.id"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_attachment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assistant_attachment.id", ondelete="SET NULL"))
    source_extraction_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assistant_extraction.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="EDITING")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    draft_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    creation_request_id: Mapped[str | None] = mapped_column(String(36))
    creation_payload_hash: Mapped[str | None] = mapped_column(String(64))
    creation_payload: Mapped[dict | None] = mapped_column(JSON)
    created_plan_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("purchase_plan.id", ondelete="SET NULL"))
    creation_result: Mapped[dict | None] = mapped_column(JSON)
