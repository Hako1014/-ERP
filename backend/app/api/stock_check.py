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
from app.services import stock_check_service
from app.services.excel import build_template

router = APIRouter(tags=["库存管理"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

read_stock = require_permission("module:stock")
manage_stock = require_permission("stock:manage")


class StockCheckItemRequest(BaseModel):
    product_id: int
    sku_id: int
    location_id: int
    actual_qty: Decimal | None = Field(default=None, ge=0)
    barcode: str | None = Field(default=None, max_length=64)


class StockCheckCreateRequest(BaseModel):
    warehouse_id: int
    lock_warehouse: bool = True
    remark: str | None = Field(default=None, max_length=255)
    items: list[StockCheckItemRequest] | None = Field(
        default=None,
        max_length=5000,
    )


class StockCheckUpdateRequest(BaseModel):
    lock_warehouse: bool | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[StockCheckItemRequest] | None = Field(
        default=None,
        min_length=1,
        max_length=5000,
    )


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class TerminateRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _items_payload(
    items: list[StockCheckItemRequest] | None,
) -> list[dict] | None:
    if items is None:
        return None
    return [item.model_dump(exclude_unset=True) for item in items]


def _excel_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stock-checks/template")
def download_stock_check_template(
    staff: Staff = Depends(read_stock),
):
    return _excel_response(
        build_template(stock_check_service.STOCK_CHECK_TEMPLATE),
        "stock_check.xlsx",
    )


@router.get("/stock-checks", response_model=Result[dict])
def list_stock_checks(
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_check_service.list_stock_checks(
            db,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/stock-checks", response_model=Result[dict])
def create_stock_check(
    req: StockCheckCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    row = stock_check_service.create_stock_check(
        db,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        stock_check_service.stock_check_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库存盘点单已创建",
    )


@router.get("/stock-checks/{check_id}/export")
def export_stock_check(
    check_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return _excel_response(
        stock_check_service.export_stock_check(db, check_id=check_id),
        f"stock_check_{check_id}.xlsx",
    )


@router.post("/stock-checks/{check_id}/import", response_model=Result[dict])
async def import_stock_check_result(
    check_id: int,
    file: UploadFile = File(...),
    staff: Staff = Depends(manage_stock),
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
        stock_check_service.import_stock_check_result(
            db,
            check_id=check_id,
            file_bytes=data,
            staff=staff,
        )
    )


@router.get("/stock-checks/{check_id}", response_model=Result[dict])
def get_stock_check(
    check_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = stock_check_service.get_stock_check_or_404(db, check_id)
    return Result.ok(
        stock_check_service.stock_check_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        )
    )


@router.put("/stock-checks/{check_id}", response_model=Result[dict])
def update_stock_check(
    check_id: int,
    req: StockCheckUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    row = stock_check_service.update_stock_check(
        db,
        check_id=check_id,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        stock_check_service.stock_check_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库存盘点单已更新",
    )


@router.delete("/stock-checks/{check_id}", response_model=Result[dict])
def delete_stock_check(
    check_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    stock_check_service.delete_stock_check(
        db,
        check_id=check_id,
        staff=staff,
    )
    return Result.ok(message="库存盘点单已删除")


@router.post("/stock-checks/{check_id}/submit", response_model=Result[dict])
def submit_stock_check(
    check_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_check_service.submit_stock_check(
        db,
        check_id=check_id,
        staff=staff,
    )
    return Result.ok(
        stock_check_service.stock_check_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库存盘点单已提交审批",
    )


@router.post("/stock-checks/{check_id}/approve", response_model=Result[dict])
def approve_stock_check(
    check_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_check_service.approve_stock_check(
        db,
        check_id=check_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_check_service.stock_check_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库存盘点审核通过",
    )


@router.post("/stock-checks/{check_id}/reject", response_model=Result[dict])
def reject_stock_check(
    check_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_check_service.reject_stock_check(
        db,
        check_id=check_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_check_service.stock_check_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库存盘点审核不通过",
    )


@router.post("/stock-checks/{check_id}/terminate", response_model=Result[dict])
def terminate_stock_check(
    check_id: int,
    req: TerminateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_check_service.terminate_stock_check(
        db,
        check_id=check_id,
        staff=staff,
        reason=req.reason,
    )
    return Result.ok(
        stock_check_service.stock_check_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库存盘点单已作废",
    )
