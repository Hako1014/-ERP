from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, CompanyOwnedBase as Base, TimestampMixin


class ApprovalFlow(Base, TimestampMixin):
    __tablename__ = "approval_flow"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    approvers: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)


class ApprovalRecord(Base, TimestampMixin):
    __tablename__ = "approval_record"
    __table_args__ = (
        Index(
            "ix_approval_record_approver_action_doc",
            "approver_id", "action", "doc_type", "doc_id",
        ),
        Index(
            "ix_approval_record_action_status_doc",
            "action", "status", "doc_type", "doc_id",
        ),
        Index(
            "ix_approval_record_doc_history",
            "doc_type", "doc_id", "id",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    flow_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("approval_flow.id"), nullable=True)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    doc_id: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    doc_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    approver_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(16), default="SUBMIT", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
