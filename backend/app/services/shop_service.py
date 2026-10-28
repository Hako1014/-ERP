"""平台店铺与 API 对接配置服务（TASK-023）。

真实订单抓取、库存同步和平台授权在 TASK-065 落地；本模块负责配置维护。
"""

from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.db.base import Base
from app.models.org import Staff
from app.models.party import Supplier
from app.models.platform import Platform, Shop, ShopApi

PLATFORM_FIELDS = {"name", "code"}
SHOP_FIELDS = {
    "platform_id",
    "name",
    "login_account",
    "url",
    "tag",
    "supplier_id",
    "default_address",
    "is_locked",
    "auto_sync_order",
    "auto_sync_stock",
    "download_mode",
    "sms_ship_template",
    "sms_deliver_template",
    "enable_logistics_params",
    "message_match",
}
SHOP_API_FIELDS = {"api_code", "verify_code", "app_key", "app_secret"}
DOWNLOAD_MODES = {"ALL", "MATCH_PRODUCT"}
_SHOP_CHILD_TABLES = {"shop_api"}


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _require_text(value: Any, label: str, max_length: int) -> str:
    if value is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    if len(text) > max_length:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}")
    return text


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _require_text(value, label, max_length)


def _optional_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _check_unknown_fields(data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}"
        )


# --------------------------------------------------------------------------- #
# 电商平台
# --------------------------------------------------------------------------- #


def platform_dict(platform: Platform) -> dict:
    return {"id": platform.id, "name": platform.name, "code": platform.code}


def get_platform_or_404(db: Session, platform_id: int) -> Platform:
    platform = db.get(Platform, platform_id)
    if platform is None:
        raise BizException(ErrorCode.NOT_FOUND, "平台不存在")
    return platform


def list_platforms(db: Session, *, keyword: str | None = None) -> list[Platform]:
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(Platform.name.like(like), Platform.code.like(like)))
    return list(
        db.scalars(
            select(Platform)
            .where(*conditions)
            .order_by(Platform.id)
        )
    )


def _validate_platform_code_unique(
    db: Session, code: str | None, *, exclude_id: int | None = None
) -> None:
    if code is None:
        return
    stmt = select(Platform.id).where(Platform.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Platform.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"平台编码已存在：{code}")


def create_platform(db: Session, *, data: dict, staff: Staff, commit: bool = True) -> Platform:
    _check_unknown_fields(data, PLATFORM_FIELDS)
    code = _optional_text(data.get("code"), "平台编码", 32)
    _validate_platform_code_unique(db, code)
    platform = Platform(
        name=_require_text(data.get("name"), "平台名称", 64),
        code=code,
    )
    db.add(platform)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="platform",
        target_id=str(platform.id),
        summary=f"新增平台：{platform.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return platform


def update_platform(
    db: Session, *, platform_id: int, data: dict, staff: Staff, commit: bool = True
) -> Platform:
    _check_unknown_fields(data, PLATFORM_FIELDS)
    platform = get_platform_or_404(db, platform_id)
    if "name" in data:
        platform.name = _require_text(data["name"], "平台名称", 64)
    if "code" in data:
        code = _optional_text(data["code"], "平台编码", 32)
        _validate_platform_code_unique(db, code, exclude_id=platform.id)
        platform.code = code
    write_audit(
        db,
        action="UPDATE",
        target_type="platform",
        target_id=str(platform.id),
        summary=f"修改平台：{platform.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return platform


def delete_platform(
    db: Session, *, platform_id: int, staff: Staff, commit: bool = True
) -> None:
    platform = get_platform_or_404(db, platform_id)
    if db.scalar(select(func.count(Shop.id)).where(Shop.platform_id == platform.id)):
        raise BizException(ErrorCode.CONFLICT, "平台下存在店铺，不能删除")
    db.delete(platform)
    write_audit(
        db,
        action="DELETE",
        target_type="platform",
        target_id=str(platform.id),
        summary=f"删除平台：{platform.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 店铺
# --------------------------------------------------------------------------- #


def get_shop_or_404(db: Session, shop_id: int) -> Shop:
    shop = db.get(Shop, shop_id)
    if shop is None:
        raise BizException(ErrorCode.NOT_FOUND, "店铺不存在")
    return shop


def shop_dict(db: Session, shop: Shop, *, with_api: bool = False) -> dict:
    platform = db.get(Platform, shop.platform_id)
    supplier = db.get(Supplier, shop.supplier_id) if shop.supplier_id else None
    api = get_shop_api(db, shop.id)
    data = {
        "id": shop.id,
        "platform_id": shop.platform_id,
        "platform_name": platform.name if platform else None,
        "name": shop.name,
        "login_account": shop.login_account,
        "url": shop.url,
        "tag": shop.tag,
        "supplier_id": shop.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "default_address": shop.default_address,
        "is_locked": shop.is_locked,
        "auto_sync_order": shop.auto_sync_order,
        "auto_sync_stock": shop.auto_sync_stock,
        "download_mode": shop.download_mode,
        "sms_ship_template": shop.sms_ship_template,
        "sms_deliver_template": shop.sms_deliver_template,
        "enable_logistics_params": shop.enable_logistics_params,
        "message_match": shop.message_match,
        "api_configured": api is not None,
    }
    if with_api:
        data["api_config"] = shop_api_dict(api) if api else None
    return data


def _validate_shop_values(
    db: Session, data: dict, *, existing: Shop | None = None
) -> dict:
    _check_unknown_fields(data, SHOP_FIELDS)
    values: dict[str, Any] = {}

    if existing is None or "platform_id" in data:
        platform_id = _optional_id(data.get("platform_id"), "所属平台")
        if platform_id is None:
            raise BizException(ErrorCode.BAD_REQUEST, "所属平台不能为空")
        get_platform_or_404(db, platform_id)
        values["platform_id"] = platform_id
    if existing is None or "name" in data:
        values["name"] = _require_text(data.get("name"), "店铺名称", 128)

    for field, label, max_length in (
        ("login_account", "登录账户", 64),
        ("url", "店铺网址", 255),
        ("tag", "店铺 tag", 64),
        ("default_address", "默认发货地址", 255),
        ("sms_ship_template", "发货短信模板", 64),
        ("sms_deliver_template", "派件短信模板", 64),
        ("message_match", "留言匹配", 255),
    ):
        if field in data:
            values[field] = _optional_text(data[field], label, max_length)

    if "supplier_id" in data:
        supplier_id = _optional_id(data["supplier_id"], "供应商")
        if supplier_id is not None and db.get(Supplier, supplier_id) is None:
            raise BizException(ErrorCode.BAD_REQUEST, "供应商不存在")
        values["supplier_id"] = supplier_id

    for field, label in (
        ("is_locked", "锁定"),
        ("auto_sync_order", "自动同步订单"),
        ("auto_sync_stock", "自动同步库存"),
        ("enable_logistics_params", "启用物流参数"),
    ):
        if field in data:
            values[field] = _to_bool(data[field], label)

    if "download_mode" in data:
        mode = _optional_text(data["download_mode"], "下载订单方式", 16)
        if mode is not None:
            mode = mode.upper()
            if mode not in DOWNLOAD_MODES:
                raise BizException(
                    ErrorCode.BAD_REQUEST,
                    "下载订单方式仅支持 ALL / MATCH_PRODUCT",
                )
        values["download_mode"] = mode
    return values


def create_shop(db: Session, *, data: dict, staff: Staff, commit: bool = True) -> Shop:
    values = _validate_shop_values(db, data)
    values.setdefault("is_locked", False)
    values.setdefault("auto_sync_order", False)
    values.setdefault("auto_sync_stock", False)
    values.setdefault("enable_logistics_params", False)
    shop = Shop(**values)
    db.add(shop)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="shop",
        target_id=str(shop.id),
        summary=f"新增店铺：{shop.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return shop


def update_shop(
    db: Session, *, shop_id: int, data: dict, staff: Staff, commit: bool = True
) -> Shop:
    shop = get_shop_or_404(db, shop_id)
    values = _validate_shop_values(db, data, existing=shop)
    for field, value in values.items():
        setattr(shop, field, value)
    write_audit(
        db,
        action="UPDATE",
        target_type="shop",
        target_id=str(shop.id),
        summary=f"修改店铺：{shop.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return shop


def list_shops(
    db: Session,
    *,
    platform_id: int | None = None,
    keyword: str | None = None,
    is_locked: bool | None = None,
    auto_sync_order: bool | None = None,
    auto_sync_stock: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if platform_id is not None:
        conditions.append(Shop.platform_id == platform_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(Shop.name.like(like), Shop.login_account.like(like), Shop.tag.like(like))
        )
    if is_locked is not None:
        conditions.append(Shop.is_locked.is_(is_locked))
    if auto_sync_order is not None:
        conditions.append(Shop.auto_sync_order.is_(auto_sync_order))
    if auto_sync_stock is not None:
        conditions.append(Shop.auto_sync_stock.is_(auto_sync_stock))

    total = db.scalar(select(func.count(Shop.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Shop)
            .where(*conditions)
            .order_by(Shop.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [shop_dict(db, row) for row in rows],
    }


def set_shop_sync_switch(
    db: Session,
    *,
    shop_id: int,
    field: str,
    enabled: bool,
    staff: Staff,
    commit: bool = True,
) -> Shop:
    if field not in {"auto_sync_order", "auto_sync_stock"}:
        raise BizException(ErrorCode.BAD_REQUEST, "不支持的同步开关")
    shop = get_shop_or_404(db, shop_id)
    setattr(shop, field, enabled)
    label = "订单" if field == "auto_sync_order" else "库存"
    write_audit(
        db,
        action="ENABLE" if enabled else "DISABLE",
        target_type="shop",
        target_id=str(shop.id),
        summary=f"{'开启' if enabled else '关闭'}店铺自动同步{label}：{shop.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return shop


def set_shop_locked(
    db: Session,
    *,
    shop_id: int,
    locked: bool,
    staff: Staff,
    commit: bool = True,
) -> Shop:
    shop = get_shop_or_404(db, shop_id)
    shop.is_locked = locked
    write_audit(
        db,
        action="LOCK" if locked else "UNLOCK",
        target_type="shop",
        target_id=str(shop.id),
        summary=f"{'锁定' if locked else '解锁'}店铺：{shop.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return shop


def _shop_referenced_tables(db: Session, shop_id: int) -> list[str]:
    used: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name in _SHOP_CHILD_TABLES:
            continue
        for column in table.columns:
            if not any(fk.target_fullname == "shop.id" for fk in column.foreign_keys):
                continue
            stmt = select(literal(1)).select_from(table).where(column == shop_id).limit(1)
            if db.execute(stmt).first() is not None:
                used.append(table.name)
                break
    return used


def delete_shop(db: Session, *, shop_id: int, staff: Staff, commit: bool = True) -> None:
    shop = get_shop_or_404(db, shop_id)
    referenced = _shop_referenced_tables(db, shop.id)
    if referenced:
        raise BizException(
            ErrorCode.CONFLICT,
            f"店铺已被 {','.join(sorted(referenced))} 引用，不能删除，请改为锁定",
        )
    api = get_shop_api(db, shop.id)
    if api is not None:
        db.delete(api)
    db.delete(shop)
    write_audit(
        db,
        action="DELETE",
        target_type="shop",
        target_id=str(shop.id),
        summary=f"删除店铺：{shop.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 店铺 API 配置
# --------------------------------------------------------------------------- #


def get_shop_api(db: Session, shop_id: int) -> ShopApi | None:
    return db.scalar(
        select(ShopApi).where(ShopApi.shop_id == shop_id).order_by(ShopApi.id)
    )


def shop_api_dict(api: ShopApi) -> dict:
    return {
        "id": api.id,
        "shop_id": api.shop_id,
        "api_code": api.api_code,
        "verify_code": api.verify_code,
        "app_key": api.app_key,
        "has_app_secret": bool(api.app_secret),
    }


def upsert_shop_api(
    db: Session,
    *,
    shop_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ShopApi:
    _check_unknown_fields(data, SHOP_API_FIELDS)
    shop = get_shop_or_404(db, shop_id)
    api = get_shop_api(db, shop.id)
    created = api is None
    if api is None:
        api = ShopApi(shop_id=shop.id)
        db.add(api)

    for field, label, max_length in (
        ("api_code", "API 关联码", 128),
        ("verify_code", "验证码", 128),
        ("app_key", "AppKey", 128),
        ("app_secret", "AppSecret", 128),
    ):
        if field in data:
            setattr(api, field, _optional_text(data[field], label, max_length))
    db.flush()
    write_audit(
        db,
        action="CREATE" if created else "UPDATE",
        target_type="shop_api",
        target_id=str(api.id),
        summary=f"{'新增' if created else '修改'}店铺 API 配置：{shop.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return api


def delete_shop_api(
    db: Session, *, shop_id: int, staff: Staff, commit: bool = True
) -> None:
    shop = get_shop_or_404(db, shop_id)
    api = get_shop_api(db, shop.id)
    if api is None:
        raise BizException(ErrorCode.NOT_FOUND, "店铺 API 配置不存在")
    db.delete(api)
    write_audit(
        db,
        action="DELETE",
        target_type="shop_api",
        target_id=str(api.id),
        summary=f"删除店铺 API 配置：{shop.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
