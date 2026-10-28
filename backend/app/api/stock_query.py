from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import stock_query_service

router = APIRouter(tags=["库存管理"])

read_stock = require_permission("module:stock")
manage_stock = require_permission("stock:manage")


class ManualInRequest(BaseModel):
    warehouse_id: int
    location_id: int
    product_id: int
    sku_id: int
    qty: Decimal = Field(..., gt=0)
    cost_price: Decimal | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=255)


class ManualOutRequest(BaseModel):
    warehouse_id: int
    location_id: int
    product_id: int
    sku_id: int
    qty: Decimal = Field(..., gt=0)
    remark: str | None = Field(default=None, max_length=255)


class LocationStockUpdateRequest(BaseModel):
    warehouse_id: int
    location_id: int
    product_id: int
    sku_id: int
    target_qty: Decimal = Field(..., ge=0)
    cost_price: Decimal | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=255)


class LocationAdjustItemRequest(BaseModel):
    product_id: int
    sku_id: int
    from_location_id: int
    to_location_id: int
    qty: Decimal = Field(..., gt=0)


class LocationAdjustCreateRequest(BaseModel):
    warehouse_id: int
    remark: str | None = Field(default=None, max_length=255)
    items: list[LocationAdjustItemRequest] = Field(..., min_length=1)


class LocationAdjustUpdateRequest(BaseModel):
    warehouse_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[LocationAdjustItemRequest] | None = Field(
        default=None,
        min_length=1,
    )


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _adjust_items_payload(
    items: list[LocationAdjustItemRequest],
) -> list[dict]:
    return [item.model_dump() for item in items]


@router.get("/stock-flows", response_model=Result[dict])
def list_stock_flows(
    warehouse_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    location_id: int | None = None,
    direction: Literal["IN", "OUT"] | None = None,
    biz_type: str | None = None,
    source_no: str | None = None,
    order_no: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_query_service.list_stock_flows(
            db,
            warehouse_id=warehouse_id,
            product_id=product_id,
            sku_id=sku_id,
            location_id=location_id,
            direction=direction,
            biz_type=biz_type,
            source_no=source_no,
            order_no=order_no,
            start_time=start_time,
            end_time=end_time,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/stock-summary", response_model=Result[dict])
def list_stock_summary(
    warehouse_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    keyword: str | None = None,
    below_warning: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_query_service.list_stock_summary(
            db,
            warehouse_id=warehouse_id,
            product_id=product_id,
            sku_id=sku_id,
            keyword=keyword,
            below_warning=below_warning,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/stock-locations", response_model=Result[list[dict]])
def list_stock_locations(
    warehouse_id: int | None = None,
    location_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_query_service.list_stock_locations(
            db,
            warehouse_id=warehouse_id,
            location_id=location_id,
            product_id=product_id,
            sku_id=sku_id,
        )
    )


@router.post("/stock/manual-in", response_model=Result[dict])
def manual_in(
    req: ManualInRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_query_service.manual_in(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_query_service.stock_flow_dict(db, row),
        message="手动入库完成",
    )


@router.post("/stock/manual-out", response_model=Result[dict])
def manual_out(
    req: ManualOutRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_query_service.manual_out(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_query_service.stock_flow_dict(db, row),
        message="手动出库完成",
    )


@router.put("/stock/location-stock", response_model=Result[dict])
def set_location_stock(
    req: LocationStockUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_query_service.set_location_stock(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_query_service.stock_flow_dict(db, row) if row else None,
        message="库位库存已更新",
    )


@router.get("/stock-warnings", response_model=Result[list[dict]])
def list_stock_warnings(
    only_below: bool = False,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_query_service.list_stock_warnings(
            db,
            only_below=only_below,
        )
    )


@router.post("/stock-warnings/refresh", response_model=Result[list[dict]])
def refresh_stock_warnings(
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_query_service.refresh_stock_warnings(db, staff=staff),
        message="库存预警已刷新",
    )


@router.get("/stock-costs", response_model=Result[list[dict]])
def list_stock_costs(
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(stock_query_service.list_stock_costs(db))


@router.post("/stock-costs/refresh", response_model=Result[list[dict]])
def refresh_stock_costs(
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_query_service.refresh_stock_costs(db, staff=staff),
        message="库存成本已刷新",
    )


@router.get("/location-adjusts", response_model=Result[dict])
def list_location_adjusts(
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
        stock_query_service.list_location_adjusts(
            db,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/location-adjusts", response_model=Result[dict])
def create_location_adjust(
    req: LocationAdjustCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _adjust_items_payload(req.items)
    row = stock_query_service.create_location_adjust(
        db,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        stock_query_service.location_adjust_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库位调整单已创建",
    )


@router.get("/location-adjusts/{adjust_id}", response_model=Result[dict])
def get_location_adjust(
    adjust_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = stock_query_service.get_location_adjust_or_404(db, adjust_id)
    return Result.ok(
        stock_query_service.location_adjust_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        )
    )


@router.put("/location-adjusts/{adjust_id}", response_model=Result[dict])
def update_location_adjust(
    adjust_id: int,
    req: LocationAdjustUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _adjust_items_payload(req.items)
    row = stock_query_service.update_location_adjust(
        db,
        adjust_id=adjust_id,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        stock_query_service.location_adjust_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库位调整单已更新",
    )


@router.delete("/location-adjusts/{adjust_id}", response_model=Result[dict])
def delete_location_adjust(
    adjust_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    stock_query_service.delete_location_adjust(
        db,
        adjust_id=adjust_id,
        staff=staff,
    )
    return Result.ok(message="库位调整单已删除")


@router.post(
    "/location-adjusts/{adjust_id}/submit",
    response_model=Result[dict],
)
def submit_location_adjust(
    adjust_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_query_service.submit_location_adjust(
        db,
        adjust_id=adjust_id,
        staff=staff,
    )
    return Result.ok(
        stock_query_service.location_adjust_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库位调整单已提交审批",
    )


@router.post(
    "/location-adjusts/{adjust_id}/approve",
    response_model=Result[dict],
)
def approve_location_adjust(
    adjust_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_query_service.approve_location_adjust(
        db,
        adjust_id=adjust_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_query_service.location_adjust_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库位调整单审核通过",
    )


@router.post(
    "/location-adjusts/{adjust_id}/reject",
    response_model=Result[dict],
)
def reject_location_adjust(
    adjust_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_query_service.reject_location_adjust(
        db,
        adjust_id=adjust_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        stock_query_service.location_adjust_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="库位调整单审核不通过",
    )
