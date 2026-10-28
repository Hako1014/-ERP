"""独立快捷录入目录及本人私有草稿 API；不负责创建业务单据。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_staff
from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.query_access import current_actor_access
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.models.quick_entry_draft import QuickEntryDraft

router = APIRouter(prefix="/quick-entry", tags=["快捷录入"])
DRAFT_DAYS = 7
MAX_DRAFT_BYTES = 128 * 1024

# 后续 TASK-115～119 通过适配器扩展；每个类型在服务端声明模块和创建权限。
DOCUMENT_CATALOG = (
    {
        "module_key": "purchase",
        "module_label": "采购管理",
        "document_type": "purchase_plan",
        "document_label": "采购计划",
        "permission": "purchase:create",
        "target_path": "/purchase/plans",
    },
)
CATALOG_BY_TYPE = {item["document_type"]: item for item in DOCUMENT_CATALOG}


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    document_type: str = Field(min_length=1, max_length=64)
    source_mode: str = Field(pattern="^(natural|table)$")
    title: str = Field(min_length=1, max_length=160)
    input_text: str = Field(max_length=4000)
    draft_data: dict


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    input_text: str = Field(max_length=4000)
    source_mode: str = Field(pattern="^(natural|table)$")
    draft_data: dict


class ImportConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_version: int = Field(ge=1)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_payload(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _permissions(db: Session, staff: Staff) -> set[str]:
    _, _, permissions = current_actor_access(db, staff.id)
    return permissions


def _spec(db: Session, staff: Staff, document_type: str) -> dict:
    spec = CATALOG_BY_TYPE.get(document_type)
    permissions = _permissions(db, staff)
    if spec is None or ("*" not in permissions and spec["permission"] not in permissions):
        raise BizException(ErrorCode.FORBIDDEN, "当前账号无权快捷录入该类单据")
    module_permission = f"module:{spec['module_key']}"
    if "*" not in permissions and module_permission not in permissions:
        raise BizException(ErrorCode.FORBIDDEN, "当前账号无权访问该业务模块")
    return spec


def _owned_draft(db: Session, staff: Staff, draft_id: UUID, *, lock: bool = False) -> QuickEntryDraft:
    statement = select(QuickEntryDraft).where(
        QuickEntryDraft.id == str(draft_id),
        QuickEntryDraft.company_id == staff.company_id,
        QuickEntryDraft.staff_id == staff.id,
        QuickEntryDraft.status != "DELETED",
    )
    row = db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "快捷录入草稿不存在或不可见")
    _spec(db, staff, row.document_type)
    if row.expires_at <= _now() and row.status != "EXPIRED":
        row.status = "EXPIRED"
        db.commit()
    return row


def _contract(row: QuickEntryDraft) -> dict:
    return {
        "draft_id": row.id,
        "company_id": row.company_id,
        "staff_id": row.staff_id,
        "module_key": row.module_key,
        "document_type": row.document_type,
        "source_mode": row.source_mode,
        "title": row.title,
        "input_text": row.input_text,
        "draft_data": row.draft_data,
        "status": row.status,
        "version": row.version,
        "applied_version": row.applied_version,
        "expires_at": row.expires_at.isoformat(timespec="seconds"),
        "updated_at": row.updated_at.isoformat(timespec="seconds"),
    }


def _validate_size(data: dict) -> None:
    size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_DRAFT_BYTES:
        raise BizException(ErrorCode.BAD_REQUEST, "草稿内容过大，请精简后重试")


def _audit(db: Session, staff: Staff, row: QuickEntryDraft, action: str) -> None:
    write_audit(
        db,
        action=action,
        target_type="quick_entry_draft",
        target_id=row.id,
        summary="快捷录入私人草稿状态变更",
        detail={"document_type": row.document_type, "status": row.status, "version": row.version},
        actor_id=staff.id,
        actor_name=staff.name,
        company_id=staff.company_id,
    )


@router.get("/catalog", response_model=Result[list])
def get_catalog(staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)):
    permissions = _permissions(db, staff)
    return Result.ok([
        {key: value for key, value in item.items() if key != "permission"}
        for item in DOCUMENT_CATALOG
        if ("*" in permissions or item["permission"] in permissions)
        and ("*" in permissions or f"module:{item['module_key']}" in permissions)
    ])


@router.get("/drafts", response_model=Result[list])
def list_drafts(
    document_type: str | None = None,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    statement = select(QuickEntryDraft).where(
        QuickEntryDraft.company_id == staff.company_id,
        QuickEntryDraft.staff_id == staff.id,
        QuickEntryDraft.status != "DELETED",
    )
    if document_type:
        _spec(db, staff, document_type)
        statement = statement.where(QuickEntryDraft.document_type == document_type)
    rows = db.scalars(statement.order_by(QuickEntryDraft.updated_at.desc()).limit(100)).all()
    for row in rows:
        _spec(db, staff, row.document_type)
        if row.expires_at <= _now() and row.status != "EXPIRED":
            row.status = "EXPIRED"
    db.commit()
    return Result.ok([_contract(row) for row in rows])


@router.post("/drafts", response_model=Result[dict])
def create_draft(
    payload: DraftCreate,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    spec = _spec(db, staff, payload.document_type)
    _validate_size(payload.draft_data)
    body = payload.model_dump(mode="json")
    digest = _hash_payload({key: value for key, value in body.items() if key != "request_id"})
    existing = db.scalar(select(QuickEntryDraft).where(
        QuickEntryDraft.company_id == staff.company_id,
        QuickEntryDraft.staff_id == staff.id,
        QuickEntryDraft.request_id == str(payload.request_id),
    ))
    if existing:
        if existing.request_hash != digest:
            raise BizException(ErrorCode.CONFLICT, "请求标识已用于不同的快捷录入草稿")
        return Result.ok(_contract(existing))

    now = _now()
    row = QuickEntryDraft(
        id=str(uuid4()), company_id=staff.company_id, staff_id=staff.id,
        module_key=spec["module_key"], document_type=payload.document_type,
        source_mode=payload.source_mode, title=payload.title.strip(), input_text=payload.input_text,
        draft_data=payload.draft_data, status="EDITING", version=1,
        request_id=str(payload.request_id), request_hash=digest,
        expires_at=now + timedelta(days=DRAFT_DAYS),
    )
    db.add(row)
    _audit(db, staff, row, "quick_entry.draft.create")
    db.commit()
    db.refresh(row)
    return Result.ok(_contract(row))


@router.get("/drafts/{draft_id}", response_model=Result[dict])
def get_draft(
    draft_id: UUID,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, draft_id)
    return Result.ok(_contract(row))


@router.put("/drafts/{draft_id}", response_model=Result[dict])
def update_draft(
    draft_id: UUID,
    payload: DraftUpdate,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, draft_id, lock=True)
    if row.status == "EXPIRED":
        raise BizException(ErrorCode.CONFLICT, "草稿已过期，不能继续编辑")
    if row.version != payload.draft_version:
        raise BizException(ErrorCode.CONFLICT, "草稿已在其他位置更新，请刷新后重试")
    _validate_size(payload.draft_data)
    row.title = payload.title.strip()
    row.input_text = payload.input_text
    row.source_mode = payload.source_mode
    row.draft_data = payload.draft_data
    row.status = "EDITING"
    row.version += 1
    row.expires_at = _now() + timedelta(days=DRAFT_DAYS)
    _audit(db, staff, row, "quick_entry.draft.update")
    db.commit()
    db.refresh(row)
    return Result.ok(_contract(row))


@router.post("/drafts/{draft_id}/confirm-import", response_model=Result[dict])
def confirm_import(
    draft_id: UUID,
    payload: ImportConfirm,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, draft_id, lock=True)
    if row.expires_at <= _now() or row.status == "EXPIRED":
        row.status = "EXPIRED"
        db.commit()
        raise BizException(ErrorCode.CONFLICT, "草稿已过期，不能导入")
    if row.version != payload.draft_version:
        raise BizException(ErrorCode.CONFLICT, "草稿版本已变化，请刷新并重新确认")
    if row.status != "APPLIED":
        row.status = "APPLIED"
        row.applied_version = row.version
        _audit(db, staff, row, "quick_entry.import")
        db.commit()
        db.refresh(row)
    return Result.ok(_contract(row))


@router.delete("/drafts/{draft_id}", response_model=Result[dict])
def delete_draft(
    draft_id: UUID,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row = _owned_draft(db, staff, draft_id, lock=True)
    row.status = "DELETED"
    row.draft_data = {}
    row.input_text = ""
    _audit(db, staff, row, "quick_entry.draft.delete")
    db.commit()
    return Result.ok({"deleted": True})
