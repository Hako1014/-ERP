from datetime import date
from decimal import Decimal
from io import BytesIO

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import finance_doc_service
from app.services.excel import build_template

router = APIRouter(tags=["财务管理-费用结算收入"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

read_finance = require_permission("module:finance")
manage_finance = require_permission("finance:manage")


class ExpenseCreateRequest(BaseModel):
    customer_id: int = Field(..., ge=1)
    expense_type: str | None = Field(default=None, max_length=32)
    amount: Decimal
    description: str | None = Field(default=None, max_length=255)


class ExpenseUpdateRequest(BaseModel):
    customer_id: int | None = Field(default=None, ge=1)
    expense_type: str | None = Field(default=None, max_length=32)
    amount: Decimal | None = None
    description: str | None = Field(default=None, max_length=255)


class IncomeCreateRequest(BaseModel):
    supplier_id: int = Field(..., ge=1)
    income_type: str | None = Field(default=None, max_length=32)
    amount: Decimal
    description: str | None = Field(default=None, max_length=255)


class IncomeUpdateRequest(BaseModel):
    supplier_id: int | None = Field(default=None, ge=1)
    income_type: str | None = Field(default=None, max_length=32)
    amount: Decimal | None = None
    description: str | None = Field(default=None, max_length=255)


class SettlementCreateRequest(BaseModel):
    platform_no: str | None = Field(default=None, max_length=64)
    settlement_date: date | None = None
    shop_id: int = Field(..., ge=1)
    amount: Decimal
    remark: str | None = Field(default=None, max_length=255)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


class SettlementCheckRequest(BaseModel):
    remark: str | None = Field(default=None, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _excel_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# 费用单
# --------------------------------------------------------------------------- #


@router.get("/expenses", response_model=Result[dict])
def list_expenses(
    customer_id: int | None = None,
    status: str | None = None,
    expense_type: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        finance_doc_service.list_expenses(
            db,
            customer_id=customer_id,
            status=status,
            expense_type=expense_type,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/expenses", response_model=Result[dict])
def create_expense(
    req: ExpenseCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    expense = finance_doc_service.create_expense(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        finance_doc_service.expense_dict(
            db, expense, with_approval=True, with_voucher=True
        ),
        message="费用单已创建",
    )


@router.get("/expenses/{expense_id}", response_model=Result[dict])
def get_expense(
    expense_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    expense = finance_doc_service.get_expense_or_404(db, expense_id)
    return Result.ok(
        finance_doc_service.expense_dict(
            db, expense, with_approval=True, with_voucher=True
        )
    )


@router.put("/expenses/{expense_id}", response_model=Result[dict])
def update_expense(
    expense_id: int,
    req: ExpenseUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    expense = finance_doc_service.update_expense(
        db, expense_id=expense_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        finance_doc_service.expense_dict(
            db, expense, with_approval=True, with_voucher=True
        ),
        message="费用单已更新",
    )


@router.delete("/expenses/{expense_id}", response_model=Result[dict])
def cancel_expense(
    expense_id: int,
    reason: str | None = Query(default=None, max_length=255),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    finance_doc_service.cancel_expense(
        db, expense_id=expense_id, staff=staff, reason=reason
    )
    return Result.ok(message="费用单已作废")


@router.post("/expenses/{expense_id}/submit", response_model=Result[dict])
def submit_expense(
    expense_id: int,
    req: AuditRequest | None = None,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    expense = finance_doc_service.submit_expense(
        db,
        expense_id=expense_id,
        staff=staff,
        comment=req.comment if req else None,
    )
    return Result.ok(
        finance_doc_service.expense_dict(
            db, expense, with_approval=True, with_voucher=True
        ),
        message="费用单已提交审批",
    )


@router.post("/expenses/{expense_id}/approve", response_model=Result[dict])
def approve_expense(
    expense_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    expense = finance_doc_service.approve_expense(
        db, expense_id=expense_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        finance_doc_service.expense_dict(
            db, expense, with_approval=True, with_voucher=True
        ),
        message="费用单审核通过",
    )


@router.post("/expenses/{expense_id}/reject", response_model=Result[dict])
def reject_expense(
    expense_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    expense = finance_doc_service.reject_expense(
        db, expense_id=expense_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        finance_doc_service.expense_dict(
            db, expense, with_approval=True, with_voucher=True
        ),
        message="费用单审核不通过",
    )


@router.post("/expenses/{expense_id}/unaudit", response_model=Result[dict])
def unaudit_expense(
    expense_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    expense = finance_doc_service.unaudit_expense(
        db, expense_id=expense_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        finance_doc_service.expense_dict(
            db, expense, with_approval=True, with_voucher=True
        ),
        message="费用单已弃审",
    )


@router.get("/expenses/{expense_id}/voucher", response_model=Result[dict])
def get_expense_voucher(
    expense_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(finance_doc_service.get_expense_voucher(db, expense_id))


# --------------------------------------------------------------------------- #
# 收入单
# --------------------------------------------------------------------------- #


@router.get("/incomes", response_model=Result[dict])
def list_incomes(
    supplier_id: int | None = None,
    status: str | None = None,
    income_type: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        finance_doc_service.list_incomes(
            db,
            supplier_id=supplier_id,
            status=status,
            income_type=income_type,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/incomes", response_model=Result[dict])
def create_income(
    req: IncomeCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    income = finance_doc_service.create_income(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        finance_doc_service.income_dict(
            db, income, with_approval=True, with_voucher=True
        ),
        message="收入单已创建",
    )


@router.get("/incomes/{income_id}", response_model=Result[dict])
def get_income(
    income_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    income = finance_doc_service.get_income_or_404(db, income_id)
    return Result.ok(
        finance_doc_service.income_dict(
            db, income, with_approval=True, with_voucher=True
        )
    )


@router.put("/incomes/{income_id}", response_model=Result[dict])
def update_income(
    income_id: int,
    req: IncomeUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    income = finance_doc_service.update_income(
        db, income_id=income_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        finance_doc_service.income_dict(
            db, income, with_approval=True, with_voucher=True
        ),
        message="收入单已更新",
    )


@router.delete("/incomes/{income_id}", response_model=Result[dict])
def cancel_income(
    income_id: int,
    reason: str | None = Query(default=None, max_length=255),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    finance_doc_service.cancel_income(
        db, income_id=income_id, staff=staff, reason=reason
    )
    return Result.ok(message="收入单已作废")


@router.post("/incomes/{income_id}/submit", response_model=Result[dict])
def submit_income(
    income_id: int,
    req: AuditRequest | None = None,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    income = finance_doc_service.submit_income(
        db,
        income_id=income_id,
        staff=staff,
        comment=req.comment if req else None,
    )
    return Result.ok(
        finance_doc_service.income_dict(
            db, income, with_approval=True, with_voucher=True
        ),
        message="收入单已提交审批",
    )


@router.post("/incomes/{income_id}/approve", response_model=Result[dict])
def approve_income(
    income_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    income = finance_doc_service.approve_income(
        db, income_id=income_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        finance_doc_service.income_dict(
            db, income, with_approval=True, with_voucher=True
        ),
        message="收入单审核通过",
    )


@router.post("/incomes/{income_id}/reject", response_model=Result[dict])
def reject_income(
    income_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    income = finance_doc_service.reject_income(
        db, income_id=income_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        finance_doc_service.income_dict(
            db, income, with_approval=True, with_voucher=True
        ),
        message="收入单审核不通过",
    )


@router.post("/incomes/{income_id}/unaudit", response_model=Result[dict])
def unaudit_income(
    income_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    income = finance_doc_service.unaudit_income(
        db, income_id=income_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        finance_doc_service.income_dict(
            db, income, with_approval=True, with_voucher=True
        ),
        message="收入单已弃审",
    )


@router.get("/incomes/{income_id}/voucher", response_model=Result[dict])
def get_income_voucher(
    income_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(finance_doc_service.get_income_voucher(db, income_id))


# --------------------------------------------------------------------------- #
# 平台结算单
# --------------------------------------------------------------------------- #


@router.get("/settlements/template")
def download_settlement_template(
    staff: Staff = Depends(read_finance),
):
    del staff
    return _excel_response(
        build_template(finance_doc_service.SETTLEMENT_TEMPLATE),
        "platform_settlement.xlsx",
    )


@router.get("/settlements", response_model=Result[dict])
def list_settlements(
    shop_id: int | None = None,
    status: str | None = None,
    settlement_date_from: date | None = None,
    settlement_date_to: date | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        finance_doc_service.list_settlements(
            db,
            shop_id=shop_id,
            status=status,
            settlement_date_from=settlement_date_from,
            settlement_date_to=settlement_date_to,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/settlements/import", response_model=Result[dict])
async def import_settlements(
    file: UploadFile = File(...),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    data = await file.read()
    limit = get_settings().max_upload_size_mb * 1024 * 1024
    if len(data) > limit:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"文件大小不能超过 {limit // (1024 * 1024)}MB",
        )
    return Result.ok(
        finance_doc_service.import_settlements(
            db,
            file_bytes=data,
            file_name=file.filename,
            staff=staff,
        )
    )


@router.post("/settlements", response_model=Result[dict])
def create_settlement(
    req: SettlementCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    settlement = finance_doc_service.create_settlement(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        finance_doc_service.settlement_dict(db, settlement, with_voucher=True),
        message="结算单已创建",
    )


@router.get("/settlements/{settlement_id}", response_model=Result[dict])
def get_settlement(
    settlement_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    settlement = finance_doc_service.get_settlement_or_404(db, settlement_id)
    return Result.ok(
        finance_doc_service.settlement_dict(
            db, settlement, with_voucher=True
        )
    )


@router.post("/settlements/{settlement_id}/check", response_model=Result[dict])
def check_settlement(
    settlement_id: int,
    req: SettlementCheckRequest | None = None,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    settlement = finance_doc_service.check_settlement(
        db,
        settlement_id=settlement_id,
        staff=staff,
        remark=req.remark if req else None,
    )
    return Result.ok(
        finance_doc_service.settlement_dict(
            db, settlement, with_voucher=True
        ),
        message="结算单核对完成",
    )


@router.post(
    "/settlements/{settlement_id}/confirm", response_model=Result[dict]
)
def confirm_settlement(
    settlement_id: int,
    req: SettlementCheckRequest | None = None,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    settlement = finance_doc_service.confirm_settlement(
        db,
        settlement_id=settlement_id,
        staff=staff,
        remark=req.remark if req else None,
    )
    return Result.ok(
        finance_doc_service.settlement_dict(
            db, settlement, with_voucher=True
        ),
        message="结算单确认完成并生成应收款",
    )


@router.delete("/settlements/{settlement_id}", response_model=Result[dict])
def cancel_settlement(
    settlement_id: int,
    reason: str | None = Query(default=None, max_length=255),
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    finance_doc_service.cancel_settlement(
        db, settlement_id=settlement_id, staff=staff, reason=reason
    )
    return Result.ok(message="结算单已作废")


@router.get(
    "/settlements/{settlement_id}/voucher", response_model=Result[dict]
)
def get_settlement_voucher(
    settlement_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        finance_doc_service.get_settlement_voucher(db, settlement_id)
    )
