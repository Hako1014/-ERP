from datetime import date, datetime
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
from app.services import stock_settlement_service
from app.services.excel import build_template

router = APIRouter(tags=["库存管理"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

read_stock = require_permission("module:stock")
manage_stock = require_permission("stock:manage")


class StockSettingUpdateRequest(BaseModel):
    sync_stock_default: bool | None = None
    shop_stock_enabled: bool | None = None
    floating_cost_enabled: bool | None = None
    off_shelf_below_warning: bool | None = None


class ShopStockCreateRequest(BaseModel):
    shop_id: int
    warehouse_id: int
    product_id: int
    sku_id: int
    exclusive_qty: Decimal = Field(default=Decimal("0"), ge=0)
    is_sync: bool = False
    is_enabled: bool = True
    effective_start: datetime | None = None
    effective_end: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)


class ShopStockUpdateRequest(BaseModel):
    shop_id: int | None = None
    warehouse_id: int | None = None
    product_id: int | None = None
    sku_id: int | None = None
    exclusive_qty: Decimal | None = Field(default=None, ge=0)
    is_sync: bool | None = None
    is_enabled: bool | None = None
    effective_start: datetime | None = None
    effective_end: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)


class ShopStockSyncRequest(BaseModel):
    shop_id: int | None = None
    ids: list[int] | None = Field(default=None, max_length=1000)


class SettlementCreateRequest(BaseModel):
    settlement_date: date | None = None
    month: str | None = Field(default=None, max_length=7)


class RewriteCostRequest(BaseModel):
    settlement_date: date | None = None


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


def _excel_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# 库存设置
# --------------------------------------------------------------------------- #


@router.get("/stock-settings", response_model=Result[dict])
def get_stock_setting(
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    from app.services.shared_queries import stock_settings

    return Result.ok(stock_settings(db))


@router.put("/stock-settings", response_model=Result[dict])
def update_stock_setting(
    req: StockSettingUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_settlement_service.update_stock_setting(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_settlement_service.stock_setting_dict(row),
        message="库存设置已更新",
    )


# --------------------------------------------------------------------------- #
# 店铺独享库存
# --------------------------------------------------------------------------- #


@router.get("/shop-stocks", response_model=Result[dict])
def list_shop_stocks(
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    is_enabled: bool | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_settlement_service.list_shop_stocks(
            db,
            shop_id=shop_id,
            warehouse_id=warehouse_id,
            product_id=product_id,
            sku_id=sku_id,
            is_enabled=is_enabled,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/shop-stocks", response_model=Result[dict])
def create_shop_stock(
    req: ShopStockCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_settlement_service.create_shop_stock(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_settlement_service.shop_stock_dict(db, row),
        message="店铺库存设置已创建",
    )


@router.post("/shop-stocks/sync", response_model=Result[dict])
def sync_shop_stock(
    req: ShopStockSyncRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    result = stock_settlement_service.sync_shop_stock(
        db,
        staff=staff,
        shop_id=req.shop_id,
        shop_stock_ids=req.ids,
    )
    return Result.ok(result, message="店铺库存同步完成")


@router.get("/shop-stocks/{shop_stock_id}", response_model=Result[dict])
def get_shop_stock(
    shop_stock_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = stock_settlement_service.get_shop_stock_or_404(db, shop_stock_id)
    return Result.ok(stock_settlement_service.shop_stock_dict(db, row))


@router.put("/shop-stocks/{shop_stock_id}", response_model=Result[dict])
def update_shop_stock(
    shop_stock_id: int,
    req: ShopStockUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = stock_settlement_service.update_shop_stock(
        db,
        shop_stock_id=shop_stock_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        stock_settlement_service.shop_stock_dict(db, row),
        message="店铺库存设置已更新",
    )


@router.delete("/shop-stocks/{shop_stock_id}", response_model=Result[dict])
def delete_shop_stock(
    shop_stock_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    stock_settlement_service.delete_shop_stock(
        db,
        shop_stock_id=shop_stock_id,
        staff=staff,
    )
    return Result.ok(message="店铺库存设置已删除")


# --------------------------------------------------------------------------- #
# 库存结存
# --------------------------------------------------------------------------- #


@router.get("/stock-settlements/template")
def download_settlement_template(
    staff: Staff = Depends(read_stock),
):
    return _excel_response(
        build_template(stock_settlement_service.SETTLEMENT_TEMPLATE),
        "stock_settlement.xlsx",
    )


@router.get("/stock-settlements/periods", response_model=Result[list[dict]])
def list_settlement_periods(
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(stock_settlement_service.list_settlement_periods(db))


@router.get("/stock-settlements", response_model=Result[dict])
def list_settlements(
    settlement_date: date | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_settlement_service.list_settlements(
            db,
            settlement_date=settlement_date,
            product_id=product_id,
            sku_id=sku_id,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/stock-settlements/export")
def export_settlements(
    settlement_date: date | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    keyword: str | None = None,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return _excel_response(
        stock_settlement_service.export_settlements(
            db,
            settlement_date=settlement_date,
            product_id=product_id,
            sku_id=sku_id,
            keyword=keyword,
        ),
        "stock_settlements.xlsx",
    )


@router.post("/stock-settlements/import", response_model=Result[dict])
async def import_settlements(
    file: UploadFile = File(...),
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    data = await file.read()
    limit = get_settings().max_upload_size_mb * 1024 * 1024
    if len(data) > limit:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"文件大小不能超过 {limit // (1024 * 1024)}MB",
        )
    return Result.ok(
        stock_settlement_service.import_settlements(
            db,
            file_bytes=data,
            staff=staff,
        )
    )


@router.post("/stock-settlements", response_model=Result[dict])
def create_settlement(
    req: SettlementCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    result = stock_settlement_service.create_settlement(
        db,
        staff=staff,
        settlement_date=req.settlement_date,
        month=req.month,
    )
    return Result.ok(result, message="库存结存完成")


@router.post("/stock-settlements/rewrite-outbound-cost", response_model=Result[dict])
def rewrite_outbound_cost(
    req: RewriteCostRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    result = stock_settlement_service.rewrite_outbound_cost(
        db,
        staff=staff,
        settlement_date=req.settlement_date,
    )
    return Result.ok(result, message="出库成本已回写")


@router.get("/stock-settlements/cost-rewrite-logs", response_model=Result[dict])
def list_cost_rewrite_logs(
    batch_no: str | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    target_type: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        stock_settlement_service.list_cost_rewrite_logs(
            db,
            batch_no=batch_no,
            product_id=product_id,
            sku_id=sku_id,
            target_type=target_type,
            start_time=start_time,
            end_time=end_time,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )
