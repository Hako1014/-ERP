from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import transfer_service

router = APIRouter(tags=["库存管理"])

read_stock = require_permission("module:stock")
manage_stock = require_permission("stock:manage")


class TransferItemRequest(BaseModel):
    product_id: int
    sku_id: int
    qty: Decimal
    barcode: str | None = Field(default=None, max_length=64)


class TransferCreateRequest(BaseModel):
    from_warehouse_id: int
    to_warehouse_id: int
    sync_stock: bool = False
    remark: str | None = Field(default=None, max_length=255)
    items: list[TransferItemRequest] = Field(..., min_length=1, max_length=500)


class TransferUpdateRequest(BaseModel):
    from_warehouse_id: int | None = None
    to_warehouse_id: int | None = None
    sync_stock: bool | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[TransferItemRequest] | None = Field(
        default=None, min_length=1, max_length=500
    )


class AuditRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=255)


class OffShelfItemRequest(BaseModel):
    transfer_out_item_id: int
    location_id: int
    qty: Decimal


class OffShelfRequest(BaseModel):
    items: list[OffShelfItemRequest] = Field(..., min_length=1)


class ReceiveItemRequest(BaseModel):
    transfer_in_item_id: int
    location_id: int
    qty: Decimal


class ReceiveRequest(BaseModel):
    items: list[ReceiveItemRequest] = Field(..., min_length=1)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _items_payload(items: list[TransferItemRequest]) -> list[dict]:
    return [item.model_dump(exclude_unset=True) for item in items]


@router.get("/transfers", response_model=Result[dict])
def list_transfers(
    from_warehouse_id: int | None = None,
    to_warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        transfer_service.list_transfers(
            db,
            from_warehouse_id=from_warehouse_id,
            to_warehouse_id=to_warehouse_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/transfers", response_model=Result[dict])
def create_transfer(
    req: TransferCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    payload["items"] = _items_payload(req.items)
    row = transfer_service.create_transfer(db, data=payload, staff=staff)
    return Result.ok(
        transfer_service.transfer_dict(
            db,
            row,
            with_items=True,
            with_docs=True,
            with_approval=True,
        ),
        message="调拨单已创建",
    )


@router.get("/transfers/{transfer_id}", response_model=Result[dict])
def get_transfer(
    transfer_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.get_transfer_or_404(db, transfer_id)
    return Result.ok(
        transfer_service.transfer_dict(
            db,
            row,
            with_items=True,
            with_docs=True,
            with_approval=True,
        )
    )


@router.put("/transfers/{transfer_id}", response_model=Result[dict])
def update_transfer(
    transfer_id: int,
    req: TransferUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    if req.items is not None:
        payload["items"] = _items_payload(req.items)
    row = transfer_service.update_transfer(
        db,
        transfer_id=transfer_id,
        data=payload,
        staff=staff,
    )
    return Result.ok(
        transfer_service.transfer_dict(
            db,
            row,
            with_items=True,
            with_docs=True,
            with_approval=True,
        ),
        message="调拨单已更新",
    )


@router.delete("/transfers/{transfer_id}", response_model=Result[dict])
def delete_transfer(
    transfer_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    transfer_service.delete_transfer(
        db,
        transfer_id=transfer_id,
        staff=staff,
    )
    return Result.ok(message="调拨单已删除")


@router.post("/transfers/{transfer_id}/submit", response_model=Result[dict])
def submit_transfer(
    transfer_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.submit_transfer(
        db,
        transfer_id=transfer_id,
        staff=staff,
    )
    return Result.ok(
        transfer_service.transfer_dict(
            db,
            row,
            with_items=True,
            with_docs=True,
            with_approval=True,
        ),
        message="调拨单已提交审批",
    )


@router.post("/transfers/{transfer_id}/approve", response_model=Result[dict])
def approve_transfer(
    transfer_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.approve_transfer(
        db,
        transfer_id=transfer_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        transfer_service.transfer_dict(
            db,
            row,
            with_items=True,
            with_docs=True,
            with_approval=True,
        ),
        message="调拨单审核通过",
    )


@router.post("/transfers/{transfer_id}/reject", response_model=Result[dict])
def reject_transfer(
    transfer_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.reject_transfer(
        db,
        transfer_id=transfer_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        transfer_service.transfer_dict(
            db,
            row,
            with_items=True,
            with_docs=True,
            with_approval=True,
        ),
        message="调拨单审核不通过",
    )


@router.get("/transfer-outs", response_model=Result[dict])
def list_transfer_outs(
    transfer_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        transfer_service.list_transfer_outs(
            db,
            transfer_id=transfer_id,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/transfer-outs/{transfer_out_id}", response_model=Result[dict])
def get_transfer_out(
    transfer_out_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.get_transfer_out_or_404(db, transfer_out_id)
    return Result.ok(
        transfer_service.transfer_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        )
    )


@router.post(
    "/transfer-outs/{transfer_out_id}/submit",
    response_model=Result[dict],
)
def submit_transfer_out(
    transfer_out_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.submit_transfer_out(
        db,
        transfer_out_id=transfer_out_id,
        staff=staff,
    )
    return Result.ok(
        transfer_service.transfer_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨出库单已提交审批",
    )


@router.post(
    "/transfer-outs/{transfer_out_id}/approve",
    response_model=Result[dict],
)
def approve_transfer_out(
    transfer_out_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.approve_transfer_out(
        db,
        transfer_out_id=transfer_out_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        transfer_service.transfer_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨出库单审核通过",
    )


@router.post(
    "/transfer-outs/{transfer_out_id}/reject",
    response_model=Result[dict],
)
def reject_transfer_out(
    transfer_out_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.reject_transfer_out(
        db,
        transfer_out_id=transfer_out_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        transfer_service.transfer_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨出库单审核不通过",
    )


@router.post(
    "/transfer-outs/{transfer_out_id}/off-shelf",
    response_model=Result[dict],
)
def off_shelf_transfer_out(
    transfer_out_id: int,
    req: OffShelfRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.off_shelf_transfer_out(
        db,
        transfer_out_id=transfer_out_id,
        data={"items": [item.model_dump() for item in req.items]},
        staff=staff,
    )
    return Result.ok(
        transfer_service.transfer_out_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨出库下架完成",
    )


@router.get("/transfer-ins", response_model=Result[dict])
def list_transfer_ins(
    transfer_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        transfer_service.list_transfer_ins(
            db,
            transfer_id=transfer_id,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/transfer-ins/{transfer_in_id}", response_model=Result[dict])
def get_transfer_in(
    transfer_in_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.get_transfer_in_or_404(db, transfer_in_id)
    return Result.ok(
        transfer_service.transfer_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        )
    )


@router.post(
    "/transfer-ins/{transfer_in_id}/submit",
    response_model=Result[dict],
)
def submit_transfer_in(
    transfer_in_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.submit_transfer_in(
        db,
        transfer_in_id=transfer_in_id,
        staff=staff,
    )
    return Result.ok(
        transfer_service.transfer_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨入库单已提交审批",
    )


@router.post(
    "/transfer-ins/{transfer_in_id}/approve",
    response_model=Result[dict],
)
def approve_transfer_in(
    transfer_in_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.approve_transfer_in(
        db,
        transfer_in_id=transfer_in_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        transfer_service.transfer_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨入库单审核通过",
    )


@router.post(
    "/transfer-ins/{transfer_in_id}/reject",
    response_model=Result[dict],
)
def reject_transfer_in(
    transfer_in_id: int,
    req: AuditRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.reject_transfer_in(
        db,
        transfer_in_id=transfer_in_id,
        staff=staff,
        comment=req.comment,
    )
    return Result.ok(
        transfer_service.transfer_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨入库单审核不通过",
    )


@router.post(
    "/transfer-ins/{transfer_in_id}/receive",
    response_model=Result[dict],
)
def receive_transfer_in(
    transfer_in_id: int,
    req: ReceiveRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = transfer_service.receive_transfer_in(
        db,
        transfer_in_id=transfer_in_id,
        data={"items": [item.model_dump() for item in req.items]},
        staff=staff,
    )
    return Result.ok(
        transfer_service.transfer_in_dict(
            db,
            row,
            with_items=True,
            with_approval=True,
        ),
        message="调拨入库签收完成",
    )
