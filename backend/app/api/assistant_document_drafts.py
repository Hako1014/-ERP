"""TASK-108：本人私有、可跨登录恢复的 AI 采购计划草稿。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import assistant as assistant_api
from app.api.deps import require_permission
from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.redis import RedisCache, get_redis
from app.db.session import get_db
from app.integrations.assistant_provider import AssistantProvider
from app.models.assistant_conversation import AssistantConversation
from app.models.assistant_document_draft import AssistantDocumentDraft
from app.models.assistant_extraction import AssistantExtraction
from app.models.org import Staff
from app.services import assistant_attachments, assistant_conversations, assistant_drafts
from app.services import purchase_plan_service
from app.services.assistant_conversations import utcnow
from app.services.assistant_attachment_files import PrivateAttachmentStorage, safe_name

router = APIRouter(prefix="/assistant/document-drafts", tags=["AI 单据草稿"])
create_purchase_draft = require_permission("purchase:create")
DRAFT_DAYS = 7


class ParseRequest(assistant_api.AssistantDraftParseRequest):
    conversation_id: UUID


class ReparseRequest(ParseRequest):
    draft_version: int = Field(ge=1)


class ExtractionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    extraction_id: UUID
    request_id: UUID


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_version: int = Field(ge=1)
    edits: assistant_api.AssistantDraftEdits


class FormItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    sku_id: int | None = None
    planned_qty: str | int | float
    purchase_price: str | int | float | None = None


class ConfirmForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: int
    plan_date: datetime
    remark: str | None = Field(default=None, max_length=255)
    items: list[FormItem] = Field(min_length=1, max_length=100)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_version: int = Field(ge=1)
    request_id: UUID
    form: ConfirmForm
    attach_source: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _draft_hash(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def _owned_conversation(db: Session, staff: Staff, conversation_id: str, *, lock: bool = False):
    actor = assistant_conversations.actor_context(db, staff.id, staff.company_id)
    stmt = select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.company_id == actor.company_id,
            AssistantConversation.staff_id == actor.staff_id,
            AssistantConversation.deleted_at.is_(None),
            AssistantConversation.status != "DELETED",
        )
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "会话不存在或不可见")
    if row.status != "ACTIVE":
        raise BizException(ErrorCode.CONFLICT, "请先恢复会话后再操作单据草稿")
    return row


def _owned_draft(db: Session, staff: Staff, draft_id: str, *, lock: bool = False):
    stmt = select(AssistantDocumentDraft).where(
        AssistantDocumentDraft.id == draft_id,
        AssistantDocumentDraft.company_id == staff.company_id,
        AssistantDocumentDraft.staff_id == staff.id,
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "单据草稿不存在或不可见")
    return row


def _audit(db: Session, staff: Staff, row: AssistantDocumentDraft, action: str) -> None:
    write_audit(
        db,
        action=action,
        target_type="assistant_document_draft",
        target_id=row.id,
        summary="AI 采购计划草稿状态变更",
        detail={"status": row.status, "version": row.version, "source_kind": row.source_kind},
        actor_id=staff.id,
        actor_name=staff.name,
        company_id=staff.company_id,
    )


def _refresh_creation_state(db: Session, staff: Staff, row: AssistantDocumentDraft) -> None:
    if row.status != "CREATING" or not row.creation_request_id:
        return
    result = purchase_plan_service.get_creation_request_result(
        db, staff=staff, request_id=row.creation_request_id
    )
    if result["status"] == "SUCCEEDED" and result["result"]:
        row.status = "CREATED"
        row.creation_result = result["result"]
        row.created_plan_id = result["result"].get("id")
        row.updated_at = utcnow()
        _audit(db, staff, row, "ai.docdraft.created.recovered")
        db.commit()
    elif result["status"] == "FAILED":
        row.status = "FAILED"
        row.updated_at = utcnow()
        _audit(db, staff, row, "ai.docdraft.create_failed")
        db.commit()


def _contract(db: Session, staff: Staff, row: AssistantDocumentDraft) -> dict:
    _refresh_creation_state(db, staff, row)
    now = _now()
    if row.status not in {"CREATED", "CANCELLED", "FAILED", "EXPIRED"} and row.expires_at <= now:
        row.status = "EXPIRED"
        row.updated_at = now
        _audit(db, staff, row, "ai.docdraft.expired")
        db.commit()
    data = dict(row.draft_data or {})
    data["draft_id"] = row.id
    data["version"] = row.version
    data["expires_at"] = row.expires_at.isoformat(timespec="seconds")
    result = assistant_drafts.draft_contract(data)
    result.update(
        draft_id=row.id,
        version=row.version,
        conversation_id=row.conversation_id,
        source_kind=row.source_kind,
        draft_status=row.status,
        source_text=(data.get("source_text") or "")[:8_000],
        source_attachment_id=row.source_attachment_id,
        source_extraction_id=row.source_extraction_id,
        source_display_name=data.get("source_display_name"),
        created_plan_id=row.created_plan_id,
        creation_result=row.creation_result,
        creation_request_id=row.creation_request_id,
        attachments=(row.creation_result or {}).get("attachments", []) if row.status == "CREATED" else [],
    )
    return result


def _discard_unlinked_copy(row: AssistantDocumentDraft) -> None:
    payload = row.creation_payload if isinstance(row.creation_payload, dict) else {}
    attachment = payload.get("assistant_attachment")
    if row.status == "FAILED" and isinstance(attachment, dict) and attachment.get("storage_key"):
        try:
            PrivateAttachmentStorage().remove(attachment["storage_key"])
        except (BizException, OSError):
            pass


def _copy_source_for_business_attachment(
    db: Session, staff: Staff, row: AssistantDocumentDraft
) -> dict:
    if not row.source_attachment_id:
        raise BizException(ErrorCode.BAD_REQUEST, "该草稿没有可转为业务附件的源文件")
    source, _ = assistant_attachments.owned(
        db, staff, row.conversation_id, row.source_attachment_id,
    )
    display_name = safe_name(source.display_name)
    data = PrivateAttachmentStorage().read(source.storage_key)
    storage_key = uuid4().hex + uuid4().hex
    PrivateAttachmentStorage().save(data, key=storage_key)
    return {
        "id": str(uuid4()),
        "storage_key": storage_key,
        "source_attachment_id": source.id,
        "display_name": display_name,
        "media_type": source.media_type,
        "size": source.size,
        "sha256": source.sha256,
    }


def _new_row(
    *, staff: Staff, conversation_id: str, request_id: str, source_kind: str,
    data: dict, source_attachment_id: str | None = None,
    source_extraction_id: str | None = None,
) -> AssistantDocumentDraft:
    data["expires_at"] = (_now() + timedelta(days=DRAFT_DAYS)).isoformat(timespec="seconds")
    contract = assistant_drafts.draft_contract(data)
    return AssistantDocumentDraft(
        id=str(data["draft_id"]), company_id=staff.company_id, staff_id=staff.id,
        conversation_id=conversation_id, source_kind=source_kind,
        source_attachment_id=source_attachment_id, source_extraction_id=source_extraction_id,
        status="READY" if contract["ready"] else "EDITING", version=int(data["version"]),
        request_id=request_id, draft_data=data,
        expires_at=_now() + timedelta(days=DRAFT_DAYS),
    )


@router.get("", response_model=Result[list])
def list_document_drafts(
    conversation_id: UUID,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
):
    _owned_conversation(db, staff, str(conversation_id))
    rows = db.scalars(
        select(AssistantDocumentDraft).where(
            AssistantDocumentDraft.company_id == staff.company_id,
            AssistantDocumentDraft.staff_id == staff.id,
            AssistantDocumentDraft.conversation_id == str(conversation_id),
        ).order_by(AssistantDocumentDraft.updated_at.desc()).limit(50)
    ).all()
    return Result.ok([_contract(db, staff, row) for row in rows])


@router.get("/{draft_id}", response_model=Result[dict])
def get_document_draft(
    draft_id: UUID,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, str(draft_id), lock=True)
    return Result.ok(_contract(db, staff, row))


@router.post("/parse", response_model=Result[dict])
async def create_document_draft(
    payload: ParseRequest,
    request: Request,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
    redis: RedisCache = Depends(get_redis),
    provider: AssistantProvider = Depends(assistant_api.get_assistant_provider),
):
    conversation = _owned_conversation(db, staff, str(payload.conversation_id))
    digest = _draft_hash(str(conversation.id), payload.mode, payload.input_text)
    existing = db.scalar(select(AssistantDocumentDraft).where(
        AssistantDocumentDraft.company_id == staff.company_id,
        AssistantDocumentDraft.staff_id == staff.id,
        AssistantDocumentDraft.request_id == str(payload.request_id),
    ))
    if existing:
        if existing.conversation_id != conversation.id or existing.draft_data.get("request_hash") != digest:
            raise BizException(ErrorCode.CONFLICT, "请求标识已用于不同的草稿内容")
        return Result.ok(_contract(db, staff, existing))

    fingerprint, remaining = assistant_api._draft_session_context(
        request, staff, payload.session_id, payload.session_version
    )
    internal = await assistant_api._run_draft_parse(
        payload=payload, request=request, staff=staff, db=db, redis=redis,
        provider=provider, fingerprint=fingerprint, remaining_seconds=remaining,
        existing_raw=None, existing_draft=None, store_temporary_draft=False,
        return_internal=True, write_draft_audit=False,
    )
    internal["request_hash"] = digest
    row = _new_row(
        staff=staff, conversation_id=conversation.id, request_id=str(payload.request_id),
        source_kind=payload.mode, data=internal,
    )
    db.add(row)
    _audit(db, staff, row, "ai.docdraft.create")
    db.commit()
    return Result.ok(_contract(db, staff, row))


@router.post("/from-extraction", response_model=Result[dict])
def create_draft_from_extraction(
    payload: ExtractionDraftRequest,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
):
    conversation = _owned_conversation(db, staff, str(payload.conversation_id))
    extraction = db.scalar(select(AssistantExtraction).where(
        AssistantExtraction.id == str(payload.extraction_id),
        AssistantExtraction.company_id == staff.company_id,
        AssistantExtraction.staff_id == staff.id,
        AssistantExtraction.conversation_id == conversation.id,
    ))
    if extraction is None or extraction.status != "COMPLETED" or not isinstance(extraction.result, dict):
        raise BizException(ErrorCode.NOT_FOUND, "可用的单据识别结果不存在")
    result = extraction.result
    if result.get("document_type") != "purchase_plan" or not result.get("supported_for_draft"):
        raise BizException(ErrorCode.BAD_REQUEST, "该识别结果不能生成采购计划草稿")
    existing = db.scalar(select(AssistantDocumentDraft).where(
        AssistantDocumentDraft.company_id == staff.company_id,
        AssistantDocumentDraft.staff_id == staff.id,
        AssistantDocumentDraft.request_id == str(payload.request_id),
    ))
    digest = _draft_hash(conversation.id, extraction.id)
    if existing:
        if existing.draft_data.get("request_hash") != digest:
            raise BizException(ErrorCode.CONFLICT, "请求标识已用于其他草稿")
        return Result.ok(_contract(db, staff, existing))

    attachment, _ = assistant_attachments.owned(
        db, staff, conversation.id, extraction.attachment_id
    )
    fields = result.get("fields") or {}

    def value(field: dict | None, name: str):
        item = (field or {}).get(name) or {}
        return item.get("value")

    extracted = assistant_drafts._ExtractedPlan.model_validate({
        "document_type": result.get("document_type"),
        "supplier_name": value(fields, "supplier_name"),
        "supplier_code": value(fields, "supplier_code"),
        "plan_date": value(fields, "plan_date"),
        "remark": value(fields, "remark"),
        "supplier_source_ref": (fields.get("supplier_name") or {}).get("source_ref"),
        "plan_date_source_ref": (fields.get("plan_date") or {}).get("source_ref"),
        "remark_source_ref": (fields.get("remark") or {}).get("source_ref"),
        "extracted_text": result.get("extracted_text"),
        "items": [
            {
                "product_name": value(item.get("fields"), "product_name"),
                "product_code": value(item.get("fields"), "product_code"),
                "spec_name": value(item.get("fields"), "spec_name"),
                "spec_code": value(item.get("fields"), "spec_code"),
                "unit_name": value(item.get("fields"), "unit_name"),
                "quantity": value(item.get("fields"), "quantity"),
                "purchase_price": value(item.get("fields"), "purchase_price"),
                "source_ref": item.get("source_ref"),
            }
            for item in result.get("items", [])
        ],
    })
    data = assistant_drafts.build_purchase_plan_draft(
        db, staff=staff, extracted=extracted, source_kind="attachment",
        source_text=str(result.get("source_text") or "")[:32_000],
    )
    data["request_hash"] = digest
    data["source_display_name"] = attachment.display_name
    data["sources"] = result.get("sources", [])
    for index, extraction_item in enumerate(result.get("items", [])):
        if index < len(data["items"]):
            data["items"][index]["source_ref"] = extraction_item.get("source_ref")
    assistant_drafts.recalculate_amounts(db, data)
    row = _new_row(
        staff=staff, conversation_id=conversation.id, request_id=str(payload.request_id),
        source_kind="attachment", data=data, source_attachment_id=attachment.id,
        source_extraction_id=extraction.id,
    )
    db.add(row)
    _audit(db, staff, row, "ai.docdraft.from_extraction")
    db.commit()
    return Result.ok(_contract(db, staff, row))


@router.put("/{draft_id}", response_model=Result[dict])
def edit_document_draft(
    draft_id: UUID,
    payload: EditRequest,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, str(draft_id), lock=True)
    if row.status in {"CREATED", "CANCELLED", "EXPIRED", "CREATING"} or row.expires_at <= _now():
        raise BizException(ErrorCode.CONFLICT, "当前草稿状态不可编辑")
    if row.version != payload.draft_version:
        raise BizException(ErrorCode.AI_SESSION_VERSION_CONFLICT, "草稿版本已更新，请刷新后重试")
    _discard_unlinked_copy(row)
    data = assistant_drafts.validate_and_apply_edits(
        db, staff=staff, draft=row.draft_data,
        edits=payload.edits.model_dump(exclude_unset=True),
    )
    assistant_drafts.recalculate_amounts(db, data)
    row.draft_data = data
    row.version = int(data["version"])
    row.status = "READY" if assistant_drafts.draft_contract(data)["ready"] else "EDITING"
    row.creation_request_id = None
    row.creation_payload_hash = None
    row.creation_payload = None
    row.creation_result = None
    row.created_plan_id = None
    row.updated_at = utcnow()
    _audit(db, staff, row, "ai.docdraft.update")
    db.commit()
    return Result.ok(_contract(db, staff, row))


@router.post("/{draft_id}/reparse", response_model=Result[dict])
async def reparse_document_draft(
    draft_id: UUID,
    payload: ReparseRequest,
    request: Request,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
    redis: RedisCache = Depends(get_redis),
    provider: AssistantProvider = Depends(assistant_api.get_assistant_provider),
):
    row = _owned_draft(db, staff, str(draft_id), lock=True)
    if row.conversation_id != str(payload.conversation_id):
        raise BizException(ErrorCode.NOT_FOUND, "单据草稿不存在或不可见")
    if row.version != payload.draft_version or row.status in {"CREATED", "CANCELLED", "EXPIRED", "CREATING"}:
        raise BizException(ErrorCode.AI_SESSION_VERSION_CONFLICT, "草稿版本或状态已变化，请刷新后重试")
    _discard_unlinked_copy(row)
    fingerprint, remaining = assistant_api._draft_session_context(
        request, staff, payload.session_id, payload.session_version
    )
    internal = await assistant_api._run_draft_parse(
        payload=payload, request=request, staff=staff, db=db, redis=redis,
        provider=provider, fingerprint=fingerprint, remaining_seconds=remaining,
        existing_raw=None, existing_draft=row.draft_data, store_temporary_draft=False,
        return_internal=True, write_draft_audit=False,
    )
    internal["request_hash"] = row.draft_data.get("request_hash")
    if row.source_attachment_id:
        internal["source_display_name"] = row.draft_data.get("source_display_name")
        internal["sources"] = row.draft_data.get("sources", [])
    row.draft_data = internal
    row.version = int(internal["version"])
    row.source_kind = payload.mode
    row.status = "READY" if assistant_drafts.draft_contract(internal)["ready"] else "EDITING"
    row.expires_at = _now() + timedelta(days=DRAFT_DAYS)
    row.creation_request_id = None
    row.creation_payload_hash = None
    row.creation_payload = None
    row.creation_result = None
    row.created_plan_id = None
    row.updated_at = utcnow()
    _audit(db, staff, row, "ai.docdraft.reparse")
    db.commit()
    return Result.ok(_contract(db, staff, row))


@router.delete("/{draft_id}", response_model=Result[dict])
def cancel_document_draft(
    draft_id: UUID,
    draft_version: int,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, str(draft_id), lock=True)
    if row.version != draft_version:
        raise BizException(ErrorCode.AI_SESSION_VERSION_CONFLICT, "草稿版本已更新，请刷新后重试")
    if row.status in {"CREATED", "CREATING"}:
        raise BizException(ErrorCode.CONFLICT, "已提交或正在提交的草稿不能取消")
    _discard_unlinked_copy(row)
    row.status = "CANCELLED"
    row.creation_request_id = None
    row.creation_payload_hash = None
    row.creation_payload = None
    row.creation_result = None
    row.created_plan_id = None
    row.draft_data = {"draft_id": row.id, "doc_type": "purchase_plan", "source_kind": row.source_kind,
                      "items": [], "supplier": {}, "plan_date": {}, "remark": {}, "errors": [], "edited_fields": [], "version": row.version}
    row.updated_at = utcnow()
    _audit(db, staff, row, "ai.docdraft.cancel")
    db.commit()
    return Result.ok({"cancelled": True, "draft_id": row.id})


@router.post("/{draft_id}/confirm", response_model=Result[dict])
def confirm_document_draft(
    draft_id: UUID,
    payload: ConfirmRequest,
    staff: Staff = Depends(create_purchase_draft),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, str(draft_id), lock=True)
    if row.status == "CREATED" and row.creation_result:
        if row.creation_request_id != str(payload.request_id):
            raise BizException(ErrorCode.CONFLICT, "该草稿已创建采购计划")
        return Result.ok(row.creation_result)
    if row.status == "CREATING" and row.creation_request_id:
        if row.creation_request_id != str(payload.request_id):
            raise BizException(ErrorCode.CONFLICT, "保存结果尚未确认，请使用原请求号恢复状态")
        data = assistant_drafts.validate_and_apply_edits(
            db, staff=staff, draft=row.draft_data, edits={
                "supplier_id": payload.form.supplier_id,
                "plan_date": payload.form.plan_date.isoformat(timespec="seconds"),
                "remark": payload.form.remark,
                "items": [
                    {"product_id": item.product_id, "sku_id": item.sku_id,
                     "planned_qty": str(item.planned_qty),
                     "purchase_price": str(item.purchase_price) if item.purchase_price is not None else None}
                    for item in payload.form.items
                ],
            },
        )
        retry_contract = assistant_drafts.draft_contract(data)
        saved_creation = row.creation_payload if isinstance(row.creation_payload, dict) else {}
        retry_attachment = saved_creation.get("assistant_attachment")
        if payload.attach_source != bool(retry_attachment):
            raise BizException(ErrorCode.CONFLICT, "同一保存请求不能更改附件选择")
        retry_hash = hashlib.sha256(json.dumps({
            "form": retry_contract["payload"], "assistant_attachment": retry_attachment,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if retry_hash != row.creation_payload_hash or not retry_contract["ready"]:
            raise BizException(ErrorCode.CONFLICT, "同一保存请求不能用于不同的表单内容")
        retry_result = purchase_plan_service.create_plan_idempotent(
            db, data=retry_contract["payload"], staff=staff,
            request_id=row.creation_request_id, assistant_draft_id=row.id,
            assistant_attachment=retry_attachment,
        )
        row.status = "CREATED"
        row.created_plan_id = retry_result.get("id")
        row.creation_result = retry_result
        row.updated_at = utcnow()
        _audit(db, staff, row, "ai.docdraft.created")
        db.commit()
        return Result.ok(retry_result, message="采购计划已创建")
    if row.expires_at <= _now() or row.status in {"CANCELLED", "EXPIRED", "FAILED"}:
        raise BizException(ErrorCode.CONFLICT, "草稿已过期或不可提交")
    if row.version != payload.draft_version:
        raise BizException(ErrorCode.AI_SESSION_VERSION_CONFLICT, "草稿版本已更新，请刷新后重试")
    if row.creation_request_id and row.creation_request_id != str(payload.request_id):
        raise BizException(ErrorCode.CONFLICT, "保存结果尚未确认，请使用原请求号恢复状态")
    conversation = _owned_conversation(db, staff, row.conversation_id)
    form = payload.form
    edits = {
        "supplier_id": form.supplier_id,
        "plan_date": form.plan_date.isoformat(timespec="seconds"),
        "remark": form.remark,
        "items": [
            {"product_id": item.product_id, "sku_id": item.sku_id,
             "planned_qty": str(item.planned_qty),
             "purchase_price": str(item.purchase_price) if item.purchase_price is not None else None}
            for item in form.items
        ],
    }
    data = assistant_drafts.validate_and_apply_edits(
        db, staff=staff, draft=row.draft_data, edits=edits,
    )
    assistant_drafts.recalculate_amounts(db, data)
    contract = assistant_drafts.draft_contract(data)
    if not contract["ready"] or not contract["payload"]:
        raise BizException(ErrorCode.BAD_REQUEST, "请先补齐草稿必填项并核对商品、规格和数量")
    if row.creation_request_id:
        raise BizException(ErrorCode.CONFLICT, "保存结果尚未确认，请使用原请求号恢复状态")
    assistant_attachment = _copy_source_for_business_attachment(db, staff, row) if payload.attach_source else None
    creation_data = {"form": contract["payload"], "assistant_attachment": assistant_attachment}
    canonical = json.dumps(creation_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    row.creation_request_id = str(payload.request_id)
    row.creation_payload_hash = payload_hash
    row.creation_payload = creation_data
    row.draft_data = data
    row.version = int(data["version"])
    row.status = "CREATING"
    row.updated_at = utcnow()
    _audit(db, staff, row, "ai.docdraft.confirm")
    db.commit()

    result = purchase_plan_service.create_plan_idempotent(
        db, data=contract["payload"], staff=staff,
        request_id=str(payload.request_id), assistant_draft_id=row.id,
        assistant_attachment=assistant_attachment,
    )
    row = _owned_draft(db, staff, row.id, lock=True)
    row.status = "CREATED"
    row.created_plan_id = result.get("id")
    row.creation_result = result
    row.updated_at = utcnow()
    _audit(db, staff, row, "ai.docdraft.created")
    db.commit()
    return Result.ok(result, message="采购计划已创建")

