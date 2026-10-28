from datetime import datetime
from decimal import Decimal
from io import BytesIO

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import after_sale_service

router = APIRouter(tags=["订单售后"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

read_order = require_permission("module:order")
manage_after_sale = require_permission("after_sale:manage")
confirm_return = require_permission("after_sale:manage", "finance:manage")


class ReturnItemRequest(BaseModel):
    product_id: int
    sku_id: int | None = None
    qty: Decimal = Field(..., gt=0)
    refund_amount: Decimal | None = Field(default=None, ge=0)


class ReturnCreateRequest(BaseModel):
    order_id: int
    warehouse_id: int
    reason: str | None = Field(default=None, max_length=128)
    logistics_company: str | None = Field(default=None, max_length=64)
    tracking_no: str | None = Field(default=None, max_length=64)
    buyer_phone: str | None = Field(default=None, max_length=32)
    is_arrived: bool = False
    freight: Decimal | None = Field(default=None, ge=0)
    is_rejected_sign: bool = False
    return_person: str | None = Field(default=None, max_length=64)
    goods_status: str | None = Field(default=None, max_length=32)
    need_return_goods: bool = False
    items: list[ReturnItemRequest] = Field(..., min_length=1, max_length=500)


class ReturnUpdateRequest(BaseModel):
    order_id: int | None = None
    warehouse_id: int | None = None
    reason: str | None = Field(default=None, max_length=128)
    logistics_company: str | None = Field(default=None, max_length=64)
    tracking_no: str | None = Field(default=None, max_length=64)
    buyer_phone: str | None = Field(default=None, max_length=32)
    is_arrived: bool | None = None
    freight: Decimal | None = Field(default=None, ge=0)
    is_rejected_sign: bool | None = None
    return_person: str | None = Field(default=None, max_length=64)
    goods_status: str | None = Field(default=None, max_length=32)
    need_return_goods: bool | None = None
    items: list[ReturnItemRequest] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )


class ReturnConfirmRequest(BaseModel):
    location_id: int | None = Field(default=None, ge=1)
    comment: str | None = Field(default=None, max_length=255)


class ReturnCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class ReturnLogisticsRequest(BaseModel):
    logistics_company: str | None = Field(default=None, max_length=64)
    tracking_no: str | None = Field(default=None, max_length=64)


class AttachRegisterRequest(BaseModel):
    register_id: int


class RegisterCreateRequest(BaseModel):
    return_order_id: int | None = None
    exchange_order_id: int | None = None
    logistics_company: str | None = Field(default=None, max_length=64)
    tracking_no: str | None = Field(default=None, max_length=64)
    sender: str | None = Field(default=None, max_length=64)
    sender_phone: str | None = Field(default=None, max_length=32)
    product_info: str | None = Field(default=None, max_length=2000)
    image: str | None = Field(default=None, max_length=255)


class RegisterUpdateRequest(BaseModel):
    logistics_company: str | None = Field(default=None, max_length=64)
    tracking_no: str | None = Field(default=None, max_length=64)
    sender: str | None = Field(default=None, max_length=64)
    sender_phone: str | None = Field(default=None, max_length=32)
    product_info: str | None = Field(default=None, max_length=2000)
    image: str | None = Field(default=None, max_length=255)


class ExchangeItemRequest(BaseModel):
    product_id: int
    sku_id: int | None = None
    qty: Decimal | None = Field(default=None, gt=0)
    exchange_qty: Decimal = Field(..., gt=0)


class ExchangeCreateRequest(BaseModel):
    order_id: int
    reason: str | None = Field(default=None, max_length=128)
    is_cod: bool = False
    is_return: bool = False
    compensate_order_no: str | None = Field(default=None, max_length=64)
    compensate_amount: Decimal | None = Field(default=None, ge=0)
    compensate_direction: str | None = Field(default=None, max_length=16)
    register_id: int | None = None
    items: list[ExchangeItemRequest] = Field(..., min_length=1, max_length=500)


class ExchangeUpdateRequest(BaseModel):
    order_id: int | None = None
    reason: str | None = Field(default=None, max_length=128)
    is_cod: bool | None = None
    is_return: bool | None = None
    compensate_order_no: str | None = Field(default=None, max_length=64)
    compensate_amount: Decimal | None = Field(default=None, ge=0)
    compensate_direction: str | None = Field(default=None, max_length=16)
    register_id: int | None = None
    items: list[ExchangeItemRequest] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )


class ExchangeCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _items_payload(
    items: list[ReturnItemRequest] | list[ExchangeItemRequest] | None,
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


@router.get("/after-sales/returns/export")
def export_returns(
    order_id: int | None = None,
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return _excel_response(
        after_sale_service.export_returns(
            db,
            order_id=order_id,
            shop_id=shop_id,
            warehouse_id=warehouse_id,
            status=status,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        ),
        "after-sale-returns.xlsx",
    )


@router.get("/after-sales/returns", response_model=Result[dict])
def list_returns(
    order_id: int | None = None,
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        after_sale_service.list_returns(
            db,
            order_id=order_id,
            shop_id=shop_id,
            warehouse_id=warehouse_id,
            status=status,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/after-sales/returns", response_model=Result[dict])
def create_return(
    req: ReturnCreateRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _items_payload(req.items)
    record = after_sale_service.create_return(db, data=payload, staff=staff)
    return Result.ok(
        after_sale_service.return_dict(db, record),
        message="退货单已创建",
    )


@router.get("/after-sales/returns/{return_id}", response_model=Result[dict])
def get_return(
    return_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        after_sale_service.return_dict(
            db,
            after_sale_service.get_return_or_404(db, return_id),
        )
    )


@router.put("/after-sales/returns/{return_id}", response_model=Result[dict])
def update_return(
    return_id: int,
    req: ReturnUpdateRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    record = after_sale_service.update_return(
        db,
        return_id=return_id,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.return_dict(db, record),
        message="退货单已更新",
    )


@router.delete("/after-sales/returns/{return_id}", response_model=Result[dict])
def delete_return(
    return_id: int,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    after_sale_service.delete_return(
        db,
        return_id=return_id,
        staff=staff,
    )
    return Result.ok(message="退货单已删除")


@router.post(
    "/after-sales/returns/{return_id}/submit",
    response_model=Result[dict],
)
def submit_return(
    return_id: int,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    record = after_sale_service.submit_return(
        db,
        return_id=return_id,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.return_dict(db, record),
        message="退货单已提交",
    )


@router.post(
    "/after-sales/returns/{return_id}/confirm",
    response_model=Result[dict],
)
def confirm_return(
    return_id: int,
    req: ReturnConfirmRequest,
    staff: Staff = Depends(confirm_return),
    db: Session = Depends(get_db),
):
    record = after_sale_service.confirm_return(
        db,
        return_id=return_id,
        location_id=req.location_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        after_sale_service.return_dict(db, record),
        message="退货已确认并入库",
    )


@router.post(
    "/after-sales/returns/{return_id}/cancel",
    response_model=Result[dict],
)
def cancel_return(
    return_id: int,
    req: ReturnCancelRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    record = after_sale_service.cancel_return(
        db,
        return_id=return_id,
        reason=req.reason,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.return_dict(db, record),
        message="退货单已取消",
    )


@router.post(
    "/after-sales/returns/{return_id}/logistics",
    response_model=Result[dict],
)
def update_return_logistics(
    return_id: int,
    req: ReturnLogisticsRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    record = after_sale_service.update_return_logistics(
        db,
        return_id=return_id,
        logistics_company=req.logistics_company,
        tracking_no=req.tracking_no,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.return_dict(db, record),
        message="退货物流已更新",
    )


@router.post(
    "/after-sales/returns/{return_id}/register",
    response_model=Result[dict],
)
def attach_return_register(
    return_id: int,
    req: AttachRegisterRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    register = after_sale_service.attach_register(
        db,
        register_id=req.register_id,
        return_order_id=return_id,
        exchange_order_id=None,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.register_dict(db, register),
        message="退货登记已绑定",
    )


@router.delete(
    "/after-sales/returns/{return_id}/register",
    response_model=Result[dict],
)
def delete_return_register(
    return_id: int,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    after_sale_service.delete_bound_register(
        db,
        return_order_id=return_id,
        staff=staff,
    )
    return Result.ok(message="退货登记已删除")


@router.get("/after-sales/registers", response_model=Result[list[dict]])
def list_registers(
    bound: bool | None = None,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        after_sale_service.list_registers(db, bound=bound)
    )


@router.post("/after-sales/registers", response_model=Result[dict])
def create_register(
    req: RegisterCreateRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    register = after_sale_service.create_register(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        after_sale_service.register_dict(db, register),
        message="退货登记已创建",
    )


@router.put(
    "/after-sales/registers/{register_id}",
    response_model=Result[dict],
)
def update_register(
    register_id: int,
    req: RegisterUpdateRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    register = after_sale_service.update_register(
        db,
        register_id=register_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        after_sale_service.register_dict(db, register),
        message="退货登记已更新",
    )


@router.delete(
    "/after-sales/registers/{register_id}",
    response_model=Result[dict],
)
def delete_register(
    register_id: int,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    after_sale_service.delete_register(
        db,
        register_id=register_id,
        staff=staff,
    )
    return Result.ok(message="退货登记已删除")


@router.get("/after-sales/exchanges", response_model=Result[dict])
def list_exchanges(
    order_id: int | None = None,
    shop_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        after_sale_service.list_exchanges(
            db,
            order_id=order_id,
            shop_id=shop_id,
            status=status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/after-sales/exchanges", response_model=Result[dict])
def create_exchange(
    req: ExchangeCreateRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _items_payload(req.items)
    record = after_sale_service.create_exchange(
        db,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.exchange_dict(db, record),
        message="换货单已创建",
    )


@router.get(
    "/after-sales/exchanges/{exchange_id}",
    response_model=Result[dict],
)
def get_exchange(
    exchange_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        after_sale_service.exchange_dict(
            db,
            after_sale_service.get_exchange_or_404(db, exchange_id),
        )
    )


@router.put(
    "/after-sales/exchanges/{exchange_id}",
    response_model=Result[dict],
)
def update_exchange(
    exchange_id: int,
    req: ExchangeUpdateRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    record = after_sale_service.update_exchange(
        db,
        exchange_id=exchange_id,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.exchange_dict(db, record),
        message="换货单已更新",
    )


@router.delete(
    "/after-sales/exchanges/{exchange_id}",
    response_model=Result[dict],
)
def delete_exchange(
    exchange_id: int,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    after_sale_service.delete_exchange(
        db,
        exchange_id=exchange_id,
        staff=staff,
    )
    return Result.ok(message="换货单已删除")


@router.post(
    "/after-sales/exchanges/{exchange_id}/submit",
    response_model=Result[dict],
)
def submit_exchange(
    exchange_id: int,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    record = after_sale_service.submit_exchange(
        db,
        exchange_id=exchange_id,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.exchange_dict(db, record),
        message="换货单已提交",
    )


@router.post(
    "/after-sales/exchanges/{exchange_id}/cancel",
    response_model=Result[dict],
)
def cancel_exchange(
    exchange_id: int,
    req: ExchangeCancelRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    record = after_sale_service.cancel_exchange(
        db,
        exchange_id=exchange_id,
        reason=req.reason,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.exchange_dict(db, record),
        message="换货单已取消",
    )


@router.post(
    "/after-sales/exchanges/{exchange_id}/register",
    response_model=Result[dict],
)
def attach_exchange_register(
    exchange_id: int,
    req: AttachRegisterRequest,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    register = after_sale_service.attach_register(
        db,
        register_id=req.register_id,
        return_order_id=None,
        exchange_order_id=exchange_id,
        staff=staff,
    )
    return Result.ok(
        after_sale_service.register_dict(db, register),
        message="退货登记已绑定",
    )


@router.delete(
    "/after-sales/exchanges/{exchange_id}/register",
    response_model=Result[dict],
)
def delete_exchange_register(
    exchange_id: int,
    staff: Staff = Depends(manage_after_sale),
    db: Session = Depends(get_db),
):
    after_sale_service.delete_bound_register(
        db,
        exchange_order_id=exchange_id,
        staff=staff,
    )
    return Result.ok(message="退货登记已删除")
