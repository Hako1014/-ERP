from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import stock_in_service

router = APIRouter(tags=["库存管理"])

read_stock = require_permission("module:stock")
manage_stock = require_permission("stock:manage")

StockInType = Literal[
    "PURCHASE",
    "MANUAL",
    "AFTER_SALE",
    "TRANSFER",
    "CHECK",
    "PUSH",
    "ADJUST",
]


class StockInItemRequest(BaseModel):
    product_id: int
    sku_id: int | None = None
    qty: Decimal
    cost_price: Decimal | None = None
    barcode: str | None = Field(default=None, max_length=64)


class StockInCreateRequest(BaseModel):
    warehouse_id: int
    purchase_order_id: int | None = None
    stock_in_type: StockInType = "PURCHASE"
    remark: str | None = Field(default=None, max_length=255)
    items: list[StockInItemRequest] = Field(..., min_length=1, max_length=500)


class StockInUpdateRequest(BaseModel):
    warehouse_id: int | None = None
    purchase_order_id: int | None = None
    stock_in_type: StockInType | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[StockInItemRequest] | None = Field(
        default=None, min_length=1, max_length=500
    )


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class PutawayItemRequest(BaseModel):
    stock_in_item_id: int
    location_id: int
    qty: Decimal


class PutawayRequest(BaseModel):
    items: list[PutawayItemRequest] = Field(..., min_length=1)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _items_payload(items: list[StockInItemRequest]) -> list[dict]:
    return [item.model_dump(exclude_unset=True) for item in items]


@router.get("/stock-ins/putaway/pending", response_model=Result[list[dict]])
def list_pending_putaways(
    warehouse_id: int | None = None,
    keyword: str | None = None,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_in_service.list_pending_putaways(
            db,
            warehouse_id=warehouse_id,
            keyword=keyword,
        )
    )


@router.get("/putaways", response_model=Result[list[dict]])
def list_putaways(
    warehouse_id: int | None = None,
    location_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    stock_in_id: int | None = None,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    rows = stock_in_service.list_putaways(
        db,
        warehouse_id=warehouse_id,
        location_id=location_id,
        product_id=product_id,
        sku_id=sku_id,
        stock_in_id=stock_in_id,
    )
    return Result.ok([stock_in_service.putaway_dict(db, row) for row in rows])


@router.get("/stock-ins", response_model=Result[dict])
def list_stock_ins(
    warehouse_id: int | None = None,
    purchase_order_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_in_service.list_stock_ins(
            db,
            warehouse_id=warehouse_id,
            purchase_order_id=purchase_order_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/stock-ins", response_model=Result[dict])
def create_stock_in(
    req: StockInCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _items_payload(req.items)
    row = stock_in_service.create_stock_in(db, data=payload, staff=staff)
    return Result.ok(
        stock_in_service.stock_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购入库单已创建",
    )


@router.get("/stock-ins/{stock_in_id}", response_model=Result[dict])
def get_stock_in(
    stock_in_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = stock_in_service.get_stock_in_or_404(db, stock_in_id)
    return Result.ok(
        stock_in_service.stock_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        )
    )


@router.put("/stock-ins/{stock_in_id}", response_model=Result[dict])
def update_stock_in(
    stock_in_id: int,
    req: StockInUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    row = stock_in_service.update_stock_in(
        db,
        stock_in_id=stock_in_id,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        stock_in_service.stock_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购入库单已更新",
    )


@router.delete("/stock-ins/{stock_in_id}", response_model=Result[dict])
def delete_stock_in(
    stock_in_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    stock_in_service.delete_stock_in(
        db,
        stock_in_id=stock_in_id,
        staff=staff,
    )
    return Result.ok(message="采购入库单已删除")


@router.post("/stock-ins/{stock_in_id}/submit", response_model=Result[dict])
def submit_stock_in(
    stock_in_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_in_service.submit_stock_in(
        db,
        stock_in_id=stock_in_id,
        staff=staff,
    )
    return Result.ok(
        stock_in_service.stock_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购入库单已提交审批",
    )


@router.post("/stock-ins/{stock_in_id}/approve", response_model=Result[dict])
def approve_stock_in(
    stock_in_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_in_service.approve_stock_in(
        db,
        stock_in_id=stock_in_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_in_service.stock_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购入库单审核通过",
    )


@router.post("/stock-ins/{stock_in_id}/reject", response_model=Result[dict])
def reject_stock_in(
    stock_in_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_in_service.reject_stock_in(
        db,
        stock_in_id=stock_in_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_in_service.stock_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="采购入库单审核不通过",
    )


@router.post(
    "/stock-ins/{stock_in_id}/putaway",
    response_model=Result[list[dict]],
)
def putaway_stock(
    stock_in_id: int,
    req: PutawayRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    rows = stock_in_service.putaway_stock(
        db,
        stock_in_id=stock_in_id,
        data={"items": [item.model_dump() for item in req.items]},
        staff=staff,
    )
    return Result.ok(
        [stock_in_service.putaway_dict(db, row) for row in rows],
        message="商品已上架",
    )
