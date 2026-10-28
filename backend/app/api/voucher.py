from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import voucher_service

router = APIRouter(tags=["财务管理-凭证管理"])

read_finance = require_permission("module:finance")
manage_finance = require_permission("finance:manage")


class VoucherItemRequest(BaseModel):
    subject_id: int = Field(..., ge=1)
    summary: str | None = Field(default=None, max_length=255)
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")


class VoucherCreateRequest(BaseModel):
    voucher_word: str = Field(default="记", min_length=1, max_length=8)
    voucher_no: str | None = Field(default=None, max_length=64)
    business_date: datetime | None = None
    record_date: datetime | None = None
    reference: str | None = Field(default=None, max_length=255)
    items: list[VoucherItemRequest] = Field(
        ..., min_length=2, max_length=voucher_service.MAX_VOUCHER_ITEMS
    )


class VoucherUpdateRequest(BaseModel):
    voucher_word: str | None = Field(default=None, min_length=1, max_length=8)
    voucher_no: str | None = Field(default=None, min_length=1, max_length=64)
    business_date: datetime | None = None
    record_date: datetime | None = None
    reference: str | None = Field(default=None, max_length=255)
    items: list[VoucherItemRequest] | None = Field(
        default=None, min_length=2, max_length=voucher_service.MAX_VOUCHER_ITEMS
    )


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/vouchers", response_model=Result[dict])
def list_vouchers(
    status: str | None = None,
    keyword: str | None = Query(default=None, max_length=64),
    business_date_from: datetime | None = None,
    business_date_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        voucher_service.list_vouchers(
            db,
            status=status,
            keyword=keyword,
            business_date_from=business_date_from,
            business_date_to=business_date_to,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/vouchers", response_model=Result[dict])
def create_voucher(
    req: VoucherCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    voucher = voucher_service.create_voucher(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        voucher_service.voucher_dict(db, voucher),
        message="会计凭证已创建",
    )


@router.get("/vouchers/{voucher_id}", response_model=Result[dict])
def get_voucher(
    voucher_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    voucher = voucher_service.get_voucher_or_404(db, voucher_id)
    return Result.ok(voucher_service.voucher_dict(db, voucher))


@router.put("/vouchers/{voucher_id}", response_model=Result[dict])
def update_voucher(
    voucher_id: int,
    req: VoucherUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    voucher = voucher_service.update_voucher(
        db,
        voucher_id=voucher_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        voucher_service.voucher_dict(db, voucher),
        message="会计凭证已更新",
    )


@router.delete("/vouchers/{voucher_id}", response_model=Result[dict])
def delete_voucher(
    voucher_id: int,
    reason: str | None = Query(default=None, max_length=255),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    voucher_service.delete_voucher(
        db,
        voucher_id=voucher_id,
        staff=staff,
        reason=reason,
    )
    return Result.ok(message="会计凭证已作废")


@router.post("/vouchers/{voucher_id}/audit", response_model=Result[dict])
def audit_voucher(
    voucher_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    voucher = voucher_service.audit_voucher(
        db,
        voucher_id=voucher_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        voucher_service.voucher_dict(db, voucher),
        message="会计凭证审核通过",
    )


@router.post("/vouchers/{voucher_id}/unaudit", response_model=Result[dict])
def unaudit_voucher(
    voucher_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    voucher = voucher_service.unaudit_voucher(
        db,
        voucher_id=voucher_id,
        staff=staff,
        reason=req.reason,
    )
    return Result.ok(
        voucher_service.voucher_dict(db, voucher),
        message="会计凭证已反审",
    )


@router.post(
    "/vouchers/{voucher_id}/kingdee-sync", response_model=Result[dict]
)
def sync_voucher_to_kingdee(
    voucher_id: int,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    return Result.ok(
        voucher_service.send_voucher_to_kingdee(
            db, voucher_id=voucher_id, staff=staff
        ),
        message="会计凭证已提交金蝶同步",
    )
