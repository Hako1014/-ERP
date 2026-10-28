from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base


class AssistantConfig(Base):
    """按公司隔离的 AI 助手运行策略；密钥字段只存 Fernet 密文。"""

    __tablename__ = "assistant_config"
    __table_args__ = (UniqueConstraint("company_id", name="uq_assistant_config_company"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_attachment_external_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="deepseek")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_overrides_environment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    requests_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10
    )
    concurrent_requests_per_user: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    daily_requests_per_user: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    input_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    output_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    updated_by_staff_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class AssistantUsage(Base):
    """不保存聊天内容的调用台账；unknown 费用不能折算为零。"""

    __tablename__ = "assistant_usage"
    __table_args__ = (
        UniqueConstraint("company_id", "request_id", name="uq_assistant_usage_request"),
        Index("ix_assistant_usage_company_started", "company_id", "started_at"),
        Index("ix_assistant_usage_staff_started", "staff_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    staff_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cost_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    reserved_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    input_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    output_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
