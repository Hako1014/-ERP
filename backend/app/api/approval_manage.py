from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import approval_manage_service

router = APIRouter(prefix="/approval-manage", tags=["协同办公-审批管理与我的审批"])

read_approval = require_permission("module:approval")
configure_flow = require_permission("approval:configure")
approve_document = require_permission("approval:approve")


class FlowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    doc_type: str = Field(..., min_length=1, max_length=32)
    approvers: list[Any] = Field(..., min_length=1)


class FlowUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    doc_type: str | None = Field(default=None, min_length=1, max_length=32)
    approvers: list[Any] | None = None


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class UnauditRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)
    step: int | None = Field(default=None, ge=1)


@router.get("/doc-types", response_model=Result[list[dict]])
def list_doc_types(
    staff: Staff = Depends(read_approval),
):
    del staff
    return Result.ok(approval_manage_service.list_doc_types())


@router.get("/flows", response_model=Result[list[dict]])
def list_flows(
    staff: Staff = Depends(configure_flow),
    db: Session = Depends(get_db),
):
    del staff
    from app.services.shared_queries import approval_flows

    return Result.ok(approval_flows(db, configuration=True))


@router.post("/flows", response_model=Result[dict])
def create_flow(
    req: FlowCreateRequest,
    staff: Staff = Depends(configure_flow),
    db: Session = Depends(get_db),
):
    flow = approval_manage_service.create_manage_flow(
        db,
        name=req.name,
        doc_type=req.doc_type,
        approvers=req.approvers,
        staff=staff,
    )
    return Result.ok(flow, message="审批流已创建")


@router.put("/flows/{flow_id}", response_model=Result[dict])
def update_flow(
    flow_id: int,
    req: FlowUpdateRequest,
    staff: Staff = Depends(configure_flow),
    db: Session = Depends(get_db),
):
    flow = approval_manage_service.update_manage_flow(
        db,
        flow_id=flow_id,
        name=req.name,
        doc_type=req.doc_type,
        approvers=req.approvers,
        staff=staff,
    )
    return Result.ok(flow, message="审批流已更新")


@router.get("/my-approvals", response_model=Result[dict])
def list_my_approvals(
    scope: Literal["pending", "submitted", "processed"] = "pending",
    doc_type: str | None = None,
    keyword: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_approval),
    db: Session = Depends(get_db),
):
    return Result.ok(
        approval_manage_service.list_my_approvals(
            db,
            staff=staff,
            scope=scope,
            doc_type=doc_type,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/my-approvals/{doc_type}/{doc_id}", response_model=Result[dict])
def get_my_approval_detail(
    doc_type: str,
    doc_id: int,
    staff: Staff = Depends(read_approval),
    db: Session = Depends(get_db),
):
    return Result.ok(
        approval_manage_service.get_my_approval_detail(
            db,
            doc_type=doc_type,
            doc_id=doc_id,
            staff=staff,
        )
    )


@router.post(
    "/my-approvals/{doc_type}/{doc_id}/approve",
    response_model=Result[dict],
)
def approve_my_approval(
    doc_type: str,
    doc_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_document),
    db: Session = Depends(get_db),
):
    return Result.ok(
        approval_manage_service.approve_my_approval(
            db,
            doc_type=doc_type,
            doc_id=doc_id,
            staff=staff,
            comment=req.comment,
        ),
        message="审核通过",
    )


@router.post(
    "/my-approvals/{doc_type}/{doc_id}/reject",
    response_model=Result[dict],
)
def reject_my_approval(
    doc_type: str,
    doc_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_document),
    db: Session = Depends(get_db),
):
    return Result.ok(
        approval_manage_service.reject_my_approval(
            db,
            doc_type=doc_type,
            doc_id=doc_id,
            staff=staff,
            comment=req.comment,
        ),
        message="审核不通过",
    )


@router.post(
    "/my-approvals/{doc_type}/{doc_id}/unaudit",
    response_model=Result[dict],
)
def unaudit_my_approval(
    doc_type: str,
    doc_id: int,
    req: UnauditRequest,
    staff: Staff = Depends(read_approval),
    db: Session = Depends(get_db),
):
    return Result.ok(
        approval_manage_service.unaudit_my_approval(
            db,
            doc_type=doc_type,
            doc_id=doc_id,
            staff=staff,
            reason=req.reason,
            step=req.step,
        ),
        message="已弃审并退回待审核",
    )
