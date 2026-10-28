from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import purchase_return_service

router = APIRouter(tags=["采购退货"])

read_return = require_permission(
    "module:purchase", "module:stock", "module:finance"
)
create_return = require_permission("purchase:create")
approve_return = require_permission("purchase:approve", "stock:manage")
unaudit_return = require_permission("purchase:create")


class ReturnItemRequest(BaseModel):
    product_id: int
    sku_id: int
    unit_id: int | None = None
    unit_price: Decimal | None = None
    return_qty: Decimal
    amount: Decimal | None = None


class ReturnCreateRequest(BaseModel):
    supplier_id: int | None = None
    order_id: int | None = None
    warehouse_id: int
    auto_out: bool = False
    return_reason: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    items: list[ReturnItemRequest] = Field(..., min_length=1)


class ReturnUpdateRequest(BaseModel):
    supplier_id: int | None = None
    order_id: int | None = None
    warehouse_id: int | None = None
    auto_out: bool | None = None
    return_reason: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    items: list[ReturnItemRequest] | None = Field(default=None, min_length=1)


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _items_payload(items: list[ReturnItemRequest]) -> list[dict]:
    return [item.model_dump(exclude_unset=True) for item in items]


@router.get("/purchase-returns", response_model=Result[dict])
def list_purchase_returns(
    supplier_id: int | None = None,
    order_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_return),
    db: Session = Depends(get_db),
):
    return Result.ok(
        purchase_return_service.list_returns(
            db,
            supplier_id=supplier_id,
            order_id=order_id,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/purchase-returns", response_model=Result[dict])
def create_purchase_return(
    req: ReturnCreateRequest,
    staff: Staff = Depends(create_return),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _items_payload(req.items)
    record = purchase_return_service.create_return(
        db, data=payload, staff=staff
    )
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        ),
        message="采购退货单已创建",
    )


@router.get("/purchase-returns/{return_id}", response_model=Result[dict])
def get_purchase_return(
    return_id: int,
    staff: Staff = Depends(read_return),
    db: Session = Depends(get_db),
):
    record = purchase_return_service.get_return_or_404(db, return_id)
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        )
    )


@router.put("/purchase-returns/{return_id}", response_model=Result[dict])
def update_purchase_return(
    return_id: int,
    req: ReturnUpdateRequest,
    staff: Staff = Depends(create_return),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    record = purchase_return_service.update_return(
        db, return_id=return_id, data=payload, staff=staff
    )
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        ),
        message="采购退货单已更新",
    )


@router.delete("/purchase-returns/{return_id}", response_model=Result[dict])
def delete_purchase_return(
    return_id: int,
    staff: Staff = Depends(create_return),
    db: Session = Depends(get_db),
):
    purchase_return_service.delete_return(
        db, return_id=return_id, staff=staff
    )
    return Result.ok(message="采购退货单已删除")


@router.post(
    "/purchase-returns/{return_id}/submit", response_model=Result[dict]
)
def submit_purchase_return(
    return_id: int,
    req: AuditRequest | None = None,
    staff: Staff = Depends(create_return),
    db: Session = Depends(get_db),
):
    record = purchase_return_service.submit_return(
        db,
        return_id=return_id,
        staff=staff,
        comment=req.comment if req else None,
    )
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        ),
        message="采购退货单已提交审批",
    )


@router.post(
    "/purchase-returns/{return_id}/approve", response_model=Result[dict]
)
def approve_purchase_return(
    return_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_return),
    db: Session = Depends(get_db),
):
    record = purchase_return_service.approve_return(
        db,
        return_id=return_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        ),
        message="采购退货单审核通过",
    )


@router.post(
    "/purchase-returns/{return_id}/reject", response_model=Result[dict]
)
def reject_purchase_return(
    return_id: int,
    req: AuditRequest,
    staff: Staff = Depends(approve_return),
    db: Session = Depends(get_db),
):
    record = purchase_return_service.reject_return(
        db,
        return_id=return_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        ),
        message="采购退货单审核不通过",
    )


@router.post(
    "/purchase-returns/{return_id}/unaudit", response_model=Result[dict]
)
def unaudit_purchase_return(
    return_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(unaudit_return),
    db: Session = Depends(get_db),
):
    record = purchase_return_service.unaudit_return(
        db, return_id=return_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        ),
        message="采购退货单已弃审并退回待审核",
    )


@router.post(
    "/purchase-returns/{return_id}/terminate", response_model=Result[dict]
)
def terminate_purchase_return(
    return_id: int,
    req: ReasonRequest,
    staff: Staff = Depends(create_return),
    db: Session = Depends(get_db),
):
    record = purchase_return_service.terminate_return(
        db, return_id=return_id, staff=staff, reason=req.reason
    )
    return Result.ok(
        purchase_return_service.return_dict(
            db, record, with_items=True, with_approval=True
        ),
        message="采购退货单已终止",
    )


@router.get(
    "/purchase-returns/{return_id}/stock-out", response_model=Result[dict]
)
def get_purchase_return_stock_out(
    return_id: int,
    staff: Staff = Depends(read_return),
    db: Session = Depends(get_db),
):
    return Result.ok(
        purchase_return_service.get_return_stock_out(db, return_id)
    )


@router.get(
    "/purchase-returns/{return_id}/refund", response_model=Result[dict]
)
def get_purchase_return_refund(
    return_id: int,
    staff: Staff = Depends(read_return),
    db: Session = Depends(get_db),
):
    return Result.ok(purchase_return_service.get_return_refund(db, return_id))
