"""提醒规则由部署配置显式提供；没有业务默认 SLA。"""

import hashlib
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReminderRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    company_id: int = Field(gt=0)
    actor_staff_id: int = Field(gt=0)
    kind: Literal["approval_timeout", "purchase_overdue", "low_stock"]
    enabled: bool = False
    sla_seconds: int | None = Field(default=None, gt=0)
    repeat_seconds: int | None = Field(default=None, ge=60)
    active_from: datetime | None = None
    timezone: str = "Asia/Shanghai"
    batch_limit: int = Field(default=100, ge=1, le=500)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("提醒时区必须是有效的 IANA 时区名") from exc
        return value

    @field_validator("active_from")
    @classmethod
    def aware_start(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("启用时间必须带明确时区，禁止默认补发历史")
        return value

    def disabled_reason(self) -> str | None:
        if not self.enabled:
            return "RULE_DISABLED"
        if self.kind == "purchase_overdue":
            return "MISSING_EXPECTED_ARRIVAL_FIELD"
        if self.kind == "low_stock":
            return "MISSING_COMPANY_AND_RECIPIENT_RULE"
        if self.sla_seconds is None or self.repeat_seconds is None or self.active_from is None:
            return "MISSING_CONFIRMED_THRESHOLDS"
        return None

    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:20]
