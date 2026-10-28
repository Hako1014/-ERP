from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_staff, require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import approval_service

router = APIRouter(prefix="/approval", tags=["协同办公-审批"])


class FlowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    doc_type: str = Field(..., min_length=1, max_length=32)
    approvers: list[Any] = Field(..., min_length=1)


class FlowUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    doc_type: str | None = Field(default=None, min_length=1, max_length=32)
    approvers: list[Any] | None = None


class SubmitRequest(BaseModel):
    doc_no: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=255)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class UnauditRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)
    step: int | None = Field(default=None, ge=1)


@router.get("/flows", response_model=Result[list[dict]])
def list_flows(
    staff: Staff = Depends(require_permission("module:approval")),
    db: Session = Depends(get_db),
):
    from app.services.shared_queries import approval_flows

    return Result.ok(approval_flows(db))


@router.post("/flows", response_model=Result[dict])
def create_flow(
    req: FlowCreateRequest,
    staff: Staff = Depends(require_permission("approval:configure")),
    db: Session = Depends(get_db),
):
    flow = approval_service.create_flow(
        db,
        name=req.name,
        doc_type=req.doc_type,
        approvers=req.approvers,
        actor=staff,
    )
    return Result.ok(approval_service.flow_dict(flow), message="审批流已创建")


@router.put("/flows/{flow_id}", response_model=Result[dict])
def update_flow(
    flow_id: int,
    req: FlowUpdateRequest,
    staff: Staff = Depends(require_permission("approval:configure")),
    db: Session = Depends(get_db),
):
    flow = approval_service.update_flow(
        db,
        flow_id=flow_id,
        name=req.name,
        doc_type=req.doc_type,
        approvers=req.approvers,
        actor=staff,
    )
    return Result.ok(approval_service.flow_dict(flow), message="审批流已更新")


@router.get("/records/{doc_type}/{doc_id}", response_model=Result[dict])
def get_records(
    doc_type: str,
    doc_id: int,
    staff: Staff = Depends(require_permission("module:approval")),
    db: Session = Depends(get_db),
):
    from app.services.shared_queries import approval_state

    return Result.ok(approval_state(db, doc_type=doc_type, doc_id=doc_id))


@router.post("/records/{doc_type}/{doc_id}/submit", response_model=Result[dict])
def submit(
    doc_type: str,
    doc_id: int,
    req: SubmitRequest,
    staff: Staff = Depends(require_permission("module:approval")),
    db: Session = Depends(get_db),
):
    state = approval_service.submit(
        db,
        doc_type=doc_type,
        doc_id=doc_id,
        staff=staff,
        doc_no=req.doc_no,
        comment=req.comment,
    )
    return Result.ok(state, message="已提交审批")


@router.post("/records/{doc_type}/{doc_id}/approve", response_model=Result[dict])
def approve(
    doc_type: str,
    doc_id: int,
    req: AuditRequest,
    staff: Staff = Depends(require_permission("approval:approve")),
    db: Session = Depends(get_db),
):
    state = approval_service.approve(
        db, doc_type=doc_type, doc_id=doc_id, staff=staff, comment=req.comment
    )
    return Result.ok(state, message="审核通过")


@router.post("/records/{doc_type}/{doc_id}/reject", response_model=Result[dict])
def reject(
    doc_type: str,
    doc_id: int,
    req: AuditRequest,
    staff: Staff = Depends(require_permission("approval:approve")),
    db: Session = Depends(get_db),
):
    state = approval_service.reject(
        db, doc_type=doc_type, doc_id=doc_id, staff=staff, comment=req.comment
    )
    return Result.ok(state, message="审核不通过")


@router.post("/records/{doc_type}/{doc_id}/unaudit", response_model=Result[dict])
def unaudit(
    doc_type: str,
    doc_id: int,
    req: UnauditRequest,
    staff: Staff = Depends(require_permission("module:approval")),
    db: Session = Depends(get_db),
):
    state = approval_service.unaudit(
        db,
        doc_type=doc_type,
        doc_id=doc_id,
        staff=staff,
        reason=req.reason,
        step=req.step,
    )
    return Result.ok(state, message="已弃审并退回待审核")
