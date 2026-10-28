from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import purchase_invoice_service

router = APIRouter(tags=["采购发票"])

read_invoice = require_permission("module:purchase", "module:finance")
manage_invoice = require_permission("finance:manage")


class InvoiceCreateRequest(BaseModel):
    supplier_id: int
    invoice_type: str
    tax_rate: Decimal | None = None
    amount: Decimal
    business_type: str | None = Field(default=None, max_length=32)
    voucher_no: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    stock_in_ids: list[int] = Field(default_factory=list, max_length=200)


class InvoiceUpdateRequest(BaseModel):
    supplier_id: int | None = None
    invoice_type: str | None = None
    tax_rate: Decimal | None = None
    amount: Decimal | None = None
    business_type: str | None = Field(default=None, max_length=32)
    voucher_no: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    stock_in_ids: list[int] | None = Field(default=None, max_length=200)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/purchase-invoices", response_model=Result[dict])
def list_purchase_invoices(
    supplier_id: int | None = None,
    invoice_type: str | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    write_off_status: str | None = None,
    business_type: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_invoice),
    db: Session = Depends(get_db),
):
    return Result.ok(
        purchase_invoice_service.list_invoices(
            db,
            supplier_id=supplier_id,
            invoice_type=invoice_type,
            status=status,
            audit_status=audit_status,
            write_off_status=write_off_status,
            business_type=business_type,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/purchase-invoices", response_model=Result[dict])
def create_purchase_invoice(
    req: InvoiceCreateRequest,
    staff: Staff = Depends(manage_invoice),
    db: Session = Depends(get_db),
):
    invoice = purchase_invoice_service.create_invoice(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        purchase_invoice_service.invoice_dict(
            db, invoice, with_approval=True
        ),
        message="采购发票已创建",
    )


@router.get("/purchase-invoices/{invoice_id}", response_model=Result[dict])
def get_purchase_invoice(
    invoice_id: int,
    staff: Staff = Depends(read_invoice),
    db: Session = Depends(get_db),
):
    invoice = purchase_invoice_service.get_invoice_or_404(db, invoice_id)
    return Result.ok(
        purchase_invoice_service.invoice_dict(
            db, invoice, with_approval=True
        )
    )


@router.put("/purchase-invoices/{invoice_id}", response_model=Result[dict])
def update_purchase_invoice(
    invoice_id: int,
    req: InvoiceUpdateRequest,
    staff: Staff = Depends(manage_invoice),
    db: Session = Depends(get_db),
):
    invoice = purchase_invoice_service.update_invoice(
        db, invoice_id=invoice_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        purchase_invoice_service.invoice_dict(
            db, invoice, with_approval=True
        ),
        message="采购发票已更新",
    )


@router.delete("/purchase-invoices/{invoice_id}", response_model=Result[dict])
def delete_purchase_invoice(
    invoice_id: int,
    staff: Staff = Depends(manage_invoice),
    db: Session = Depends(get_db),
):
    purchase_invoice_service.delete_invoice(
        db, invoice_id=invoice_id, staff=staff
    )
    return Result.ok(message="采购发票已删除")


@router.post(
    "/purchase-invoices/{invoice_id}/submit", response_model=Result[dict]
)
def submit_purchase_invoice(
    invoice_id: int,
    req: AuditRequest | None = None,
    staff: Staff = Depends(manage_invoice),
    db: Session = Depends(get_db),
):
    invoice = purchase_invoice_service.submit_invoice(
        db,
        invoice_id=invoice_id,
        staff=staff,
        comment=req.comment if req else None,
    )
    return Result.ok(
        purchase_invoice_service.invoice_dict(
            db, invoice, with_approval=True
        ),
        message="采购发票已提交审核",
    )


@router.post(
    "/purchase-invoices/{invoice_id}/approve", response_model=Result[dict]
)
def approve_purchase_invoice(
    invoice_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_invoice),
    db: Session = Depends(get_db),
):
    invoice = purchase_invoice_service.approve_invoice(
        db,
        invoice_id=invoice_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        purchase_invoice_service.invoice_dict(
            db, invoice, with_approval=True
        ),
        message="采购发票审核通过",
    )


@router.post(
    "/purchase-invoices/{invoice_id}/reject", response_model=Result[dict]
)
def reject_purchase_invoice(
    invoice_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_invoice),
    db: Session = Depends(get_db),
):
    invoice = purchase_invoice_service.reject_invoice(
        db,
        invoice_id=invoice_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        purchase_invoice_service.invoice_dict(
            db, invoice, with_approval=True
        ),
        message="采购发票审核不通过",
    )


@router.post(
    "/purchase-invoices/{invoice_id}/unaudit", response_model=Result[dict]
)
def unaudit_purchase_invoice(
    invoice_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(manage_invoice),
    db: Session = Depends(get_db),
):
    invoice = purchase_invoice_service.unaudit_invoice(
        db, invoice_id=invoice_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        purchase_invoice_service.invoice_dict(
            db, invoice, with_approval=True
        ),
        message="采购发票已弃审并恢复入库成本",
    )


@router.get(
    "/purchase-invoices/{invoice_id}/cost-detail",
    response_model=Result[list[dict]],
)
def get_purchase_invoice_cost_detail(
    invoice_id: int,
    staff: Staff = Depends(read_invoice),
    db: Session = Depends(get_db),
):
    return Result.ok(
        purchase_invoice_service.get_stock_in_cost_detail(db, invoice_id)
    )
