from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.models.purchase import PurchasePlanAttachment
from app.services import purchase_plan_service
from app.services.assistant_attachment_files import PrivateAttachmentStorage
from sqlalchemy import select
from urllib.parse import quote

router = APIRouter(tags=["采购计划"])

read_purchase = require_permission("module:purchase")
create_purchase = require_permission("purchase:create")
approve_purchase = require_permission("purchase:approve")
unaudit_purchase = require_permission("purchase:create", "purchase:approve")


class PlanItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    sku_id: int | None = None
    planned_qty: Decimal
    purchase_price: Decimal | None = None


class PlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: int
    plan_date: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[PlanItemRequest] = Field(..., min_length=1)


class PlanUpdateRequest(BaseModel):
    plan_date: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)


class BatchPlanItemsRequest(BaseModel):
    item_ids: list[int] = Field(..., min_length=1)
    planned_qty: Decimal | None = None
    purchase_price: Decimal | None = None


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class UnauditRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/purchase-plans/needs", response_model=Result[dict])
def preview_purchase_needs(
    supplier_id: int,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    return Result.ok(
        purchase_plan_service.preview_purchase_needs(
            db,
            supplier_id=supplier_id,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/purchase-plans", response_model=Result[dict])
def list_purchase_plans(
    supplier_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    return Result.ok(
        purchase_plan_service.list_plans(
            db,
            supplier_id=supplier_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/purchase-plans", response_model=Result[dict])
def create_purchase_plan(
    req: PlanCreateRequest,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    assistant_draft_id: str | None = Header(
        default=None, alias="X-Assistant-Draft-Id", max_length=36
    ),
):
    payload = _payload(req)
    payload["items"] = [
        item.model_dump(exclude_unset=True) for item in req.items
    ]
    if assistant_draft_id and not idempotency_key:
        raise BizException(ErrorCode.BAD_REQUEST, "助手草稿创建必须提供幂等请求标识")
    if idempotency_key:
        result = purchase_plan_service.create_plan_idempotent(
            db,
            data=payload,
            staff=staff,
            request_id=idempotency_key,
            assistant_draft_id=assistant_draft_id,
        )
        return Result.ok(result, message="采购计划已生成")

    plan = purchase_plan_service.create_plan(db, data=payload, staff=staff)
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划已生成",
    )


@router.get(
    "/purchase-plans/creation-requests/{request_id}", response_model=Result[dict]
)
def get_purchase_plan_creation_request(
    request_id: str,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    return Result.ok(
        purchase_plan_service.get_creation_request_result(
            db, staff=staff, request_id=request_id
        )
    )


@router.get("/purchase-plans/{plan_id}", response_model=Result[dict])
def get_purchase_plan(
    plan_id: int,
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.get_plan_or_404(db, plan_id)
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        )
    )


@router.get("/purchase-plans/{plan_id}/attachments/{attachment_id}/content")
def download_purchase_plan_attachment(
    plan_id: int,
    attachment_id: str,
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.get_plan_or_404(
        db, plan_id, company_id=staff.company_id
    )
    attachment = db.scalar(select(PurchasePlanAttachment).where(
        PurchasePlanAttachment.id == attachment_id,
        PurchasePlanAttachment.plan_id == plan.id,
        PurchasePlanAttachment.company_id == staff.company_id,
    ))
    if attachment is None:
        raise BizException(ErrorCode.NOT_FOUND, "采购计划附件不存在或不可见")
    data = PrivateAttachmentStorage().read(attachment.storage_key)
    return Response(
        content=data,
        media_type=attachment.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.display_name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.put("/purchase-plans/{plan_id}", response_model=Result[dict])
def update_purchase_plan(
    plan_id: int,
    req: PlanUpdateRequest,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.update_plan(
        db, plan_id=plan_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划已更新",
    )


@router.put("/purchase-plans/{plan_id}/items/batch", response_model=Result[dict])
def batch_update_purchase_plan_items(
    plan_id: int,
    req: BatchPlanItemsRequest,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.batch_update_plan_items(
        db, plan_id=plan_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="计划明细已批量更新",
    )


@router.delete("/purchase-plans/{plan_id}", response_model=Result[dict])
def delete_purchase_plan(
    plan_id: int,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    purchase_plan_service.delete_plan(db, plan_id=plan_id, staff=staff)
    return Result.ok(message="采购计划已删除")


@router.post("/purchase-plans/{plan_id}/submit", response_model=Result[dict])
def submit_purchase_plan(
    plan_id: int,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.submit_plan(db, plan_id=plan_id, staff=staff)
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划已提交审批",
    )


@router.post("/purchase-plans/{plan_id}/approve", response_model=Result[dict])
def approve_purchase_plan(
    plan_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.approve_plan(
        db, plan_id=plan_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划审核通过",
    )


@router.post("/purchase-plans/{plan_id}/reject", response_model=Result[dict])
def reject_purchase_plan(
    plan_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.reject_plan(
        db, plan_id=plan_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划审核不通过",
    )


@router.post("/purchase-plans/{plan_id}/unaudit", response_model=Result[dict])
def unaudit_purchase_plan(
    plan_id: int,
    req: UnauditRequest,
    staff: Staff = Depends(unaudit_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.unaudit_plan(
        db, plan_id=plan_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划已弃审并退回待审核",
    )


@router.post("/purchase-plans/{plan_id}/abort", response_model=Result[dict])
def abort_purchase_plan(
    plan_id: int,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.abort_plan(db, plan_id=plan_id, staff=staff)
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划已中止",
    )


@router.post("/purchase-plans/{plan_id}/finish", response_model=Result[dict])
def finish_purchase_plan(
    plan_id: int,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    plan = purchase_plan_service.finish_plan(db, plan_id=plan_id, staff=staff)
    return Result.ok(
        purchase_plan_service.plan_dict(
            db, plan, with_items=True, with_approval=True
        ),
        message="采购计划已完成",
    )
