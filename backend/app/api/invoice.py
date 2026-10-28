from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import invoice_service

router = APIRouter(tags=["财务管理-发票与供应商余额"])

read_finance = require_permission("module:finance")
manage_finance = require_permission("finance:manage")


class InvoiceCreateRequest(BaseModel):
    invoice_title: str | None = Field(default=None, max_length=255)
    tax_no: str | None = Field(default=None, max_length=64)
    invoice_type: str
    amount: Decimal
    received_amount: Decimal = Decimal("0.00")
    is_received: bool = False
    company: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=16)
    applicant: str | None = Field(default=None, max_length=64)


class InvoiceUpdateRequest(BaseModel):
    invoice_title: str | None = Field(default=None, max_length=255)
    tax_no: str | None = Field(default=None, max_length=64)
    invoice_type: str | None = Field(default=None, max_length=32)
    amount: Decimal | None = None
    company: str | None = Field(default=None, max_length=128)
    applicant: str | None = Field(default=None, max_length=64)


class InvoiceReceiveRequest(BaseModel):
    received_amount: Decimal
    is_received: bool | None = None


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/invoices", response_model=Result[dict])
def list_invoices(
    invoice_type: str | None = None,
    status: str | None = None,
    is_received: bool | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        invoice_service.list_invoices(
            db,
            invoice_type=invoice_type,
            status=status,
            is_received=is_received,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/invoices", response_model=Result[dict])
def create_invoice(
    req: InvoiceCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    invoice = invoice_service.create_invoice(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        invoice_service.invoice_dict(invoice),
        message="发票已创建",
    )


@router.get("/invoices/{invoice_id}", response_model=Result[dict])
def get_invoice(
    invoice_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    invoice = invoice_service.get_invoice_or_404(db, invoice_id)
    return Result.ok(invoice_service.invoice_dict(invoice))


@router.put("/invoices/{invoice_id}", response_model=Result[dict])
def update_invoice(
    invoice_id: int,
    req: InvoiceUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    invoice = invoice_service.update_invoice(
        db, invoice_id=invoice_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        invoice_service.invoice_dict(invoice),
        message="发票已更新",
    )


@router.delete("/invoices/{invoice_id}", response_model=Result[dict])
def cancel_invoice(
    invoice_id: int,
    reason: str | None = Query(default=None, max_length=255),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    invoice_service.cancel_invoice(
        db, invoice_id=invoice_id, staff=staff, reason=reason
    )
    return Result.ok(message="发票已作废")


@router.post("/invoices/{invoice_id}/issue", response_model=Result[dict])
def issue_invoice(
    invoice_id: int,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    invoice = invoice_service.issue_invoice(
        db, invoice_id=invoice_id, staff=staff
    )
    return Result.ok(
        invoice_service.invoice_dict(invoice),
        message="发票开票状态已更新",
    )


@router.post("/invoices/{invoice_id}/receive", response_model=Result[dict])
def receive_invoice(
    invoice_id: int,
    req: InvoiceReceiveRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    invoice = invoice_service.receive_invoice(
        db,
        invoice_id=invoice_id,
        received_amount=req.received_amount,
        is_received=req.is_received,
        staff=staff,
    )
    return Result.ok(
        invoice_service.invoice_dict(invoice),
        message="发票到账状态已更新",
    )


@router.get("/supplier-account-balances", response_model=Result[dict])
def list_supplier_account_balances(
    supplier_id: int | None = None,
    sync_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        invoice_service.list_supplier_account_balances(
            db,
            supplier_id=supplier_id,
            sync_status=sync_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/supplier-account-balances/{account_id}", response_model=Result[dict]
)
def get_supplier_account_balance(
    account_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        invoice_service.get_supplier_account_balance(db, account_id)
    )


@router.post(
    "/supplier-account-balances/{account_id}/sync",
    response_model=Result[dict],
)
def sync_supplier_account_balance(
    account_id: int,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    return Result.ok(
        invoice_service.sync_supplier_account_balance(
            db, account_id=account_id, staff=staff
        ),
        message="供应商账户余额同步完成",
    )
