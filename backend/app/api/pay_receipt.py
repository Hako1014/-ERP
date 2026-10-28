from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import pay_receipt_service

router = APIRouter(tags=["财务管理-付款与收款"])

read_finance = require_permission("module:finance")
manage_finance = require_permission("finance:manage")


class PaymentCreateRequest(BaseModel):
    supplier_id: int = Field(..., ge=1)
    stock_in_id: int | None = Field(default=None, ge=1)
    invoice_id: int | None = Field(default=None, ge=1)
    income_id: int | None = Field(default=None, ge=1)
    prepay_id: int | None = Field(default=None, ge=1)
    payment_type: str | None = Field(default=None, max_length=32)
    pay_method: str | None = Field(default=None, max_length=32)
    amount: Decimal


class PaymentUpdateRequest(BaseModel):
    supplier_id: int | None = Field(default=None, ge=1)
    stock_in_id: int | None = Field(default=None, ge=1)
    invoice_id: int | None = Field(default=None, ge=1)
    income_id: int | None = Field(default=None, ge=1)
    prepay_id: int | None = Field(default=None, ge=1)
    payment_type: str | None = Field(default=None, max_length=32)
    pay_method: str | None = Field(default=None, max_length=32)
    amount: Decimal | None = None


class ReceiptCreateRequest(BaseModel):
    customer_id: int = Field(..., ge=1)
    order_id: int | None = Field(default=None, ge=1)
    invoice_id: int | None = Field(default=None, ge=1)
    expense_id: int | None = Field(default=None, ge=1)
    sale_type: str | None = Field(default=None, max_length=32)
    settle_type: str | None = Field(default=None, max_length=32)
    amount: Decimal


class ReceiptUpdateRequest(BaseModel):
    customer_id: int | None = Field(default=None, ge=1)
    order_id: int | None = Field(default=None, ge=1)
    invoice_id: int | None = Field(default=None, ge=1)
    expense_id: int | None = Field(default=None, ge=1)
    sale_type: str | None = Field(default=None, max_length=32)
    settle_type: str | None = Field(default=None, max_length=32)
    amount: Decimal | None = None


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/payments", response_model=Result[dict])
def list_payments(
    supplier_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    payment_type: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        pay_receipt_service.list_payments(
            db,
            supplier_id=supplier_id,
            status=status,
            audit_status=audit_status,
            payment_type=payment_type,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/payments", response_model=Result[dict])
def create_payment(
    req: PaymentCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    payment = pay_receipt_service.create_payment(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        pay_receipt_service.payment_dict(
            db, payment, with_approval=True, with_voucher=True
        ),
        message="付款单已创建",
    )


@router.get("/payments/{payment_id}", response_model=Result[dict])
def get_payment(
    payment_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    payment = pay_receipt_service.get_payment_or_404(db, payment_id)
    return Result.ok(
        pay_receipt_service.payment_dict(
            db, payment, with_approval=True, with_voucher=True
        )
    )


@router.put("/payments/{payment_id}", response_model=Result[dict])
def update_payment(
    payment_id: int,
    req: PaymentUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    payment = pay_receipt_service.update_payment(
        db, payment_id=payment_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        pay_receipt_service.payment_dict(
            db, payment, with_approval=True, with_voucher=True
        ),
        message="付款单已更新",
    )


@router.delete("/payments/{payment_id}", response_model=Result[dict])
def cancel_payment(
    payment_id: int,
    reason: str | None = Query(default=None, max_length=255),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    pay_receipt_service.cancel_payment(
        db, payment_id=payment_id, staff=staff, reason=reason
    )
    return Result.ok(message="付款单已作废")


@router.post("/payments/{payment_id}/submit", response_model=Result[dict])
def submit_payment(
    payment_id: int,
    req: AuditRequest | None = None,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    payment = pay_receipt_service.submit_payment(
        db,
        payment_id=payment_id,
        staff=staff,
        comment=req.comment if req else None,
    )
    return Result.ok(
        pay_receipt_service.payment_dict(
            db, payment, with_approval=True, with_voucher=True
        ),
        message="付款单已提交审批",
    )


@router.post("/payments/{payment_id}/approve", response_model=Result[dict])
def approve_payment(
    payment_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    payment = pay_receipt_service.approve_payment(
        db, payment_id=payment_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        pay_receipt_service.payment_dict(
            db, payment, with_approval=True, with_voucher=True
        ),
        message="付款单审核通过",
    )


@router.post("/payments/{payment_id}/reject", response_model=Result[dict])
def reject_payment(
    payment_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    payment = pay_receipt_service.reject_payment(
        db, payment_id=payment_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        pay_receipt_service.payment_dict(
            db, payment, with_approval=True, with_voucher=True
        ),
        message="付款单审核不通过",
    )


@router.post("/payments/{payment_id}/unaudit", response_model=Result[dict])
def unaudit_payment(
    payment_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    payment = pay_receipt_service.unaudit_payment(
        db, payment_id=payment_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        pay_receipt_service.payment_dict(
            db, payment, with_approval=True, with_voucher=True
        ),
        message="付款单已弃审",
    )


@router.get("/payments/{payment_id}/voucher", response_model=Result[dict])
def get_payment_voucher(
    payment_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(pay_receipt_service.get_payment_voucher(db, payment_id))


@router.get("/receipts", response_model=Result[dict])
def list_receipts(
    customer_id: int | None = None,
    order_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    sale_type: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        pay_receipt_service.list_receipts(
            db,
            customer_id=customer_id,
            order_id=order_id,
            status=status,
            audit_status=audit_status,
            sale_type=sale_type,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/receipts", response_model=Result[dict])
def create_receipt(
    req: ReceiptCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    receipt = pay_receipt_service.create_receipt(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        pay_receipt_service.receipt_dict(
            db, receipt, with_approval=True, with_voucher=True
        ),
        message="收款单已创建",
    )


@router.get("/receipts/{receipt_id}", response_model=Result[dict])
def get_receipt(
    receipt_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    receipt = pay_receipt_service.get_receipt_or_404(db, receipt_id)
    return Result.ok(
        pay_receipt_service.receipt_dict(
            db, receipt, with_approval=True, with_voucher=True
        )
    )


@router.put("/receipts/{receipt_id}", response_model=Result[dict])
def update_receipt(
    receipt_id: int,
    req: ReceiptUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    receipt = pay_receipt_service.update_receipt(
        db, receipt_id=receipt_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        pay_receipt_service.receipt_dict(
            db, receipt, with_approval=True, with_voucher=True
        ),
        message="收款单已更新",
    )


@router.delete("/receipts/{receipt_id}", response_model=Result[dict])
def cancel_receipt(
    receipt_id: int,
    reason: str | None = Query(default=None, max_length=255),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    pay_receipt_service.cancel_receipt(
        db, receipt_id=receipt_id, staff=staff, reason=reason
    )
    return Result.ok(message="收款单已作废")


@router.post("/receipts/{receipt_id}/submit", response_model=Result[dict])
def submit_receipt(
    receipt_id: int,
    req: AuditRequest | None = None,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    receipt = pay_receipt_service.submit_receipt(
        db,
        receipt_id=receipt_id,
        staff=staff,
        comment=req.comment if req else None,
    )
    return Result.ok(
        pay_receipt_service.receipt_dict(
            db, receipt, with_approval=True, with_voucher=True
        ),
        message="收款单已提交审批",
    )


@router.post("/receipts/{receipt_id}/approve", response_model=Result[dict])
def approve_receipt(
    receipt_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    receipt = pay_receipt_service.approve_receipt(
        db, receipt_id=receipt_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        pay_receipt_service.receipt_dict(
            db, receipt, with_approval=True, with_voucher=True
        ),
        message="收款单审核通过",
    )


@router.post("/receipts/{receipt_id}/reject", response_model=Result[dict])
def reject_receipt(
    receipt_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    receipt = pay_receipt_service.reject_receipt(
        db, receipt_id=receipt_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        pay_receipt_service.receipt_dict(
            db, receipt, with_approval=True, with_voucher=True
        ),
        message="收款单审核不通过",
    )


@router.post("/receipts/{receipt_id}/unaudit", response_model=Result[dict])
def unaudit_receipt(
    receipt_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    receipt = pay_receipt_service.unaudit_receipt(
        db, receipt_id=receipt_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        pay_receipt_service.receipt_dict(
            db, receipt, with_approval=True, with_voucher=True
        ),
        message="收款单已弃审",
    )


@router.get("/receipts/{receipt_id}/voucher", response_model=Result[dict])
def get_receipt_voucher(
    receipt_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(pay_receipt_service.get_receipt_voucher(db, receipt_id))
