"""AI 助手运行配置、用量台账、预算预占与脱敏审计。"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import write_audit
from app.core.config import Settings, get_settings
from app.core.errors import BizException, ErrorCode
from app.models.assistant_governance import AssistantConfig, AssistantUsage

logger = logging.getLogger(__name__)

OFFICIAL_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MONEY_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class AssistantRuntime:
    settings: Settings
    requests_per_minute: int
    concurrent_requests_per_user: int
    daily_requests_per_user: int | None
    monthly_budget: Decimal | None
    input_price_per_million: Decimal | None
    output_price_per_million: Decimal | None
    config_row_id: int | None
    api_key_configured: bool


def _cipher() -> Fernet:
    key = get_settings().assistant_config_encryption_key.strip()
    if not key:
        raise BizException(
            ErrorCode.AI_UNCONFIGURED,
            "尚未配置助手密钥加密密钥，暂不能在管理页保存 API Key",
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise BizException(
            ErrorCode.AI_UNCONFIGURED,
            "助手密钥加密配置无效，请检查服务端密钥设置",
        ) from None


def _decrypt_api_key(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError, UnicodeDecodeError):
        logger.error("AI configuration secret decryption failed")
        raise BizException(
            ErrorCode.AI_UNCONFIGURED,
            "助手密钥无法解密，请检查服务端加密配置",
        ) from None


def _validated_base_url(value: str) -> str:
    """只允许固定官方 host；不能由管理配置将密钥转发到任意 URL。"""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise BizException(ErrorCode.AI_UNCONFIGURED, "AI 服务地址不符合固定官方地址策略") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.deepseek.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        logger.error("AI provider host rejected by fixed-host policy")
        raise BizException(ErrorCode.AI_UNCONFIGURED, "AI 服务地址不符合固定官方地址策略")
    return OFFICIAL_DEEPSEEK_BASE_URL


def resolve_runtime(
    db: Session, company_id: int, *, env_settings: Settings | None = None
) -> AssistantRuntime:
    env = env_settings or get_settings()
    row = db.scalar(
        select(AssistantConfig).where(AssistantConfig.company_id == company_id)
    )
    if row is None:
        api_key = env.assistant_api_key
        model = env.assistant_model
        enabled = env.assistant_enabled
        provider = env.assistant_provider.strip().lower()
        rpm = 10
        concurrent = 1
        daily = None
        monthly_budget = None
        input_price = None
        output_price = None
        row_id = None
    else:
        if row.api_key_overrides_environment:
            api_key = (
                _decrypt_api_key(row.api_key_ciphertext)
                if row.api_key_ciphertext
                else ""
            )
        else:
            api_key = env.assistant_api_key
        model = row.model or env.assistant_model
        enabled = row.enabled
        provider = row.provider.strip().lower()
        rpm = row.requests_per_minute
        concurrent = row.concurrent_requests_per_user
        daily = row.daily_requests_per_user
        monthly_budget = row.monthly_budget
        input_price = row.input_price_per_million
        output_price = row.output_price_per_million
        row_id = row.id

    base_url = _validated_base_url(env.assistant_base_url)
    runtime_settings = env.model_copy(
        update={
            "assistant_enabled": enabled,
            "assistant_provider": provider,
            "assistant_model": model,
            "assistant_api_key": api_key,
            "assistant_base_url": base_url,
        }
    )
    return AssistantRuntime(
        settings=runtime_settings,
        requests_per_minute=rpm,
        concurrent_requests_per_user=concurrent,
        daily_requests_per_user=daily,
        monthly_budget=monthly_budget,
        input_price_per_million=input_price,
        output_price_per_million=output_price,
        config_row_id=row_id,
        api_key_configured=bool(api_key),
    )


def admin_config_contract(db: Session, company_id: int) -> dict[str, Any]:
    runtime = resolve_runtime(db, company_id)
    row = db.scalar(
        select(AssistantConfig).where(AssistantConfig.company_id == company_id)
    )
    return {
        "enabled": runtime.settings.assistant_enabled,
        "allow_attachment_external_send": bool(row and row.allow_attachment_external_send),
        "provider": runtime.settings.assistant_provider,
        "model": runtime.settings.assistant_model,
        "api_key_configured": runtime.api_key_configured,
        "api_key_overrides_environment": bool(row and row.api_key_overrides_environment),
        "requests_per_minute": runtime.requests_per_minute,
        "concurrent_requests_per_user": runtime.concurrent_requests_per_user,
        "daily_requests_per_user": runtime.daily_requests_per_user,
        "monthly_budget": str(runtime.monthly_budget) if runtime.monthly_budget is not None else None,
        "input_price_per_million": (
            str(runtime.input_price_per_million)
            if runtime.input_price_per_million is not None
            else None
        ),
        "output_price_per_million": (
            str(runtime.output_price_per_million)
            if runtime.output_price_per_million is not None
            else None
        ),
        "secret_store_ready": _secret_store_ready(),
        "base_url": OFFICIAL_DEEPSEEK_BASE_URL,
    }


def _secret_store_ready() -> bool:
    key = get_settings().assistant_config_encryption_key.strip()
    if not key:
        return False
    try:
        Fernet(key.encode("ascii"))
        return True
    except (ValueError, UnicodeEncodeError):
        return False


def save_admin_config(
    db: Session,
    *,
    company_id: int,
    actor_id: int,
    payload: Any,
) -> AssistantConfig:
    if payload.provider != "deepseek":
        raise BizException(ErrorCode.BAD_REQUEST, "当前仅允许配置 DeepSeek Provider")
    if payload.model and not _MODEL_PATTERN.fullmatch(payload.model):
        raise BizException(ErrorCode.BAD_REQUEST, "模型标识格式无效")
    if payload.monthly_budget is not None and (
        payload.input_price_per_million is None
        or payload.output_price_per_million is None
    ):
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "启用月度预算前必须填写输入和输出单价，避免以未知费用当作零",
        )
    if payload.api_key and payload.clear_api_key:
        raise BizException(ErrorCode.BAD_REQUEST, "不能同时替换和清除 API Key")
    if payload.api_key:
        _cipher()  # Fail before mutating any row when secure storage is unavailable.

    row = db.scalar(
        select(AssistantConfig)
        .where(AssistantConfig.company_id == company_id)
        .with_for_update()
    )
    if row is None:
        row = AssistantConfig(company_id=company_id)
        db.add(row)
    row.enabled = payload.enabled
    row.allow_attachment_external_send = bool(getattr(payload, "allow_attachment_external_send", False))
    row.provider = payload.provider
    row.model = payload.model.strip()
    row.requests_per_minute = payload.requests_per_minute
    row.concurrent_requests_per_user = payload.concurrent_requests_per_user
    row.daily_requests_per_user = payload.daily_requests_per_user
    row.monthly_budget = payload.monthly_budget
    row.input_price_per_million = payload.input_price_per_million
    row.output_price_per_million = payload.output_price_per_million
    row.updated_by_staff_id = actor_id
    if payload.api_key:
        row.api_key_ciphertext = _cipher().encrypt(payload.api_key.encode("utf-8")).decode("ascii")
        row.api_key_overrides_environment = True
    elif payload.clear_api_key:
        row.api_key_ciphertext = None
        row.api_key_overrides_environment = True
    db.flush()
    write_audit(
        db,
        action="ai.config.update",
        target_type="assistant_config",
        target_id=str(company_id),
        summary="更新 AI 助手运行配置",
        detail={
            "enabled": row.enabled,
            "provider": row.provider,
            "model": row.model,
            "api_key_changed": bool(payload.api_key or payload.clear_api_key),
            "limits_changed": True,
        },
        actor_id=actor_id,
    )
    return row


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """文本按 UTF-8 字节上界估算；DeepSeek 图像按官方每图上限 1024 token 预占。"""
    image_count = 0

    def bounded(value):
        nonlocal image_count
        if isinstance(value, dict):
            image = value.get("image_url")
            if value.get("type") == "image_url" and isinstance(image, dict):
                url = image.get("url", "")
                if isinstance(url, str) and url.startswith("data:image/"):
                    image_count += 1
                    return {"type": "image_url", "image_url": {"url": "[inline image; token estimate bounded]"}}
            return {key: bounded(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bounded(item) for item in value]
        return value

    serialized = json.dumps(bounded(messages), ensure_ascii=False, separators=(",", ":"))
    # Byte-level tokenizers cannot emit more text tokens than encoded input
    # bytes; framing allowance covers role/message metadata.
    return max(1, len(serialized.encode("utf-8")) + len(messages) * 16 + image_count * 1024)


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_million: Decimal | None,
    output_price_per_million: Decimal | None,
) -> Decimal | None:
    if input_price_per_million is None or output_price_per_million is None:
        return None
    cost = (
        Decimal(prompt_tokens) * input_price_per_million
        + Decimal(completion_tokens) * output_price_per_million
    ) / Decimal(1_000_000)
    return cost.quantize(_MONEY_QUANTUM)


def reserve_usage(
    db: Session,
    *,
    runtime: AssistantRuntime,
    company_id: int,
    staff_id: int,
    request_id: str,
    messages: list[dict[str, Any]],
    max_output_tokens: int,
    tool_calls_enabled: bool,
) -> AssistantUsage:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = db.scalar(
        select(AssistantConfig)
        .where(AssistantConfig.company_id == company_id)
        .with_for_update()
    )
    if row is not None and not row.enabled:
        raise BizException(ErrorCode.AI_UNCONFIGURED)
    monthly_budget = row.monthly_budget if row is not None else runtime.monthly_budget
    input_price = row.input_price_per_million if row is not None else runtime.input_price_per_million
    output_price = row.output_price_per_million if row is not None else runtime.output_price_per_million
    if monthly_budget is not None:
        if input_price is None or output_price is None:
            raise BizException(ErrorCode.AI_UNCONFIGURED, "AI 预算价格配置不完整，请联系管理员")
        # Reserve worst-case across every bounded tool turn; never issue an
        # upstream request if a concurrent reservation would exceed the cap.
        from app.services.assistant_tools import (
            MAX_TOOL_ARGUMENT_CHARS,
            MAX_TOOL_CALLS_PER_TURN,
            MAX_TOOL_TURNS,
        )

        turns = MAX_TOOL_TURNS + 1
        bounded_tool_overhead = (
            MAX_TOOL_TURNS
            * MAX_TOOL_CALLS_PER_TURN
            * (MAX_TOOL_ARGUMENT_CHARS + 8_192)
            if tool_calls_enabled
            else 0
        )
        prompt_reserve = (estimate_tokens(messages) + bounded_tool_overhead) * turns
        output_reserve = max_output_tokens * turns
        reserved = estimate_cost(
            prompt_reserve,
            output_reserve,
            input_price,
            output_price,
        )
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # This is a locking read (not an aggregate snapshot read): MySQL's
        # REPEATABLE READ snapshot may predate a competing reservation even
        # after the config-row lock is acquired. The row lock sees latest
        # committed reservations serialized behind that company config row.
        current_rows = db.scalars(
            select(AssistantUsage)
            .where(
                AssistantUsage.company_id == company_id,
                AssistantUsage.started_at >= month_start,
            )
            .with_for_update()
        ).all()
        committed = sum(
            (
                item.reserved_cost or Decimal(0)
                if item.status == "started" or item.cost_status == "unknown"
                else item.estimated_cost or Decimal(0)
            )
            for item in current_rows
        )
        if committed + (reserved or Decimal(0)) > monthly_budget:
            raise BizException(ErrorCode.AI_INSUFFICIENT_BALANCE)
    else:
        reserved = None

    usage = AssistantUsage(
        company_id=company_id,
        staff_id=staff_id,
        request_id=request_id,
        provider=runtime.settings.assistant_provider,
        model=runtime.settings.assistant_model or None,
        status="started",
        tool_names=[],
        cost_status="unknown",
        reserved_cost=reserved,
        input_price_per_million=input_price,
        output_price_per_million=output_price,
        started_at=now,
    )
    db.add(usage)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(
            select(AssistantUsage.id).where(
                AssistantUsage.company_id == company_id,
                AssistantUsage.request_id == request_id,
            )
        )
        if duplicate is not None:
            raise BizException(ErrorCode.CONFLICT, "此请求已提交，请勿重复发送") from None
        raise
    return usage


def finalize_usage(
    db: Session,
    *,
    company_id: int,
    request_id: str,
    status: str,
    failure_category: str | None,
    started_at: float,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    tool_names: list[str],
    runtime: AssistantRuntime,
    no_upstream_call: bool = False,
) -> None:
    """独立提交台账，避免 SSE 流断开或业务查询事务回滚丢失用量记录。"""
    completed = datetime.now(timezone.utc).replace(tzinfo=None)
    UsageSession = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    with UsageSession() as usage_db:
        usage = usage_db.scalar(
            select(AssistantUsage).where(
                AssistantUsage.company_id == company_id,
                AssistantUsage.request_id == request_id,
            )
        )
        if usage is None:
            logger.error("AI usage reservation missing during settlement")
            return
        usage.status = status
        usage.failure_category = failure_category
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        usage.total_tokens = total_tokens
        usage.latency_ms = max(0, int((monotonic() - started_at) * 1000))
        usage.tool_names = sorted(set(tool_names))
        usage.completed_at = completed
        if no_upstream_call:
            usage.cost_status = "known"
            usage.estimated_cost = Decimal(0)
            usage.reserved_cost = None
        elif prompt_tokens is not None and completion_tokens is not None:
            estimate = estimate_cost(
                prompt_tokens,
                completion_tokens,
                usage.input_price_per_million,
                usage.output_price_per_million,
            )
            usage.cost_status = "known" if estimate is not None else "unknown"
            usage.estimated_cost = estimate
            usage.reserved_cost = None if estimate is not None else usage.reserved_cost
        else:
            # For an interrupted upstream request the provider may have billed
            # partial work. Keep the reservation against the monthly budget.
            usage.cost_status = "unknown"
            usage.estimated_cost = None
        usage_db.commit()


def write_tool_audit(
    db: Session,
    *,
    actor_id: int,
    request_id: str,
    tool_name: str,
    outcome: str,
    reason: str | None = None,
) -> None:
    """Tool审计使用独立事务，拒绝/异常记录不受主查询事务回滚影响。"""
    AuditSession = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    with AuditSession() as audit_db:
        write_audit(
            audit_db,
            action="ai.tool.query",
            target_type="assistant_request",
            target_id=request_id,
            summary=f"助手查询工具：{tool_name[:80]}",
            reason=reason[:120] if reason else None,
            detail={"tool": tool_name[:80], "outcome": outcome},
            actor_id=actor_id,
        )
        audit_db.commit()


def log_denied_attempt(
    db: Session,
    *,
    company_id: int,
    staff_id: int,
    request_id: str,
    failure_category: str,
) -> None:
    """记录被治理规则拒绝的尝试，不占用请求台账 ID，也不保存问题文本。"""
    # Roll back any preceding read transaction first, then commit the refusal
    # record independently so later request cleanup cannot erase it.
    db.rollback()
    write_audit(
        db,
        action="ai.request.reject",
        target_type="assistant_request",
        target_id=request_id,
        summary="AI 助手请求被治理规则拦截",
        reason=failure_category[:120],
        detail={"failure_category": failure_category[:64]},
        actor_id=staff_id,
    )
    db.commit()


def usage_contract(row: AssistantUsage) -> dict[str, Any]:
    return {
        "request_id": row.request_id,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "failure_category": row.failure_category,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "latency_ms": row.latency_ms,
        "tool_names": row.tool_names or [],
        "cost_status": row.cost_status,
        "estimated_cost": str(row.estimated_cost) if row.estimated_cost is not None else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def usage_totals(rows: list[AssistantUsage]) -> dict[str, Any]:
    known_cost = sum(
        (
            row.estimated_cost or Decimal(0)
            for row in rows
            if row.cost_status == "known"
        ),
        start=Decimal(0),
    )
    return {
        "request_count": len(rows),
        "known_prompt_tokens": sum(row.prompt_tokens or 0 for row in rows),
        "known_completion_tokens": sum(row.completion_tokens or 0 for row in rows),
        "unknown_token_count": sum(row.total_tokens is None for row in rows),
        "known_cost": str(known_cost.quantize(_MONEY_QUANTUM)),
        "unknown_cost_count": sum(row.cost_status == "unknown" for row in rows),
    }

