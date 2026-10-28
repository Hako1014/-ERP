"""本人私有附件识别 API；外发须同时满足公司策略和本次明确确认。"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import assistant as assistant_api
from app.api.deps import get_current_staff
from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.redis import RedisCache, get_redis
from app.db.session import get_db
from app.integrations.assistant_provider import AssistantProvider
from app.models.assistant_attachment import AssistantAttachment
from app.models.assistant_extraction import AssistantExtraction
from app.models.assistant_governance import AssistantConfig
from app.models.org import Staff
from app.services import assistant_attachments as attachments
from app.services import assistant_conversations as chats
from app.services import assistant_drafts, assistant_extractions as extraction_service
from app.services.assistant_governance import finalize_usage, reserve_usage, resolve_runtime
from app.services.assistant_limits import AssistantLease, acquire_request, release_request

router = APIRouter(prefix="/assistant", tags=["AI 助手附件识别"])
ASSISTANT_PROVIDER_DEPENDENCY = assistant_api.get_assistant_provider
_active_tasks: dict[str, asyncio.Task] = {}


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    request_id: UUID
    external_send_confirmed: bool = False
    pages: list[int] = Field(default_factory=list, max_length=20)
    sheet_name: str | None = Field(default=None, max_length=31)


def _contract(row: AssistantExtraction) -> dict:
    return {
        "id": row.id,
        "attachment_id": row.attachment_id,
        "conversation_id": row.conversation_id,
        "status": row.status,
        "input_mode": row.input_mode,
        "model": row.model,
        "selections": row.selections,
        "result": row.result,
        "error_category": row.error_category,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _owned_extraction(db: Session, staff: Staff, extraction_id: str) -> tuple[AssistantExtraction, AssistantAttachment]:
    actor = chats.actor_context(db, staff.id, staff.company_id)
    row = db.scalar(select(AssistantExtraction).where(
        AssistantExtraction.id == extraction_id,
        AssistantExtraction.company_id == actor.company_id,
        AssistantExtraction.staff_id == actor.staff_id,
    ))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "识别记录不存在或不可见")
    attachment, conversation = attachments.owned(db, staff, row.conversation_id, row.attachment_id)
    if conversation.id != row.conversation_id:
        raise BizException(ErrorCode.NOT_FOUND, "识别记录不存在或不可见")
    return row, attachment


def _external_policy(db: Session, company_id: int):
    runtime = resolve_runtime(db, company_id, env_settings=get_settings())
    config = db.scalar(select(AssistantConfig).where(AssistantConfig.company_id == company_id))
    if not config or not config.allow_attachment_external_send:
        raise BizException(ErrorCode.FORBIDDEN, "公司尚未允许附件外发识别，请联系管理员在 AI 助手配置中启用")
    if not runtime.settings.assistant_enabled or runtime.settings.assistant_provider != "deepseek":
        raise BizException(ErrorCode.AI_UNCONFIGURED, "当前公司未启用 DeepSeek 服务")
    if not runtime.settings.assistant_api_key or not runtime.settings.assistant_model:
        raise BizException(ErrorCode.AI_UNCONFIGURED, "DeepSeek 服务未配置")
    return runtime


@router.get("/attachments/{attachment_id}/recognition-options", response_model=Result[dict])
def recognition_options(
    attachment_id: UUID,
    conversation_id: UUID = Query(...),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row, conversation = attachments.owned(db, staff, str(conversation_id), str(attachment_id))
    actor = chats.actor_context(db, staff.id, staff.company_id)
    config = db.scalar(select(AssistantConfig).where(AssistantConfig.company_id == actor.company_id))
    choices = extraction_service.options(row)
    return Result.ok({**choices, "attachment_id": row.id, "conversation_id": conversation.id,
                      "external_send_allowed": bool(config and config.allow_attachment_external_send)})


@router.get("/attachments/{attachment_id}/extractions", response_model=Result[list])
def list_extractions(
    attachment_id: UUID,
    conversation_id: UUID = Query(...),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row, _ = attachments.owned(db, staff, str(conversation_id), str(attachment_id))
    actor = chats.actor_context(db, staff.id, staff.company_id)
    items = db.scalars(select(AssistantExtraction).where(
        AssistantExtraction.company_id == actor.company_id,
        AssistantExtraction.staff_id == actor.staff_id,
        AssistantExtraction.conversation_id == row.conversation_id,
        AssistantExtraction.attachment_id == row.id,
    ).order_by(AssistantExtraction.created_at.desc()).limit(5))
    return Result.ok([_contract(item) for item in items])


@router.post("/attachments/{attachment_id}/extractions", response_model=Result[dict])
async def extract_attachment(
    attachment_id: UUID,
    payload: ExtractionRequest,
    request: Request,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
    redis: RedisCache = Depends(get_redis),
    provider: AssistantProvider = Depends(ASSISTANT_PROVIDER_DEPENDENCY),
):
    if not payload.external_send_confirmed:
        raise BizException(ErrorCode.FORBIDDEN, "请先在界面确认本次附件将发送给 DeepSeek")
    row, conversation = attachments.owned(db, staff, str(payload.conversation_id), str(attachment_id), lock=True)
    if not row.message_id:
        raise BizException(ErrorCode.CONFLICT, "请先将附件保存到本人会话，再发起识别")
    if conversation.status != "ACTIVE":
        raise BizException(ErrorCode.CONFLICT, "请先恢复会话后再识别附件")
    runtime = _external_policy(db, staff.company_id)
    actor = chats.actor_context(db, staff.id, staff.company_id)
    existing = db.scalar(select(AssistantExtraction).where(
        AssistantExtraction.company_id == actor.company_id,
        AssistantExtraction.staff_id == actor.staff_id,
        AssistantExtraction.request_id == str(payload.request_id),
    ))
    selections = {"pages": payload.pages, "sheet_name": payload.sheet_name, "external_send_confirmed": True}
    if existing:
        if existing.attachment_id != row.id or existing.selections != selections:
            raise BizException(ErrorCode.CONFLICT, "识别请求号已用于其他附件或选择项")
        return Result.ok(_contract(existing))

    prepared = extraction_service.prepare_input(row, pages=payload.pages, sheet_name=payload.sheet_name)
    settings = runtime.settings
    if prepared["images"] and settings.assistant_model.casefold() != "deepseek-flash":
        raise BizException(ErrorCode.AI_UNCONFIGURED, "当前模型尚未验证支持图像输入；请联系管理员配置 deepseek-flash")
    start_with_tools = getattr(provider, "start_with_tools", None)
    if not callable(start_with_tools):
        raise BizException(ErrorCode.AI_UNCONFIGURED, "当前 AI 服务不支持结构化识别")
    messages = extraction_service.messages_for_input(prepared)
    extraction = AssistantExtraction(
        id=str(payload.request_id), company_id=actor.company_id, staff_id=actor.staff_id,
        conversation_id=conversation.id, attachment_id=row.id, request_id=str(payload.request_id),
        status="RUNNING", input_mode=prepared["mode"], model=settings.assistant_model,
        selections=selections, result=None, created_at=chats.utcnow(), updated_at=chats.utcnow(),
    )
    lease: AssistantLease | None = None
    usage_reserved = False
    upstream_called = False
    started_at = time.monotonic()
    provider_usage = None
    status, failure_category = "failed", "extraction_failed"
    try:
        db.add(extraction)
        db.commit()
        lease = acquire_request(
            redis, company_id=actor.company_id, staff_id=actor.staff_id,
            request_id=str(payload.request_id), requests_per_minute=runtime.requests_per_minute,
            concurrent_limit=runtime.concurrent_requests_per_user, daily_limit=runtime.daily_requests_per_user,
            lease_seconds=settings.assistant_total_timeout_seconds + 60,
        )
        reserve_usage(
            db, runtime=runtime, company_id=actor.company_id, staff_id=actor.staff_id,
            request_id=str(payload.request_id), messages=messages,
            max_output_tokens=settings.assistant_max_output_tokens, tool_calls_enabled=False,
        )
        db.commit()
        usage_reserved = True
        _active_tasks[extraction.id] = asyncio.current_task()
        db.close()
        upstream_called = True
        turn = await assistant_api._await_with_disconnect(
            request,
            start_with_tools(messages, [assistant_drafts.extraction_tool(include_source_refs=True)]),
            settings.assistant_total_timeout_seconds,
        )
        provider_usage = turn.usage
        extracted = assistant_drafts.parse_extraction_turn(turn)

        # 清空会话或撤销附件期间的迟到回包必须丢弃，不可复活识别结果。
        current_attachment, current_conversation = attachments.owned(
            db, staff, str(payload.conversation_id), str(attachment_id), lock=True
        )
        current = db.scalar(select(AssistantExtraction).where(
            AssistantExtraction.id == extraction.id,
            AssistantExtraction.company_id == actor.company_id,
            AssistantExtraction.staff_id == actor.staff_id,
        ).with_for_update())
        if current is None or current.status != "RUNNING" or current_attachment.message_id != row.message_id:
            raise BizException(ErrorCode.CONFLICT, "附件或会话状态已变化，识别结果未保存")
        usage_info = {
            "model": settings.assistant_model,
            "provider_prompt_tokens": provider_usage.prompt_tokens if provider_usage else None,
            "provider_completion_tokens": provider_usage.completion_tokens if provider_usage else None,
            "provider_total_tokens": provider_usage.total_tokens if provider_usage else None,
            "image_count": len(prepared["images"]),
            "image_usage_note": "图像 token 已计入 Provider 返回的 prompt_tokens；上游未单列每张图片 token。",
        }
        result = extraction_service.result_contract(
            extracted, source_text=prepared["text"], sources=prepared["sources"], usage=usage_info
        )
        current.status, current.result, current.error_category = "COMPLETED", result, None
        current.updated_at = chats.utcnow()
        chats._audit(db, actor, current_conversation, "extract.complete", str(payload.request_id))
        db.commit()
        status, failure_category = "success", None
        return Result.ok(_contract(current))
    except asyncio.CancelledError:
        status, failure_category = "cancelled", "client_cancelled"
        db.rollback()
        current = db.get(AssistantExtraction, extraction.id)
        if current and current.status == "RUNNING":
            current.status, current.error_category, current.updated_at = "CANCELLED", failure_category, chats.utcnow()
            db.commit()
        raise
    except TimeoutError:
        failure_category = "provider_timeout"
        current = db.get(AssistantExtraction, extraction.id)
        if current and current.status == "RUNNING":
            current.status, current.error_category, current.updated_at = "FAILED", failure_category, chats.utcnow()
            db.commit()
        raise BizException(ErrorCode.AI_TIMEOUT) from None
    except Exception as exc:
        if isinstance(exc, BizException):
            public = exc
            failure_category = exc.code.value
        else:
            failure = assistant_api._failure_from_exception(exc)
            public = BizException(failure.code, failure.message)
            failure_category = failure.reason
        db.rollback()
        current = db.get(AssistantExtraction, extraction.id)
        if current and current.status == "RUNNING":
            current.status, current.error_category, current.updated_at = "FAILED", failure_category, chats.utcnow()
            db.commit()
        raise public from None
    finally:
        _active_tasks.pop(extraction.id, None)
        try:
            if usage_reserved:
                finalize_usage(
                    db, company_id=actor.company_id, request_id=str(payload.request_id), status=status,
                    failure_category=failure_category, started_at=started_at,
                    prompt_tokens=provider_usage.prompt_tokens if provider_usage else None,
                    completion_tokens=provider_usage.completion_tokens if provider_usage else None,
                    total_tokens=provider_usage.total_tokens if provider_usage else None,
                    tool_names=["ai.attachment.extract"], runtime=runtime,
                    no_upstream_call=not upstream_called,
                )
        finally:
            release_request(redis, lease)


@router.get("/extractions/{extraction_id}", response_model=Result[dict])
def get_extraction(
    extraction_id: UUID,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row, _ = _owned_extraction(db, staff, str(extraction_id))
    if row.status == "RUNNING" and row.updated_at and (chats.utcnow() - row.updated_at).total_seconds() > get_settings().assistant_total_timeout_seconds + 60:
        row.status, row.error_category, row.updated_at = "FAILED", "worker_interrupted", chats.utcnow()
        db.commit()
    return Result.ok(_contract(row))


@router.delete("/extractions/{extraction_id}", response_model=Result[dict])
def cancel_extraction(
    extraction_id: UUID,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row, _ = _owned_extraction(db, staff, str(extraction_id))
    if row.status == "RUNNING":
        row.status, row.error_category, row.updated_at = "CANCELLED", "user_cancelled", chats.utcnow()
        db.commit()
        task = _active_tasks.get(row.id)
        if task and task is not asyncio.current_task():
            task.cancel()
    return Result.ok(_contract(row))
