from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.session import get_db
from app.models.order import Order
from app.models.org import Staff
from app.services import ship_service
from app.services import platform_sync_service

router = APIRouter(tags=["订单管理"])

read_order = require_permission("module:order")
manage_ship = require_permission("order:ship")


class ScanShipmentRequest(BaseModel):
    scan_code: str = Field(..., min_length=1, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    qty: Decimal = Field(default=Decimal("1.000"), gt=0)
    tracking_no: str | None = Field(default=None, max_length=64)
    package_no: int | None = Field(default=None, ge=1)
    actual_weight_g: Decimal | None = Field(default=None, ge=0)
    weight_tolerance_g: Decimal | None = Field(default=None, ge=0)
    allow_weight_override: bool = False
    remark: str | None = Field(default=None, max_length=255)


class ShipOrderRequest(BaseModel):
    tracking_no: str | None = Field(default=None, max_length=64)
    package_no: int | None = Field(default=None, ge=1)
    actual_weight_g: Decimal | None = Field(default=None, ge=0)
    weight_tolerance_g: Decimal | None = Field(default=None, ge=0)
    allow_weight_override: bool = False
    remark: str | None = Field(default=None, max_length=255)


class SelfPickupRequest(BaseModel):
    package_no: int | None = Field(default=None, ge=1)
    actual_weight_g: Decimal | None = Field(default=None, ge=0)
    weight_tolerance_g: Decimal | None = Field(default=None, ge=0)
    allow_weight_override: bool = False
    remark: str | None = Field(default=None, max_length=255)


class PlatformSyncRequest(BaseModel):
    status: str = Field(..., max_length=16)
    message: str | None = Field(default=None, max_length=255)


@router.post("/shipments/scan", response_model=Result[dict])
def scan_shipment(
    req: ScanShipmentRequest,
    staff: Staff = Depends(manage_ship),
    db: Session = Depends(get_db),
):
    return Result.ok(
        ship_service.scan_shipment(
            db,
            scan_code=req.scan_code,
            barcode=req.barcode,
            qty=req.qty,
            tracking_no=req.tracking_no,
            package_no=req.package_no,
            actual_weight_g=req.actual_weight_g,
            weight_tolerance_g=req.weight_tolerance_g,
            allow_weight_override=req.allow_weight_override,
            remark=req.remark,
            staff=staff,
        ),
        message="扫描发货已处理",
    )


@router.get("/shipments", response_model=Result[dict])
def list_shipments(
    order_id: int | None = None,
    status: str | None = None,
    tracking_no: str | None = None,
    platform_sync_status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        ship_service.list_shipments(
            db,
            order_id=order_id,
            status=status,
            tracking_no=tracking_no,
            platform_sync_status=platform_sync_status,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/orders/{order_id}/shipments",
    response_model=Result[list[dict]],
)
def list_order_shipments(
    order_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    if db.get(Order, order_id) is None:
        raise BizException(ErrorCode.NOT_FOUND, "订单不存在")
    result = ship_service.list_shipments(
        db,
        order_id=order_id,
        page=1,
        page_size=200,
    )
    return Result.ok(result["items"])


@router.get("/shipments/{shipment_id}", response_model=Result[dict])
def get_shipment(
    shipment_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        ship_service.shipment_dict(
            db,
            ship_service.get_shipment_or_404(db, shipment_id),
        )
    )


@router.post("/orders/{order_id}/ship", response_model=Result[dict])
def ship_order(
    order_id: int,
    req: ShipOrderRequest,
    staff: Staff = Depends(manage_ship),
    db: Session = Depends(get_db),
):
    return Result.ok(
        ship_service.ship_order(
            db,
            order_id=order_id,
            tracking_no=req.tracking_no,
            package_no=req.package_no,
            actual_weight_g=req.actual_weight_g,
            weight_tolerance_g=req.weight_tolerance_g,
            allow_weight_override=req.allow_weight_override,
            remark=req.remark,
            staff=staff,
        ),
        message="订单已发货",
    )


@router.post("/orders/{order_id}/pickup", response_model=Result[dict])
def self_pickup_order(
    order_id: int,
    req: SelfPickupRequest,
    staff: Staff = Depends(manage_ship),
    db: Session = Depends(get_db),
):
    return Result.ok(
        ship_service.self_pickup_order(
            db,
            order_id=order_id,
            package_no=req.package_no,
            actual_weight_g=req.actual_weight_g,
            weight_tolerance_g=req.weight_tolerance_g,
            allow_weight_override=req.allow_weight_override,
            remark=req.remark,
            staff=staff,
        ),
        message="订单自提发货完成",
    )


@router.post(
    "/shipments/{shipment_id}/platform-sync",
    response_model=Result[dict],
)
def record_platform_sync(
    shipment_id: int,
    req: PlatformSyncRequest,
    staff: Staff = Depends(manage_ship),
    db: Session = Depends(get_db),
):
    shipment = ship_service.record_platform_sync(
        db,
        shipment_id=shipment_id,
        status=req.status,
        message=req.message,
        staff=staff,
    )
    return Result.ok(
        ship_service.shipment_dict(db, shipment),
        message="平台回传状态已更新",
    )


@router.post("/shipments/{shipment_id}/platform-push", response_model=Result[dict])
def push_shipment_to_platform(
    shipment_id: int,
    staff: Staff = Depends(manage_ship),
    db: Session = Depends(get_db),
):
    return Result.ok(platform_sync_service.push_shipment(db, shipment_id=shipment_id, staff=staff))
