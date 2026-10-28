from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import shop_service

router = APIRouter(tags=["店铺对接"])

read_shop = require_permission("module:settings", "module:order")
manage_settings = require_permission("settings:manage")


class PlatformCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    code: str | None = Field(default=None, max_length=32)


class PlatformUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    code: str | None = Field(default=None, max_length=32)


class ShopCreateRequest(BaseModel):
    platform_id: int
    name: str = Field(..., min_length=1, max_length=128)
    login_account: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=255)
    tag: str | None = Field(default=None, max_length=64)
    supplier_id: int | None = None
    default_address: str | None = Field(default=None, max_length=255)
    is_locked: bool = False
    auto_sync_order: bool = False
    auto_sync_stock: bool = False
    download_mode: str | None = Field(default=None, max_length=16)
    sms_ship_template: str | None = Field(default=None, max_length=64)
    sms_deliver_template: str | None = Field(default=None, max_length=64)
    enable_logistics_params: bool = False
    message_match: str | None = Field(default=None, max_length=255)


class ShopUpdateRequest(BaseModel):
    platform_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    login_account: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=255)
    tag: str | None = Field(default=None, max_length=64)
    supplier_id: int | None = None
    default_address: str | None = Field(default=None, max_length=255)
    is_locked: bool | None = None
    auto_sync_order: bool | None = None
    auto_sync_stock: bool | None = None
    download_mode: str | None = Field(default=None, max_length=16)
    sms_ship_template: str | None = Field(default=None, max_length=64)
    sms_deliver_template: str | None = Field(default=None, max_length=64)
    enable_logistics_params: bool | None = None
    message_match: str | None = Field(default=None, max_length=255)


class ShopApiRequest(BaseModel):
    api_code: str | None = Field(default=None, max_length=128)
    verify_code: str | None = Field(default=None, max_length=128)
    app_key: str | None = Field(default=None, max_length=128)
    app_secret: str | None = Field(default=None, max_length=128)


class SyncSwitchRequest(BaseModel):
    enabled: bool


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- #
# 电商平台
# --------------------------------------------------------------------------- #


@router.get("/platforms", response_model=Result[list[dict]])
def list_platforms(
    keyword: str | None = None,
    staff: Staff = Depends(read_shop),
    db: Session = Depends(get_db),
):
    platforms = shop_service.list_platforms(db, keyword=keyword)
    return Result.ok([shop_service.platform_dict(item) for item in platforms])


@router.post("/platforms", response_model=Result[dict])
def create_platform(
    req: PlatformCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    platform = shop_service.create_platform(db, data=_payload(req), staff=staff)
    return Result.ok(shop_service.platform_dict(platform), message="平台已创建")


@router.get("/platforms/{platform_id}", response_model=Result[dict])
def get_platform(
    platform_id: int,
    staff: Staff = Depends(read_shop),
    db: Session = Depends(get_db),
):
    platform = shop_service.get_platform_or_404(db, platform_id)
    return Result.ok(shop_service.platform_dict(platform))


@router.put("/platforms/{platform_id}", response_model=Result[dict])
def update_platform(
    platform_id: int,
    req: PlatformUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    platform = shop_service.update_platform(
        db, platform_id=platform_id, data=_payload(req), staff=staff
    )
    return Result.ok(shop_service.platform_dict(platform), message="平台已更新")


@router.delete("/platforms/{platform_id}", response_model=Result[dict])
def delete_platform(
    platform_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop_service.delete_platform(db, platform_id=platform_id, staff=staff)
    return Result.ok(message="平台已删除")


# --------------------------------------------------------------------------- #
# 店铺
# --------------------------------------------------------------------------- #


@router.get("/shops", response_model=Result[dict])
def list_shops(
    platform_id: int | None = None,
    keyword: str | None = None,
    is_locked: bool | None = None,
    auto_sync_order: bool | None = None,
    auto_sync_stock: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_shop),
    db: Session = Depends(get_db),
):
    return Result.ok(
        shop_service.list_shops(
            db,
            platform_id=platform_id,
            keyword=keyword,
            is_locked=is_locked,
            auto_sync_order=auto_sync_order,
            auto_sync_stock=auto_sync_stock,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/shops", response_model=Result[dict])
def create_shop(
    req: ShopCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop = shop_service.create_shop(db, data=_payload(req), staff=staff)
    return Result.ok(shop_service.shop_dict(db, shop), message="店铺已创建")


@router.get("/shops/{shop_id}", response_model=Result[dict])
def get_shop(
    shop_id: int,
    staff: Staff = Depends(read_shop),
    db: Session = Depends(get_db),
):
    shop = shop_service.get_shop_or_404(db, shop_id)
    return Result.ok(shop_service.shop_dict(db, shop, with_api=True))


@router.put("/shops/{shop_id}", response_model=Result[dict])
def update_shop(
    shop_id: int,
    req: ShopUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop = shop_service.update_shop(
        db, shop_id=shop_id, data=_payload(req), staff=staff
    )
    return Result.ok(shop_service.shop_dict(db, shop), message="店铺已更新")


@router.delete("/shops/{shop_id}", response_model=Result[dict])
def delete_shop(
    shop_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop_service.delete_shop(db, shop_id=shop_id, staff=staff)
    return Result.ok(message="店铺已删除")


@router.post("/shops/{shop_id}/sync-order", response_model=Result[dict])
def set_shop_sync_order(
    shop_id: int,
    req: SyncSwitchRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop = shop_service.set_shop_sync_switch(
        db,
        shop_id=shop_id,
        field="auto_sync_order",
        enabled=req.enabled,
        staff=staff,
    )
    return Result.ok(shop_service.shop_dict(db, shop), message="自动同步订单开关已更新")


@router.post("/shops/{shop_id}/sync-stock", response_model=Result[dict])
def set_shop_sync_stock(
    shop_id: int,
    req: SyncSwitchRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop = shop_service.set_shop_sync_switch(
        db,
        shop_id=shop_id,
        field="auto_sync_stock",
        enabled=req.enabled,
        staff=staff,
    )
    return Result.ok(shop_service.shop_dict(db, shop), message="自动同步库存开关已更新")


@router.post("/shops/{shop_id}/lock", response_model=Result[dict])
def lock_shop(
    shop_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop = shop_service.set_shop_locked(
        db, shop_id=shop_id, locked=True, staff=staff
    )
    return Result.ok(shop_service.shop_dict(db, shop), message="店铺已锁定")


@router.post("/shops/{shop_id}/unlock", response_model=Result[dict])
def unlock_shop(
    shop_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop = shop_service.set_shop_locked(
        db, shop_id=shop_id, locked=False, staff=staff
    )
    return Result.ok(shop_service.shop_dict(db, shop), message="店铺已解锁")


# --------------------------------------------------------------------------- #
# 店铺 API 配置
# --------------------------------------------------------------------------- #


@router.get("/shops/{shop_id}/api", response_model=Result[dict | None])
def get_shop_api(
    shop_id: int,
    staff: Staff = Depends(read_shop),
    db: Session = Depends(get_db),
):
    shop_service.get_shop_or_404(db, shop_id)
    api = shop_service.get_shop_api(db, shop_id)
    return Result.ok(shop_service.shop_api_dict(api) if api else None)


@router.put("/shops/{shop_id}/api", response_model=Result[dict])
def upsert_shop_api(
    shop_id: int,
    req: ShopApiRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    api = shop_service.upsert_shop_api(
        db, shop_id=shop_id, data=_payload(req), staff=staff
    )
    return Result.ok(shop_service.shop_api_dict(api), message="店铺 API 配置已保存")


@router.delete("/shops/{shop_id}/api", response_model=Result[dict])
def delete_shop_api(
    shop_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    shop_service.delete_shop_api(db, shop_id=shop_id, staff=staff)
    return Result.ok(message="店铺 API 配置已删除")
