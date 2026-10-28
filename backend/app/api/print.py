from io import BytesIO

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import print_service

router = APIRouter(tags=["订单管理"])

read_order = require_permission("module:order")
manage_print = require_permission("order:ship")


class AcquireWaybillRequest(BaseModel):
    order_ids: list[int] = Field(..., min_length=1, max_length=500)
    print_type: str = Field(default="THERMAL", max_length=16)
    waybill_nos: dict[str, str] | None = None
    provider: bool = False


class RecycleWaybillRequest(BaseModel):
    order_ids: list[int] = Field(..., min_length=1, max_length=500)


class PrintBatchRequest(BaseModel):
    order_ids: list[int] = Field(..., min_length=1, max_length=500)
    print_type: str = Field(default="THERMAL", max_length=16)
    printer_name: str | None = Field(default=None, max_length=128)
    template_id: int | None = None
    include_shipping_slip: bool = False
    sort_type: str = Field(default="LOCATION", max_length=16)
    waybill_nos: dict[str, str] | None = None


@router.post("/print/waybills/acquire", response_model=Result[dict])
def acquire_waybills(
    req: AcquireWaybillRequest,
    staff: Staff = Depends(manage_print),
    db: Session = Depends(get_db),
):
    return Result.ok(
        print_service.acquire_waybills(
            db,
            order_ids=req.order_ids,
            print_type=req.print_type,
            waybill_nos=req.waybill_nos,
            provider=req.provider,
            staff=staff,
        ),
        message="运单号已获取",
    )


@router.post("/print/waybills/recycle", response_model=Result[dict])
def recycle_waybills(
    req: RecycleWaybillRequest,
    staff: Staff = Depends(manage_print),
    db: Session = Depends(get_db),
):
    return Result.ok(
        print_service.recycle_waybills(
            db,
            order_ids=req.order_ids,
            staff=staff,
        ),
        message="运单号已回收",
    )


@router.post("/print/batches", response_model=Result[dict])
def create_print_batch(
    req: PrintBatchRequest,
    staff: Staff = Depends(manage_print),
    db: Session = Depends(get_db),
):
    return Result.ok(
        print_service.create_print_batch(
            db,
            order_ids=req.order_ids,
            print_type=req.print_type,
            printer_name=req.printer_name,
            template_id=req.template_id,
            include_shipping_slip=req.include_shipping_slip,
            sort_type=req.sort_type,
            waybill_nos=req.waybill_nos,
            staff=staff,
        ),
        message="打印批次已生成",
    )


@router.get("/print/batches/{batch_no}", response_model=Result[dict])
def get_print_batch(
    batch_no: str,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    from app.services.shared_queries import print_batch

    return Result.ok(print_batch(db, batch_no=batch_no))


@router.get("/print/batches/{batch_no}/pdf")
def download_print_batch_pdf(
    batch_no: str,
    document_type: str = "ALL",
    staff: Staff = Depends(manage_print),
    db: Session = Depends(get_db),
):
    data = print_service.build_print_batch_pdf(
        db,
        batch_no=batch_no,
        document_type=document_type,
    )
    return StreamingResponse(
        BytesIO(data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="print_batch_{batch_no}.pdf"'
            )
        },
    )
