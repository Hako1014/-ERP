from decimal import Decimal
from io import BytesIO

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.upload import validate_and_save
from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import purchase_order_service
from app.services import shared_queries
from app.services.storage import get_storage

router = APIRouter(tags=["采购订单"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

read_purchase = require_permission("module:purchase")
create_purchase = require_permission("purchase:create")
approve_purchase = require_permission("purchase:approve")
unaudit_purchase = require_permission("purchase:create", "purchase:approve")


class OrderItemRequest(BaseModel):
    product_id: int
    sku_id: int | None = None
    qty: Decimal
    purchase_price: Decimal | None = None


class OrderCreateRequest(BaseModel):
    supplier_id: int
    warehouse_id: int | None = None
    purchaser_id: int | None = None
    description: str | None = Field(default=None, max_length=255)
    attachments: list[str] = Field(default_factory=list, max_length=50)
    items: list[OrderItemRequest] = Field(..., min_length=1)


class OrderUpdateRequest(BaseModel):
    supplier_id: int | None = None
    warehouse_id: int | None = None
    purchaser_id: int | None = None
    description: str | None = Field(default=None, max_length=255)
    attachments: list[str] | None = Field(default=None, max_length=50)
    items: list[OrderItemRequest] | None = Field(default=None, min_length=1)


class FromPlanItemRequest(BaseModel):
    plan_item_id: int
    qty: Decimal | None = None


class FromPlanRequest(BaseModel):
    plan_id: int
    warehouse_id: int | None = None
    purchaser_id: int | None = None
    description: str | None = Field(default=None, max_length=255)
    attachments: list[str] = Field(default_factory=list, max_length=50)
    items: list[FromPlanItemRequest] | None = Field(default=None, min_length=1)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class UnauditRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


class TerminateRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _items_payload(items: list[OrderItemRequest]) -> list[dict]:
    return [item.model_dump(exclude_unset=True) for item in items]


def _excel_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/purchase-orders/template")
def download_purchase_order_template(
    staff: Staff = Depends(read_purchase),
):
    from app.services.excel import build_template

    data = build_template(purchase_order_service.PURCHASE_ORDER_TEMPLATE)
    return _excel_response(data, "purchase_order.xlsx")


@router.get("/purchase-orders/export")
def export_purchase_orders(
    plan_id: int | None = None,
    supplier_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    data = purchase_order_service.export_orders(
        db,
        plan_id=plan_id,
        supplier_id=supplier_id,
        warehouse_id=warehouse_id,
        status=status,
        audit_status=audit_status,
        keyword=keyword,
    )
    return _excel_response(data, "purchase_orders.xlsx")


@router.post("/purchase-orders/import", response_model=Result[dict])
async def import_purchase_orders(
    file: UploadFile = File(...),
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    data = await file.read()
    limit = get_settings().max_upload_size_mb * 1024 * 1024
    if len(data) > limit:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"文件大小不能超过 {limit // (1024 * 1024)}MB"
        )
    return Result.ok(
        purchase_order_service.import_orders(
            db, file_bytes=data, staff=staff
        )
    )


@router.post("/purchase-orders/from-plan", response_model=Result[dict])
def create_purchase_order_from_plan(
    req: FromPlanRequest,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = [
            item.model_dump(exclude_unset=True) for item in req.items
        ]
    order = purchase_order_service.create_order_from_plan(
        db, data=payload, staff=staff
    )
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购计划已转采购订单",
    )


@router.get("/purchase-orders", response_model=Result[dict])
def list_purchase_orders(
    plan_id: int | None = None,
    supplier_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    return Result.ok(
        shared_queries.purchase_orders(
            db,
            plan_id=plan_id,
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/purchase-orders", response_model=Result[dict])
def create_purchase_order(
    req: OrderCreateRequest,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _items_payload(req.items)
    order = purchase_order_service.create_order(db, data=payload, staff=staff)
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购订单已创建",
    )


@router.get("/purchase-orders/{order_id}", response_model=Result[dict])
def get_purchase_order(
    order_id: int,
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    return Result.ok(shared_queries.purchase_order(db, order_id))


@router.put("/purchase-orders/{order_id}", response_model=Result[dict])
def update_purchase_order(
    order_id: int,
    req: OrderUpdateRequest,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    order = purchase_order_service.update_order(
        db, order_id=order_id, data=payload, staff=staff
    )
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购订单已更新",
    )


@router.delete("/purchase-orders/{order_id}", response_model=Result[dict])
def delete_purchase_order(
    order_id: int,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    purchase_order_service.delete_order(db, order_id=order_id, staff=staff)
    return Result.ok(message="采购订单已删除")


@router.post("/purchase-orders/{order_id}/submit", response_model=Result[dict])
def submit_purchase_order(
    order_id: int,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    order = purchase_order_service.submit_order(db, order_id=order_id, staff=staff)
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购订单已提交审批",
    )


@router.post("/purchase-orders/{order_id}/approve", response_model=Result[dict])
def approve_purchase_order(
    order_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_purchase),
    db: Session = Depends(get_db),
):
    order = purchase_order_service.approve_order(
        db, order_id=order_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购订单审核通过",
    )


@router.post("/purchase-orders/{order_id}/reject", response_model=Result[dict])
def reject_purchase_order(
    order_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_purchase),
    db: Session = Depends(get_db),
):
    order = purchase_order_service.reject_order(
        db, order_id=order_id, staff=staff, comment=req.comment
    )
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购订单审核不通过",
    )


@router.post("/purchase-orders/{order_id}/unaudit", response_model=Result[dict])
def unaudit_purchase_order(
    order_id: int,
    req: UnauditRequest,
    staff: Staff = Depends(unaudit_purchase),
    db: Session = Depends(get_db),
):
    order = purchase_order_service.unaudit_order(
        db, order_id=order_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购订单已弃审并退回待审核",
    )


@router.post("/purchase-orders/{order_id}/terminate", response_model=Result[dict])
def terminate_purchase_order(
    order_id: int,
    req: TerminateRequest,
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    order = purchase_order_service.terminate_order(
        db, order_id=order_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="采购订单已终止",
    )


@router.post(
    "/purchase-orders/{order_id}/attachments", response_model=Result[dict]
)
async def upload_purchase_order_attachments(
    order_id: int,
    files: list[UploadFile] = File(...),
    staff: Staff = Depends(create_purchase),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    urls: list[str] = []
    for file in files:
        data = await file.read()
        urls.append(
            validate_and_save(
                data,
                file.filename,
                get_storage(),
                settings.max_upload_size_mb * 1024 * 1024,
            )
        )
    order = purchase_order_service.add_attachment_urls(
        db, order_id=order_id, urls=urls, staff=staff
    )
    return Result.ok(
        purchase_order_service.order_dict(
            db, order, with_items=True, with_approval=True
        ),
        message="附件已上传",
    )


@router.get("/purchase-orders/{order_id}/pdf")
def download_purchase_order_pdf(
    order_id: int,
    staff: Staff = Depends(read_purchase),
    db: Session = Depends(get_db),
):
    data = purchase_order_service.build_order_pdf(db, order_id)
    return StreamingResponse(
        BytesIO(data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="purchase_order_{order_id}.pdf"'
        },
    )
