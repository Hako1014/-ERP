from datetime import datetime
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
from app.services import order_service
from app.services import platform_sync_service
from app.services.excel import build_template

router = APIRouter(tags=["订单管理"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

read_order = require_permission("module:order")
manage_order = require_permission("order:audit", "order:ship")
audit_order = require_permission("order:audit")
fulfill_order = require_permission("order:ship", "order:audit")


class OrderItemRequest(BaseModel):
    product_id: int
    sku_id: int | None = None
    sku_name: str | None = Field(default=None, max_length=128)
    qty: Decimal = Field(..., gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    is_gift: bool = False


class OrderCreateRequest(BaseModel):
    order_no: str | None = Field(default=None, max_length=64)
    shop_id: int
    warehouse_id: int | None = None
    logistics_id: int | None = None
    buyer_nick: str | None = Field(default=None, max_length=64)
    buyer_message: str | None = Field(default=None, max_length=255)
    seller_remark: str | None = Field(default=None, max_length=255)
    receiver_name: str | None = Field(default=None, max_length=64)
    receiver_phone: str | None = Field(default=None, max_length=32)
    receiver_province: str | None = Field(default=None, max_length=32)
    receiver_city: str | None = Field(default=None, max_length=32)
    receiver_address: str | None = Field(default=None, max_length=255)
    order_time: datetime | None = None
    pay_time: datetime | None = None
    pay_status: str | None = Field(default=None, max_length=16)
    freight: Decimal | None = Field(default=None, ge=0)
    platform_discount: Decimal | None = Field(default=None, ge=0)
    seller_discount: Decimal | None = Field(default=None, ge=0)
    paid_amount: Decimal | None = Field(default=None, ge=0)
    weight: Decimal | None = Field(default=None, ge=0)
    flags: list[str] = Field(default_factory=list, max_length=20)
    remark: str | None = Field(default=None, max_length=255)
    items: list[OrderItemRequest] = Field(..., min_length=1, max_length=500)


class OrderUpdateRequest(BaseModel):
    order_no: str | None = Field(default=None, max_length=64)
    shop_id: int | None = None
    warehouse_id: int | None = None
    logistics_id: int | None = None
    buyer_nick: str | None = Field(default=None, max_length=64)
    buyer_message: str | None = Field(default=None, max_length=255)
    seller_remark: str | None = Field(default=None, max_length=255)
    receiver_name: str | None = Field(default=None, max_length=64)
    receiver_phone: str | None = Field(default=None, max_length=32)
    receiver_province: str | None = Field(default=None, max_length=32)
    receiver_city: str | None = Field(default=None, max_length=32)
    receiver_address: str | None = Field(default=None, max_length=255)
    order_time: datetime | None = None
    pay_time: datetime | None = None
    pay_status: str | None = Field(default=None, max_length=16)
    freight: Decimal | None = Field(default=None, ge=0)
    platform_discount: Decimal | None = Field(default=None, ge=0)
    seller_discount: Decimal | None = Field(default=None, ge=0)
    paid_amount: Decimal | None = Field(default=None, ge=0)
    weight: Decimal | None = Field(default=None, ge=0)
    flags: list[str] | None = Field(default=None, max_length=20)
    remark: str | None = Field(default=None, max_length=255)
    items: list[OrderItemRequest] | None = Field(
        default=None, min_length=1, max_length=500
    )


class FetchedOrderRequest(OrderCreateRequest):
    order_no: str = Field(..., min_length=1, max_length=64)


class FetchOrdersRequest(BaseModel):
    orders: list[FetchedOrderRequest] = Field(
        ..., min_length=1, max_length=500
    )


class SetWarehouseRequest(BaseModel):
    warehouse_id: int


class SetLogisticsRequest(BaseModel):
    logistics_id: int | None = None
    logistics_company: str | None = Field(default=None, max_length=64)
    tracking_no: str | None = Field(default=None, max_length=64)


class SetFlagsRequest(BaseModel):
    flags: list[str] = Field(..., min_length=1, max_length=20)
    mode: str = Field(default="SET", max_length=16)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class UnauditRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _items_payload(
    items: list[OrderItemRequest] | None,
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


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    limit = get_settings().max_upload_size_mb * 1024 * 1024
    if len(data) > limit:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"文件大小不能超过 {limit // (1024 * 1024)}MB",
        )
    return data


@router.get("/orders/template")
def download_order_template(
    staff: Staff = Depends(read_order),
):
    return _excel_response(
        build_template(order_service.ORDER_IMPORT_TEMPLATE),
        "orders.xlsx",
    )


@router.get("/orders/export")
def export_orders(
    shop_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return _excel_response(
        order_service.export_orders(
            db,
            shop_id=shop_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        ),
        "orders.xlsx",
    )


@router.post("/orders/import", response_model=Result[dict])
async def import_orders(
    file: UploadFile = File(...),
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        order_service.import_orders(
            db,
            file_bytes=await _read_upload(file),
            staff=staff,
        )
    )


@router.post("/orders/fetch", response_model=Result[dict])
def fetch_orders(
    req: FetchOrdersRequest,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    payload = [
        _payload(order)
        for order in req.orders
    ]
    return Result.ok(
        order_service.fetch_orders(
            db,
            orders=payload,
            staff=staff,
        ),
        message="订单抓取完成",
    )


@router.post("/shops/{shop_id}/pull-orders", response_model=Result[dict])
def pull_platform_orders(
    shop_id: int,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    return Result.ok(platform_sync_service.pull_shop_orders(db, shop_id=shop_id, staff=staff))


@router.post("/shops/{shop_id}/pull-orders/async", response_model=Result[dict])
def enqueue_platform_orders(
    shop_id: int,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    from app.integrations.platform_tasks import pull_shop_orders_task

    platform_sync_service._shop_adapter(db, shop_id)
    task = pull_shop_orders_task.delay(shop_id, staff.id)
    return Result.ok({"task_id": task.id}, message="抓单任务已入队")


@router.get("/orders", response_model=Result[dict])
def list_orders(
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    pay_status: str | None = None,
    flag: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        order_service.list_orders(
            db,
            shop_id=shop_id,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            pay_status=pay_status,
            flag=flag,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/orders/flags", response_model=Result[list[dict]])
def list_order_flags(
    staff: Staff = Depends(read_order),
):
    return Result.ok(order_service.list_order_flags())


@router.post("/orders", response_model=Result[dict])
def create_order(
    req: OrderCreateRequest,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _items_payload(req.items)
    order = order_service.create_order(db, data=payload, staff=staff)
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单已创建",
    )


@router.get("/orders/{order_id}", response_model=Result[dict])
def get_order(
    order_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    order = order_service.get_order_or_404(db, order_id)
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        )
    )


@router.put("/orders/{order_id}", response_model=Result[dict])
def update_order(
    order_id: int,
    req: OrderUpdateRequest,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    order = order_service.update_order(
        db,
        order_id=order_id,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单已更新",
    )


@router.delete("/orders/{order_id}", response_model=Result[dict])
def delete_order(
    order_id: int,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    order_service.delete_order(db, order_id=order_id, staff=staff)
    return Result.ok(message="订单已删除")


@router.post("/orders/{order_id}/submit", response_model=Result[dict])
def submit_order(
    order_id: int,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    order = order_service.submit_order(db, order_id=order_id, staff=staff)
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单已提交审核",
    )


@router.post("/orders/{order_id}/approve", response_model=Result[dict])
def approve_order(
    order_id: int,
    req: AuditRequest,
    staff: Staff = Depends(audit_order),
    db: Session = Depends(get_db),
):
    order, checks = order_service.approve_order(
        db,
        order_id=order_id,
        staff=staff,
        comment=req.comment,
    )
    data = order_service.order_dict(
        db, order, with_items=True, with_approval=True
    )
    data["checks"] = checks
    return Result.ok(data, message="订单审核通过")


@router.post("/orders/{order_id}/reject", response_model=Result[dict])
def reject_order(
    order_id: int,
    req: AuditRequest,
    staff: Staff = Depends(audit_order),
    db: Session = Depends(get_db),
):
    order = order_service.reject_order(
        db,
        order_id=order_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单审核不通过",
    )


@router.post("/orders/{order_id}/unaudit", response_model=Result[dict])
def unaudit_order(
    order_id: int,
    req: UnauditRequest,
    staff: Staff = Depends(audit_order),
    db: Session = Depends(get_db),
):
    order = order_service.unaudit_order(
        db,
        order_id=order_id,
        staff=staff,
        reason=req.reason,
    )
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单已弃审",
    )


@router.post("/orders/{order_id}/warehouse", response_model=Result[dict])
def set_order_warehouse(
    order_id: int,
    req: SetWarehouseRequest,
    staff: Staff = Depends(fulfill_order),
    db: Session = Depends(get_db),
):
    order = order_service.set_order_warehouse(
        db,
        order_id=order_id,
        warehouse_id=req.warehouse_id,
        staff=staff,
    )
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单发货仓库已设置",
    )


@router.post("/orders/{order_id}/logistics", response_model=Result[dict])
def set_order_logistics(
    order_id: int,
    req: SetLogisticsRequest,
    staff: Staff = Depends(fulfill_order),
    db: Session = Depends(get_db),
):
    order = order_service.set_order_logistics(
        db,
        order_id=order_id,
        logistics_id=req.logistics_id,
        logistics_company=req.logistics_company,
        tracking_no=req.tracking_no,
        staff=staff,
    )
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单物流已设置",
    )


@router.post("/orders/{order_id}/flags", response_model=Result[dict])
def set_order_flags(
    order_id: int,
    req: SetFlagsRequest,
    staff: Staff = Depends(manage_order),
    db: Session = Depends(get_db),
):
    order = order_service.set_order_flags(
        db,
        order_id=order_id,
        flags=req.flags,
        mode=req.mode,
        staff=staff,
    )
    return Result.ok(
        order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="订单标识已更新",
    )


@router.get("/orders/{order_id}/logs", response_model=Result[list[dict]])
def list_order_logs(
    order_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        order_service.list_order_logs(db, order_id=order_id)
    )
