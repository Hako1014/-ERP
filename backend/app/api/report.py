from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import metrics_service

router = APIRouter(tags=["报表与看板"])

read_purchase_report = require_permission("module:purchase")


@router.get("/reports/purchase-orders", response_model=Result[dict])
def get_purchase_order_report(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    supplier_id: int | None = None,
    staff: Staff = Depends(read_purchase_report),
    db: Session = Depends(get_db),
):
    return Result.ok(
        metrics_service.purchase_order_report(
            db,
            start_time=start_time,
            end_time=end_time,
            supplier_id=supplier_id,
        )
    )


@router.get("/reports/purchase-inbounds", response_model=Result[dict])
def get_purchase_inbound_report(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    supplier_id: int | None = None,
    purchaser_id: int | None = None,
    product_id: int | None = None,
    staff: Staff = Depends(read_purchase_report),
    db: Session = Depends(get_db),
):
    return Result.ok(
        metrics_service.purchase_inbound_report(
            db,
            start_time=start_time,
            end_time=end_time,
            supplier_id=supplier_id,
            purchaser_id=purchaser_id,
            product_id=product_id,
        )
    )


@router.get("/reports/purchase-invoices", response_model=Result[dict])
def get_purchase_invoice_report(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    supplier_id: int | None = None,
    write_off_status: str | None = None,
    staff: Staff = Depends(read_purchase_report),
    db: Session = Depends(get_db),
):
    return Result.ok(
        metrics_service.purchase_invoice_report(
            db,
            start_time=start_time,
            end_time=end_time,
            supplier_id=supplier_id,
            write_off_status=write_off_status,
        )
    )


@router.get("/reports/inventory-in-transit", response_model=Result[dict])
def get_inventory_in_transit_report(
    warehouse_id: int | None = None,
    supplier_id: int | None = None,
    product_id: int | None = None,
    keyword: str | None = None,
    staff: Staff = Depends(read_purchase_report),
    db: Session = Depends(get_db),
):
    return Result.ok(
        metrics_service.inventory_in_transit_report(
            db,
            warehouse_id=warehouse_id,
            supplier_id=supplier_id,
            product_id=product_id,
            keyword=keyword,
        )
    )
