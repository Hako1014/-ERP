from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import stock_out_service

router = APIRouter(tags=["库存管理"])

read_stock = require_permission("module:stock")
manage_stock = require_permission("stock:manage")


class StockOutCreateRequest(BaseModel):
    purchase_return_id: int
    remark: str | None = Field(default=None, max_length=255)


class StockOutUpdateRequest(BaseModel):
    remark: str | None = Field(default=None, max_length=255)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class OffShelfItemRequest(BaseModel):
    stock_out_item_id: int
    location_id: int
    qty: Decimal


class OffShelfRequest(BaseModel):
    items: list[OffShelfItemRequest] = Field(..., min_length=1)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/stock-outs", response_model=Result[dict])
def list_stock_outs(
    warehouse_id: int | None = None,
    purchase_return_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    off_shelf_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_out_service.list_stock_outs(
            db,
            warehouse_id=warehouse_id,
            purchase_return_id=purchase_return_id,
            status=status,
            audit_status=audit_status,
            off_shelf_status=off_shelf_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/stock-outs", response_model=Result[dict])
def create_stock_out(
    req: StockOutCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_out_service.create_stock_out(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_out_service.stock_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购出库单已创建",
    )


@router.get("/stock-outs/{stock_out_id}", response_model=Result[dict])
def get_stock_out(
    stock_out_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = stock_out_service.get_stock_out_or_404(db, stock_out_id)
    return Result.ok(
        stock_out_service.stock_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        )
    )


@router.put("/stock-outs/{stock_out_id}", response_model=Result[dict])
def update_stock_out(
    stock_out_id: int,
    req: StockOutUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_out_service.update_stock_out(
        db,
        stock_out_id=stock_out_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_out_service.stock_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购出库单已更新",
    )


@router.delete("/stock-outs/{stock_out_id}", response_model=Result[dict])
def delete_stock_out(
    stock_out_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    stock_out_service.delete_stock_out(
        db,
        stock_out_id=stock_out_id,
        staff=staff,
    )
    return Result.ok(message="采购出库单已删除")


@router.post("/stock-outs/{stock_out_id}/submit", response_model=Result[dict])
def submit_stock_out(
    stock_out_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_out_service.submit_stock_out(
        db,
        stock_out_id=stock_out_id,
        staff=staff,
    )
    return Result.ok(
        stock_out_service.stock_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购出库单已提交审批",
    )


@router.post("/stock-outs/{stock_out_id}/approve", response_model=Result[dict])
def approve_stock_out(
    stock_out_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_out_service.approve_stock_out(
        db,
        stock_out_id=stock_out_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_out_service.stock_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购出库单审核通过",
    )


@router.post("/stock-outs/{stock_out_id}/reject", response_model=Result[dict])
def reject_stock_out(
    stock_out_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_out_service.reject_stock_out(
        db,
        stock_out_id=stock_out_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_out_service.stock_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购出库单审核不通过",
    )


@router.post(
    "/stock-outs/{stock_out_id}/off-shelf",
    response_model=Result[dict],
)
def off_shelf_stock_out(
    stock_out_id: int,
    req: OffShelfRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_out_service.off_shelf_stock_out(
        db,
        stock_out_id=stock_out_id,
        data={"items": [item.model_dump() for item in req.items]},
        staff=staff,
    )
    return Result.ok(
        stock_out_service.stock_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购出库下架完成",
    )
