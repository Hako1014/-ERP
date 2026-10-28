from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import warehouse_service, third_warehouse_service

router = APIRouter(tags=["库存管理"])

read_stock = require_permission("module:stock")
manage_stock = require_permission("stock:manage")


class WarehouseCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    warehouse_type: Literal["FINISHED", "DEFECTIVE"] = "FINISHED"
    is_default: bool = False
    sync_stock: bool = False
    enable_location: bool = False
    zero_stock_out: bool = False
    is_virtual: bool = False
    ship_regions: str | None = Field(default=None, max_length=128)
    is_enabled: bool = True
    lock_type: Literal["NONE", "MANUAL", "CHECK"] = "NONE"
    remark: str | None = Field(default=None, max_length=255)


class WarehouseUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    warehouse_type: Literal["FINISHED", "DEFECTIVE"] | None = None
    is_default: bool | None = None
    sync_stock: bool | None = None
    enable_location: bool | None = None
    zero_stock_out: bool | None = None
    is_virtual: bool | None = None
    ship_regions: str | None = Field(default=None, max_length=128)
    is_enabled: bool | None = None
    lock_type: Literal["NONE", "MANUAL", "CHECK"] | None = None
    remark: str | None = Field(default=None, max_length=255)


class ZoneCreateRequest(BaseModel):
    warehouse_id: int
    code: str = Field(..., min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=64)
    sort_order: int = 0


class ZoneUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None


class LocationCreateRequest(BaseModel):
    warehouse_id: int
    zone_id: int | None = None
    code: str | None = Field(default=None, max_length=64)
    row_no: int = Field(default=1, ge=1)
    col_no: int = Field(default=1, ge=1)
    layer_no: int = Field(default=1, ge=1)
    capacity: int = Field(default=0, ge=0)
    priority: int = 0
    sort_order: int = 0
    is_virtual: bool = False
    is_enabled: bool = True


class LocationUpdateRequest(BaseModel):
    zone_id: int | None = None
    code: str | None = Field(default=None, max_length=64)
    row_no: int | None = Field(default=None, ge=1)
    col_no: int | None = Field(default=None, ge=1)
    layer_no: int | None = Field(default=None, ge=1)
    capacity: int | None = Field(default=None, ge=0)
    priority: int | None = None
    sort_order: int | None = None
    is_virtual: bool | None = None
    is_enabled: bool | None = None


class LocationGenerateRequest(BaseModel):
    warehouse_id: int
    zone_id: int
    row_count: int = Field(..., ge=1)
    col_count: int = Field(..., ge=1)
    layer_count: int = Field(..., ge=1)
    capacity: int = Field(default=0, ge=0)
    priority: int = 0
    sort_order: int = 0
    is_virtual: bool = False
    is_enabled: bool = True


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- #
# 仓库
# --------------------------------------------------------------------------- #


@router.get("/warehouses", response_model=Result[list[dict]])
def list_warehouses(
    keyword: str | None = None,
    warehouse_type: Literal["FINISHED", "DEFECTIVE"] | None = None,
    is_enabled: bool | None = None,
    selectable_only: bool = False,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    rows = warehouse_service.list_warehouses(
        db,
        keyword=keyword,
        warehouse_type=warehouse_type,
        is_enabled=is_enabled,
        selectable_only=selectable_only,
    )
    return Result.ok([warehouse_service.warehouse_dict(item) for item in rows])


@router.post("/warehouses", response_model=Result[dict])
def create_warehouse(
    req: WarehouseCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.create_warehouse(db, data=_payload(req), staff=staff)
    return Result.ok(
        warehouse_service.warehouse_dict(row),
        message="仓库已创建",
    )


@router.get("/warehouses/{warehouse_id}", response_model=Result[dict])
def get_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.get_warehouse_or_404(db, warehouse_id)
    return Result.ok(warehouse_service.warehouse_dict(row))


@router.post("/warehouses/{warehouse_id}/third-sync", response_model=Result[dict])
def sync_third_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(third_warehouse_service.sync_warehouse(db, warehouse_id=warehouse_id, staff=staff))


@router.put("/warehouses/{warehouse_id}", response_model=Result[dict])
def update_warehouse(
    warehouse_id: int,
    req: WarehouseUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.update_warehouse(
        db,
        warehouse_id=warehouse_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        warehouse_service.warehouse_dict(row),
        message="仓库已更新",
    )


@router.delete("/warehouses/{warehouse_id}", response_model=Result[dict])
def delete_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    warehouse_service.delete_warehouse(
        db,
        warehouse_id=warehouse_id,
        staff=staff,
    )
    return Result.ok(message="仓库已删除")


@router.post("/warehouses/{warehouse_id}/enable", response_model=Result[dict])
def enable_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.set_warehouse_enabled(
        db,
        warehouse_id=warehouse_id,
        enabled=True,
        staff=staff,
    )
    return Result.ok(
        warehouse_service.warehouse_dict(row),
        message="仓库已启用",
    )


@router.post("/warehouses/{warehouse_id}/disable", response_model=Result[dict])
def disable_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.set_warehouse_enabled(
        db,
        warehouse_id=warehouse_id,
        enabled=False,
        staff=staff,
    )
    return Result.ok(
        warehouse_service.warehouse_dict(row),
        message="仓库已停用",
    )


@router.post("/warehouses/{warehouse_id}/default", response_model=Result[dict])
def set_default_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.set_default_warehouse(
        db,
        warehouse_id=warehouse_id,
        staff=staff,
    )
    return Result.ok(
        warehouse_service.warehouse_dict(row),
        message="已设为默认仓库",
    )


@router.post("/warehouses/{warehouse_id}/lock", response_model=Result[dict])
def lock_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.lock_warehouse(
        db,
        warehouse_id=warehouse_id,
        staff=staff,
    )
    return Result.ok(
        warehouse_service.warehouse_dict(row),
        message="仓库已锁定",
    )


@router.post("/warehouses/{warehouse_id}/unlock", response_model=Result[dict])
def unlock_warehouse(
    warehouse_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.unlock_warehouse(
        db,
        warehouse_id=warehouse_id,
        staff=staff,
    )
    return Result.ok(
        warehouse_service.warehouse_dict(row),
        message="仓库已解锁",
    )


@router.get("/warehouses/{warehouse_id}/zones", response_model=Result[list[dict]])
def list_warehouse_zones(
    warehouse_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    rows = warehouse_service.list_zones(db, warehouse_id=warehouse_id)
    return Result.ok([warehouse_service.zone_dict(db, item) for item in rows])


# --------------------------------------------------------------------------- #
# 库区
# --------------------------------------------------------------------------- #


@router.post("/zones", response_model=Result[dict])
def create_zone(
    req: ZoneCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.create_zone(db, data=_payload(req), staff=staff)
    return Result.ok(warehouse_service.zone_dict(db, row), message="库区已创建")


@router.get("/zones/{zone_id}", response_model=Result[dict])
def get_zone(
    zone_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.get_zone_or_404(db, zone_id)
    return Result.ok(warehouse_service.zone_dict(db, row))


@router.put("/zones/{zone_id}", response_model=Result[dict])
def update_zone(
    zone_id: int,
    req: ZoneUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.update_zone(
        db,
        zone_id=zone_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(warehouse_service.zone_dict(db, row), message="库区已更新")


@router.delete("/zones/{zone_id}", response_model=Result[dict])
def delete_zone(
    zone_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    warehouse_service.delete_zone(db, zone_id=zone_id, staff=staff)
    return Result.ok(message="库区已删除")


# --------------------------------------------------------------------------- #
# 库位
# --------------------------------------------------------------------------- #


@router.get("/locations", response_model=Result[list[dict]])
def list_locations(
    warehouse_id: int | None = None,
    zone_id: int | None = None,
    is_enabled: bool | None = None,
    keyword: str | None = None,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    rows = warehouse_service.list_locations(
        db,
        warehouse_id=warehouse_id,
        zone_id=zone_id,
        is_enabled=is_enabled,
        keyword=keyword,
    )
    return Result.ok([warehouse_service.location_dict(db, item) for item in rows])


@router.post("/locations", response_model=Result[dict])
def create_location(
    req: LocationCreateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.create_location(db, data=_payload(req), staff=staff)
    return Result.ok(
        warehouse_service.location_dict(db, row),
        message="库位已创建",
    )


@router.post("/locations/generate", response_model=Result[dict])
def generate_locations(
    req: LocationGenerateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    rows = warehouse_service.generate_locations(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        {
            "generated_count": len(rows),
            "items": [
                warehouse_service.location_dict(db, item) for item in rows
            ],
        },
        message="库位已批量生成",
    )


@router.get("/locations/{location_id}", response_model=Result[dict])
def get_location(
    location_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.get_location_or_404(db, location_id)
    return Result.ok(warehouse_service.location_dict(db, row))


@router.get(
    "/locations/{location_id}/capacity",
    response_model=Result[dict],
)
def get_location_capacity(
    location_id: int,
    staff: Staff = Depends(read_stock),
    db: Session = Depends(get_db),
):
    return Result.ok(
        warehouse_service.location_capacity_state(db, location_id)
    )


@router.put("/locations/{location_id}", response_model=Result[dict])
def update_location(
    location_id: int,
    req: LocationUpdateRequest,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.update_location(
        db,
        location_id=location_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        warehouse_service.location_dict(db, row),
        message="库位已更新",
    )


@router.delete("/locations/{location_id}", response_model=Result[dict])
def delete_location(
    location_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    warehouse_service.delete_location(
        db,
        location_id=location_id,
        staff=staff,
    )
    return Result.ok(message="库位已删除")


@router.post("/locations/{location_id}/enable", response_model=Result[dict])
def enable_location(
    location_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.set_location_enabled(
        db,
        location_id=location_id,
        enabled=True,
        staff=staff,
    )
    return Result.ok(
        warehouse_service.location_dict(db, row),
        message="库位已启用",
    )


@router.post("/locations/{location_id}/disable", response_model=Result[dict])
def disable_location(
    location_id: int,
    staff: Staff = Depends(manage_stock),
    db: Session = Depends(get_db),
):
    row = warehouse_service.set_location_enabled(
        db,
        location_id=location_id,
        enabled=False,
        staff=staff,
    )
    return Result.ok(
        warehouse_service.location_dict(db, row),
        message="库位已停用",
    )
