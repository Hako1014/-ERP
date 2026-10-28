from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import prepay_service

router = APIRouter(tags=["采购预付款"])

read_prepay = require_permission("module:purchase", "module:finance")
create_prepay = require_permission("purchase:create")
approve_prepay = require_permission("purchase:approve")
unaudit_prepay = require_permission("purchase:create")


class PrepayCreateRequest(BaseModel):
    supplier_id: int | None = None
    order_id: int | None = None
    prepay_amount: Decimal
    bank: str | None = Field(default=None, max_length=64)
    account_no: str | None = Field(default=None, max_length=64)
    account_balance: Decimal | None = None
    pay_note: str | None = Field(default=None, max_length=255)
    write_off_note: str | None = Field(default=None, max_length=255)


class PrepayUpdateRequest(BaseModel):
    supplier_id: int | None = None
    order_id: int | None = None
    prepay_amount: Decimal | None = None
    bank: str | None = Field(default=None, max_length=64)
    account_no: str | None = Field(default=None, max_length=64)
    account_balance: Decimal | None = None
    pay_note: str | None = Field(default=None, max_length=255)
    write_off_note: str | None = Field(default=None, max_length=255)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/purchase-prepays", response_model=Result[dict])
def list_purchase_prepays(
    supplier_id: int | None = None,
    order_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_prepay),
    db: Session = Depends(get_db),
):
    return Result.ok(
        prepay_service.list_prepays(
            db,
            supplier_id=supplier_id,
            order_id=order_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/purchase-prepays", response_model=Result[dict])
def create_purchase_prepay(
    req: PrepayCreateRequest,
    staff: Staff = Depends(create_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.create_prepay(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        ),
        message="采购预付款申请已创建",
    )


@router.get("/purchase-prepays/{prepay_id}", response_model=Result[dict])
def get_purchase_prepay(
    prepay_id: int,
    staff: Staff = Depends(read_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.get_prepay_or_404(db, prepay_id)
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        )
    )


@router.put("/purchase-prepays/{prepay_id}", response_model=Result[dict])
def update_purchase_prepay(
    prepay_id: int,
    req: PrepayUpdateRequest,
    staff: Staff = Depends(create_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.update_prepay(
        db, prepay_id=prepay_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        ),
        message="采购预付款申请已更新",
    )


@router.delete("/purchase-prepays/{prepay_id}", response_model=Result[dict])
def delete_purchase_prepay(
    prepay_id: int,
    staff: Staff = Depends(create_prepay),
    db: Session = Depends(get_db),
):
    prepay_service.delete_prepay(db, prepay_id=prepay_id, staff=staff)
    return Result.ok(message="采购预付款申请已删除")


@router.post(
    "/purchase-prepays/{prepay_id}/submit", response_model=Result[dict]
)
def submit_purchase_prepay(
    prepay_id: int,
    req: AuditRequest | None = None,
    staff: Staff = Depends(create_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.submit_prepay(
        db,
        prepay_id=prepay_id,
        staff=staff,
        comment=req.comment if req else None,
    )
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        ),
        message="采购预付款申请已提交审批",
    )


@router.post(
    "/purchase-prepays/{prepay_id}/approve", response_model=Result[dict]
)
def approve_purchase_prepay(
    prepay_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.approve_prepay(
        db,
        prepay_id=prepay_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        ),
        message="采购预付款申请审批通过",
    )


@router.post(
    "/purchase-prepays/{prepay_id}/reject", response_model=Result[dict]
)
def reject_purchase_prepay(
    prepay_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.reject_prepay(
        db,
        prepay_id=prepay_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        ),
        message="采购预付款申请审批不通过",
    )


@router.post(
    "/purchase-prepays/{prepay_id}/unaudit", response_model=Result[dict]
)
def unaudit_purchase_prepay(
    prepay_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(unaudit_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.unaudit_prepay(
        db,
        prepay_id=prepay_id,
        staff=staff,
        reason=req.reason,
    )
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        ),
        message="采购预付款申请已弃审并退回待审核",
    )


@router.post(
    "/purchase-prepays/{prepay_id}/terminate", response_model=Result[dict]
)
def terminate_purchase_prepay(
    prepay_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(create_prepay),
    db: Session = Depends(get_db),
):
    prepay = prepay_service.terminate_prepay(
        db,
        prepay_id=prepay_id,
        staff=staff,
        reason=req.reason,
    )
    return Result.ok(
        prepay_service.prepay_dict(
            db, prepay, with_approval=True, with_voucher=True
        ),
        message="采购预付款申请已终止",
    )


@router.get(
    "/purchase-prepays/{prepay_id}/voucher", response_model=Result[dict]
)
def get_purchase_prepay_voucher(
    prepay_id: int,
    staff: Staff = Depends(read_prepay),
    db: Session = Depends(get_db),
):
    voucher = prepay_service.get_prepay_voucher(db, prepay_id)
    return Result.ok(voucher)
