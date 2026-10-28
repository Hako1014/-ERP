"""商品资料与商品设置服务（TASK-020）。

覆盖范围：商品 CRUD、SKU 维护、上下架/禁用、批量删除、Excel 导入导出，
以及商品设置（分类/品牌/计量单位/单位组/规格类型/规格明细）维护。

组合商品：PRD 仅有「组合商品列表」与打印展示「主商品/配件」的描述，
缺少数据结构与业务规则，未在本任务内实现，待需求确认后再建模型与任务卡。
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from sqlalchemy import and_, func, literal, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money, to_decimal
from app.db.base import Base
from app.models.basic import (
    Brand,
    Category,
    SpecType,
    SpecValue,
    Unit,
    UnitGroup,
    UnitGroupItem,
)
from app.models.org import Staff
from app.models.product import ComboProduct, ComboProductItem, Product, ProductSku
from app.services.excel import Column, ParseResult, SheetSpec, export_data, parse_data, register_template

MEASURE_TYPES = {"SINGLE", "MULTI"}

_MONEY_FIELDS = {"online_price", "tag_price", "min_price", "cost_price"}
_DECIMAL_FIELDS = {"weight", "volume", "tax_rate"}
_INT_FIELDS = {"min_stock", "max_stock", "warn_stock", "category_id", "brand_id",
               "default_supplier_id", "unit_group_id", "main_unit_id"}
_BOOL_FIELDS = {"is_gift", "use_float_cost", "is_disabled"}
_STR_FIELDS = {"name", "code", "art_no", "tax_code", "barcode", "description",
               "image", "measure_type"}
_PRODUCT_FIELDS = (
    _MONEY_FIELDS | _DECIMAL_FIELDS | _INT_FIELDS | _BOOL_FIELDS | _STR_FIELDS
)

PRODUCT_TEMPLATE = SheetSpec(
    name="product",
    sheet_title="商品资料",
    columns=[
        Column("code", "商品编码", required=True, width=20),
        Column("name", "商品名称", required=True, width=28),
        Column("art_no", "商品货号", width=20),
        Column("category_name", "分类", width=16),
        Column("brand_name", "品牌", width=16),
        Column("unit_name", "主计量单位", width=14),
        Column("barcode", "商品条码", width=20),
        Column("online_price", "网上售价", type="decimal", width=14),
        Column("tag_price", "吊牌价", type="decimal", width=14),
        Column("cost_price", "成本价", type="decimal", width=14),
        Column("warn_stock", "警戒库存", type="int", width=12),
        Column("description", "商品描述", width=30),
    ],
)

register_template(PRODUCT_TEMPLATE.name, PRODUCT_TEMPLATE)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _to_money(value: Any, label: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc


def _to_decimal(value: Any, label: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return to_decimal(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数字") from exc


# --------------------------------------------------------------------------- #
# 商品资料
# --------------------------------------------------------------------------- #


def get_product(
    db: Session, product_id: int, *, company_id: int | None = None,
) -> Product | None:
    stmt = select(Product).where(Product.id == product_id)
    if company_id is not None:
        stmt = stmt.where(Product.company_id == company_id)
    return db.scalar(stmt)


def get_product_or_404(
    db: Session, product_id: int, *, company_id: int | None = None,
) -> Product:
    product = get_product(db, product_id, company_id=company_id)
    if product is None:
        raise BizException(ErrorCode.NOT_FOUND, "商品不存在")
    return product


def _validate_code_unique(db: Session, code: str, exclude_id: int | None = None) -> None:
    stmt = select(Product.id).where(Product.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"商品编码已存在：{code}")


def _validate_barcode_unique(
    db: Session, barcode: str | None, product_id: int, exclude_sku_id: int | None = None
) -> None:
    if not barcode:
        return
    stmt = select(ProductSku.id).where(ProductSku.barcode == barcode)
    if exclude_sku_id is not None:
        stmt = stmt.where(ProductSku.id != exclude_sku_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"商品条码已被其他规格占用：{barcode}")
    other = db.scalar(select(Product.id).where(Product.barcode == barcode, Product.id != product_id))
    if other is not None:
        raise BizException(ErrorCode.CONFLICT, f"商品条码已被其他商品占用：{barcode}")


def _apply_product_fields(db: Session, product: Product, data: dict) -> None:
    """先完成全部校验与类型转换，再一次性赋值，避免校验失败留下半成品状态。"""
    unknown = set(data) - _PRODUCT_FIELDS
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")

    values: dict[str, Any] = {}
    if "measure_type" in data:
        measure_type = data["measure_type"] or "SINGLE"
        if measure_type not in MEASURE_TYPES:
            raise BizException(ErrorCode.BAD_REQUEST, "计量方式仅支持 SINGLE / MULTI")
        values["measure_type"] = measure_type

    if "name" in data:
        values["name"] = _require_text(data["name"], "商品名称", 128)
    if "code" in data:
        code = _require_text(data["code"], "商品编码", 64)
        _validate_code_unique(db, code, exclude_id=product.id)
        values["code"] = code

    for field in _STR_FIELDS - {"name", "code", "measure_type"}:
        if field in data:
            value = data[field]
            values[field] = str(value).strip() if value not in (None, "") else None

    for field in _MONEY_FIELDS:
        if field in data:
            values[field] = _to_money(data[field], field)
    for field in _DECIMAL_FIELDS:
        if field in data:
            values[field] = _to_decimal(data[field], field)
    for field in _INT_FIELDS:
        if field in data:
            values[field] = _to_optional_int(data[field], field)
    for field in _BOOL_FIELDS:
        if field in data:
            values[field] = bool(data[field])

    merged = {
        field: values.get(field, getattr(product, field)) for field in _PRODUCT_FIELDS
    }
    _validate_stock_range(merged)
    resolved_unit = _validate_measure(db, merged)
    if resolved_unit is not None:
        values["main_unit_id"] = resolved_unit

    for field, value in values.items():
        setattr(product, field, value)


def _validate_stock_range(values: dict) -> None:
    for field in ("min_stock", "max_stock", "warn_stock"):
        value = values.get(field)
        if value is not None and value < 0:
            raise BizException(ErrorCode.BAD_REQUEST, "库存数量不能为负数")
    max_stock = values.get("max_stock")
    min_stock = values.get("min_stock")
    if max_stock and min_stock and max_stock < min_stock:
        raise BizException(ErrorCode.BAD_REQUEST, "最大库存不能小于最小库存")


def _to_optional_int(value: Any, label: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc


def _to_required_int(value: Any, label: str) -> int:
    result = _to_optional_int(value, label)
    if result is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    return result


def _validate_measure(db: Session, values: dict) -> int | None:
    """校验计量方式；多计量且未指定主单位时返回单位组主单位 ID。"""
    if values.get("measure_type") == "MULTI":
        unit_group_id = values.get("unit_group_id")
        if not unit_group_id:
            raise BizException(ErrorCode.BAD_REQUEST, "多计量商品必须指定计量单位组")
        group = db.get(UnitGroup, unit_group_id)
        if group is None:
            raise BizException(ErrorCode.BAD_REQUEST, "计量单位组不存在")
        unit_ids = {
            item.unit_id
            for item in db.scalars(
                select(UnitGroupItem).where(UnitGroupItem.unit_group_id == group.id)
            )
        }
        unit_ids.add(group.main_unit_id)
        main_unit_id = values.get("main_unit_id")
        if main_unit_id is None:
            return group.main_unit_id
        if main_unit_id not in unit_ids:
            raise BizException(ErrorCode.BAD_REQUEST, "主计量单位不属于所选计量单位组")
        return None

    main_unit_id = values.get("main_unit_id")
    if main_unit_id is not None and db.get(Unit, main_unit_id) is None:
        raise BizException(ErrorCode.BAD_REQUEST, "主计量单位不存在")
    return None


def create_product(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    skus: list[dict] | None = None,
    commit: bool = True,
) -> Product:
    product = Product(
        name="",
        code="",
        min_stock=0,
        max_stock=0,
        warn_stock=0,
        is_gift=False,
        use_float_cost=False,
        is_disabled=False,
        measure_type="SINGLE",
    )
    if "name" not in data or "code" not in data:
        raise BizException(ErrorCode.BAD_REQUEST, "商品名称与商品编码为必填项")
    _apply_product_fields(db, product, data)
    db.add(product)
    db.flush()

    for sku_data in skus or []:
        create_sku(db, product_id=product.id, data=sku_data, commit=False)

    write_audit(
        db,
        action="CREATE",
        target_type="product",
        target_id=str(product.id),
        summary=f"新增商品：{product.name}",
        detail={"code": product.code, "skus": len(skus or [])},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return product


def update_product(
    db: Session,
    *,
    product_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Product:
    product = get_product_or_404(db, product_id)
    _apply_product_fields(db, product, data)
    write_audit(
        db,
        action="UPDATE",
        target_type="product",
        target_id=str(product.id),
        summary=f"修改商品：{product.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return product


def list_products(
    db: Session,
    *,
    keyword: str | None = None,
    category_id: int | None = None,
    brand_id: int | None = None,
    listed: bool | None = None,
    is_disabled: bool | None = None,
    page: int = 1,
    page_size: int = 20,
    company_id: int | None = None,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if company_id is not None:
        conditions.append(Product.company_id == company_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            Product.name.like(like) | Product.code.like(like) | Product.art_no.like(like)
        )
    if category_id:
        conditions.append(Product.category_id == category_id)
    if brand_id:
        conditions.append(Product.brand_id == brand_id)
    if listed is True:
        conditions.append(Product.listed_at.is_not(None))
    elif listed is False:
        conditions.append(Product.listed_at.is_(None))
    if is_disabled is not None:
        conditions.append(Product.is_disabled.is_(is_disabled))

    total = db.scalar(select(func.count(Product.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Product)
            .where(*conditions)
            .order_by(Product.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            product_dict(db, product, company_id=company_id) for product in rows
        ],
    }


def product_dict(
    db: Session,
    product: Product,
    *,
    with_skus: bool = False,
    company_id: int | None = None,
) -> dict:
    def related(model, row_id):
        if row_id is None:
            return None
        stmt = select(model).where(model.id == row_id)
        if company_id is not None:
            stmt = stmt.where(model.company_id == company_id)
        return db.scalar(stmt)

    category = related(Category, product.category_id)
    brand = related(Brand, product.brand_id)
    unit = related(Unit, product.main_unit_id)
    data = {
        "id": product.id,
        "name": product.name,
        "code": product.code,
        "art_no": product.art_no,
        "category_id": product.category_id,
        "category_name": category.name if category else None,
        "brand_id": product.brand_id,
        "brand_name": brand.name if brand else None,
        "default_supplier_id": product.default_supplier_id,
        "measure_type": product.measure_type,
        "unit_group_id": product.unit_group_id,
        "main_unit_id": product.main_unit_id,
        "unit_name": unit.name if unit else None,
        "online_price": product.online_price,
        "tag_price": product.tag_price,
        "min_price": product.min_price,
        "cost_price": product.cost_price,
        "weight": product.weight,
        "volume": product.volume,
        "min_stock": product.min_stock,
        "max_stock": product.max_stock,
        "warn_stock": product.warn_stock,
        "tax_rate": product.tax_rate,
        "tax_code": product.tax_code,
        "barcode": product.barcode,
        "is_gift": product.is_gift,
        "use_float_cost": product.use_float_cost,
        "is_disabled": product.is_disabled,
        "listed": product.listed_at is not None,
        "listed_at": product.listed_at,
        "description": product.description,
        "image": product.image,
    }
    if with_skus:
        data["skus"] = [
            sku_dict(sku)
            for sku in list_skus(db, product.id, company_id=company_id)
        ]
    return data


def set_listing(
    db: Session,
    *,
    product_ids: Iterable[int],
    listed: bool,
    staff: Staff,
    commit: bool = True,
) -> int:
    ids = _clean_ids(product_ids)
    products = list(db.scalars(select(Product).where(Product.id.in_(ids))))
    if len(products) != len(ids):
        raise BizException(ErrorCode.NOT_FOUND, "部分商品不存在")
    for product in products:
        if listed and product.is_disabled:
            raise BizException(
                ErrorCode.CONFLICT, f"商品「{product.name}」已禁用，不能上架"
            )
        product.listed_at = _now() if listed else None
    write_audit(
        db,
        action="LIST" if listed else "UNLIST",
        target_type="product",
        target_id=",".join(str(i) for i in ids),
        summary=("商品上架" if listed else "商品下架") + f"：{len(ids)} 个",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return len(ids)


def set_disabled(
    db: Session,
    *,
    product_id: int,
    disabled: bool,
    staff: Staff,
    commit: bool = True,
) -> Product:
    product = get_product_or_404(db, product_id)
    product.is_disabled = disabled
    if disabled:
        # 禁用即下架，避免店铺继续售卖。
        product.listed_at = None
    write_audit(
        db,
        action="DISABLE" if disabled else "ENABLE",
        target_type="product",
        target_id=str(product.id),
        summary=f"{'禁用' if disabled else '启用'}商品：{product.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return product


def _clean_ids(ids: Iterable[int]) -> list[int]:
    cleaned = sorted({int(i) for i in ids})
    if not cleaned:
        raise BizException(ErrorCode.BAD_REQUEST, "请选择商品")
    return cleaned


def _referenced_tables(db: Session, product_id: int) -> list[str]:
    """检查除 product_sku 外仍引用该商品的业务表。"""
    used: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name == "product_sku":
            continue
        column = table.columns.get("product_id")
        if column is None:
            continue
        if not any(fk.target_fullname == "product.id" for fk in column.foreign_keys):
            continue
        stmt = select(literal(1)).select_from(table).where(column == product_id).limit(1)
        if db.execute(stmt).first() is not None:
            used.append(table.name)
    return used


def delete_products(
    db: Session,
    *,
    product_ids: Iterable[int],
    staff: Staff,
    commit: bool = True,
) -> dict:
    ids = _clean_ids(product_ids)
    products = list(db.scalars(select(Product).where(Product.id.in_(ids))))
    if len(products) != len(ids):
        raise BizException(ErrorCode.NOT_FOUND, "部分商品不存在")

    blocked: dict[str, list[str]] = {}
    for product in products:
        tables = _referenced_tables(db, product.id)
        if tables:
            blocked[product.code] = tables
    if blocked:
        detail = "；".join(f"{code}（被 {','.join(t)} 引用）" for code, t in blocked.items())
        raise BizException(
            ErrorCode.CONFLICT, f"以下商品已被业务单据引用，不能删除：{detail}"
        )

    for product in products:
        for sku in db.scalars(select(ProductSku).where(ProductSku.product_id == product.id)):
            db.delete(sku)
        db.delete(product)

    write_audit(
        db,
        action="DELETE",
        target_type="product",
        target_id=",".join(str(i) for i in ids),
        summary=f"删除商品：{len(ids)} 个",
        detail={"codes": [p.code for p in products]},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {"deleted": len(ids)}


# --------------------------------------------------------------------------- #
# 商品规格 SKU
# --------------------------------------------------------------------------- #


def list_skus(
    db: Session, product_id: int, *, company_id: int | None = None,
) -> list[ProductSku]:
    conditions = [ProductSku.product_id == product_id]
    if company_id is not None:
        conditions.append(ProductSku.company_id == company_id)
    return list(
        db.scalars(
            select(ProductSku)
            .where(*conditions)
            .order_by(ProductSku.id)
        )
    )


def sku_dict(sku: ProductSku) -> dict:
    return {
        "id": sku.id,
        "product_id": sku.product_id,
        "spec_name": sku.spec_name,
        "spec_code": sku.spec_code,
        "barcode": sku.barcode,
        "tag_price": sku.tag_price,
        "sale_price": sku.sale_price,
        "cost_price": sku.cost_price,
        "min_price": sku.min_price,
        "image": sku.image,
        "is_active": sku.is_active,
    }


_SKU_FIELDS = {"spec_name", "spec_code", "barcode", "tag_price", "sale_price",
               "cost_price", "min_price", "image", "is_active"}


def _validate_spec_code_unique(
    db: Session, spec_code: str, exclude_sku_id: int | None = None
) -> None:
    stmt = select(ProductSku.id).where(ProductSku.spec_code == spec_code)
    if exclude_sku_id is not None:
        stmt = stmt.where(ProductSku.id != exclude_sku_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"规格编码已存在：{spec_code}")


def create_sku(
    db: Session,
    *,
    product_id: int,
    data: dict,
    commit: bool = True,
) -> ProductSku:
    get_product_or_404(db, product_id)
    unknown = set(data) - _SKU_FIELDS
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")
    spec_code = _require_text(data.get("spec_code"), "规格编码", 64)
    _validate_spec_code_unique(db, spec_code)
    barcode = data.get("barcode")
    _validate_barcode_unique(db, barcode, product_id)

    sku = ProductSku(
        product_id=product_id,
        spec_code=spec_code,
        spec_name=data.get("spec_name"),
        barcode=barcode,
        tag_price=_to_money(data.get("tag_price"), "吊牌价"),
        sale_price=_to_money(data.get("sale_price"), "销售价"),
        cost_price=_to_money(data.get("cost_price"), "成本价"),
        min_price=_to_money(data.get("min_price"), "最低售价"),
        image=data.get("image"),
        is_active=bool(data.get("is_active", True)),
    )
    db.add(sku)
    _commit(db, commit)
    return sku


def update_sku(
    db: Session,
    *,
    sku_id: int,
    data: dict,
    commit: bool = True,
) -> ProductSku:
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise BizException(ErrorCode.NOT_FOUND, "商品规格不存在")
    unknown = set(data) - _SKU_FIELDS
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")

    if "spec_code" in data:
        spec_code = _require_text(data["spec_code"], "规格编码", 64)
        _validate_spec_code_unique(db, spec_code, exclude_sku_id=sku.id)
        sku.spec_code = spec_code
    if "barcode" in data:
        _validate_barcode_unique(db, data["barcode"], sku.product_id, exclude_sku_id=sku.id)
        sku.barcode = data["barcode"]
    if "spec_name" in data:
        sku.spec_name = data["spec_name"]
    if "image" in data:
        sku.image = data["image"]
    if "is_active" in data:
        sku.is_active = bool(data["is_active"])
    for field, label in (
        ("tag_price", "吊牌价"),
        ("sale_price", "销售价"),
        ("cost_price", "成本价"),
        ("min_price", "最低售价"),
    ):
        if field in data:
            setattr(sku, field, _to_money(data[field], label))
    _commit(db, commit)
    return sku


def delete_sku(db: Session, *, sku_id: int, commit: bool = True) -> None:
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise BizException(ErrorCode.NOT_FOUND, "商品规格不存在")
    for table in Base.metadata.sorted_tables:
        column = table.columns.get("sku_id")
        if column is None:
            continue
        if not any(fk.target_fullname == "product_sku.id" for fk in column.foreign_keys):
            continue
        stmt = select(literal(1)).select_from(table).where(column == sku.id).limit(1)
        if db.execute(stmt).first() is not None:
            raise BizException(
                ErrorCode.CONFLICT, f"规格已被 {table.name} 引用，不能删除"
            )
    db.delete(sku)
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 组合商品（主商品 + 配件清单）
# --------------------------------------------------------------------------- #

_COMBO_FIELDS = {
    "combo_no",
    "name",
    "main_product_id",
    "main_sku_id",
    "is_enabled",
    "remark",
}


def get_combo_or_404(
    db: Session, combo_id: int, *, company_id: int | None = None,
) -> ComboProduct:
    stmt = select(ComboProduct).where(ComboProduct.id == combo_id)
    if company_id is not None:
        stmt = stmt.where(ComboProduct.company_id == company_id)
    combo = db.scalar(stmt)
    if combo is None:
        raise BizException(ErrorCode.NOT_FOUND, "组合商品不存在")
    return combo


def combo_dict(
    db: Session,
    combo: ComboProduct,
    *,
    with_items: bool = True,
    company_id: int | None = None,
) -> dict:
    main_product_stmt = select(Product).where(Product.id == combo.main_product_id)
    main_sku_stmt = (
        select(ProductSku).where(ProductSku.id == combo.main_sku_id)
        if combo.main_sku_id else None
    )
    if company_id is not None:
        main_product_stmt = main_product_stmt.where(Product.company_id == company_id)
        if main_sku_stmt is not None:
            main_sku_stmt = main_sku_stmt.where(ProductSku.company_id == company_id)
    main_product = db.scalar(main_product_stmt)
    main_sku = db.scalar(main_sku_stmt) if main_sku_stmt is not None else None
    data = {
        "id": combo.id,
        "combo_no": combo.combo_no,
        "name": combo.name,
        "main_product_id": combo.main_product_id,
        "main_product_name": main_product.name if main_product else None,
        "main_sku_id": combo.main_sku_id,
        "main_sku_spec_code": main_sku.spec_code if main_sku else None,
        "is_enabled": combo.is_enabled,
        "remark": combo.remark,
    }
    if with_items:
        item_conditions = [ComboProductItem.combo_id == combo.id]
        if company_id is not None:
            item_conditions.append(ComboProductItem.company_id == company_id)
        items = db.scalars(
            select(ComboProductItem)
            .where(*item_conditions)
            .order_by(ComboProductItem.sort_order, ComboProductItem.id)
        )
        rows = []
        for item in items:
            product_stmt = select(Product).where(Product.id == item.product_id)
            if company_id is not None:
                product_stmt = product_stmt.where(Product.company_id == company_id)
            product = db.scalar(product_stmt)
            rows.append(
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "product_name": product.name if product else None,
                    "sku_id": item.sku_id,
                    "qty": item.qty,
                    "sort_order": item.sort_order,
                }
            )
        data["items"] = rows
    return data


def list_combos(
    db: Session,
    *,
    keyword: str | None = None,
    is_enabled: bool | None = None,
    page: int = 1,
    page_size: int = 20,
    company_id: int | None = None,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if company_id is not None:
        conditions.append(ComboProduct.company_id == company_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(ComboProduct.name.like(like) | ComboProduct.combo_no.like(like))
    if is_enabled is not None:
        conditions.append(ComboProduct.is_enabled.is_(is_enabled))
    total = db.scalar(select(func.count(ComboProduct.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(ComboProduct)
            .where(*conditions)
            .order_by(ComboProduct.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            combo_dict(db, combo, with_items=False, company_id=company_id)
            for combo in rows
        ],
    }


def _validate_sku_of_product(db: Session, product_id: int, sku_id: int | None, label: str) -> None:
    if sku_id is None:
        return
    sku = db.get(ProductSku, sku_id)
    if sku is None or sku.product_id != product_id:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}规格不属于对应商品")


def _assert_not_nested(db: Session, product_id: int, *, exclude_combo_id: int | None = None) -> None:
    """不允许嵌套组合：被引用的商品不能是其他组合商品的主商品。"""
    stmt = select(ComboProduct.id).where(ComboProduct.main_product_id == product_id)
    if exclude_combo_id is not None:
        stmt = stmt.where(ComboProduct.id != exclude_combo_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.BAD_REQUEST, "配件不能是其他组合商品的主商品，不支持嵌套组合")


def _assert_main_not_accessory(
    db: Session, product_id: int, *, exclude_combo_id: int | None = None
) -> None:
    """不允许嵌套组合：主商品不能同时是其他组合商品的配件。"""
    stmt = select(ComboProductItem.id).where(ComboProductItem.product_id == product_id)
    if exclude_combo_id is not None:
        stmt = stmt.where(ComboProductItem.combo_id != exclude_combo_id)
    if db.scalar(stmt) is not None:
        raise BizException(
            ErrorCode.BAD_REQUEST, "主商品已作为其他组合商品的配件，不支持嵌套组合"
        )


def _validate_combo_items(
    db: Session,
    *,
    main_product_id: int,
    main_sku_id: int | None,
    items: list[dict],
    exclude_combo_id: int | None = None,
) -> list[dict]:
    if not items:
        raise BizException(ErrorCode.BAD_REQUEST, "组合商品至少需要一个配件")

    normalized: list[dict] = []
    seen: set[tuple[int, int | None]] = set()
    for index, item in enumerate(items):
        try:
            product_id = int(item["product_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BizException(ErrorCode.BAD_REQUEST, "配件明细格式错误") from exc
        sku_id = item.get("sku_id")
        if sku_id not in (None, ""):
            try:
                sku_id = int(sku_id)
            except (TypeError, ValueError) as exc:
                raise BizException(ErrorCode.BAD_REQUEST, "配件规格 ID 非法") from exc
        else:
            sku_id = None

        key = (product_id, sku_id)
        if key in seen:
            raise BizException(ErrorCode.BAD_REQUEST, "配件清单中存在重复项")
        seen.add(key)
        if product_id == main_product_id and sku_id == main_sku_id:
            raise BizException(ErrorCode.BAD_REQUEST, "配件不能与主商品完全相同")

        get_product_or_404(db, product_id)
        _validate_sku_of_product(db, product_id, sku_id, "配件")
        _assert_not_nested(db, product_id, exclude_combo_id=exclude_combo_id)

        qty = _to_decimal(item.get("qty"), "配件数量")
        if qty is None or qty <= 0:
            raise BizException(ErrorCode.BAD_REQUEST, "配件数量必须大于 0")
        normalized.append(
            {
                "product_id": product_id,
                "sku_id": sku_id,
                "qty": qty,
                "sort_order": int(item.get("sort_order", index) or 0),
            }
        )
    return normalized


def _validate_combo_no_unique(
    db: Session, combo_no: str, *, exclude_combo_id: int | None = None
) -> None:
    stmt = select(ComboProduct.id).where(ComboProduct.combo_no == combo_no)
    if exclude_combo_id is not None:
        stmt = stmt.where(ComboProduct.id != exclude_combo_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"组合编码已存在：{combo_no}")


def _apply_combo_fields(
    db: Session, combo: ComboProduct, data: dict, *, exclude_combo_id: int | None = None
) -> None:
    unknown = set(data) - _COMBO_FIELDS
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")

    values: dict[str, Any] = {}
    if "combo_no" in data:
        combo_no = _require_text(data["combo_no"], "组合编码", 64)
        _validate_combo_no_unique(db, combo_no, exclude_combo_id=exclude_combo_id)
        values["combo_no"] = combo_no
    if "name" in data:
        values["name"] = _require_text(data["name"], "组合商品名称", 128)
    if "remark" in data:
        values["remark"] = data["remark"]
    if "is_enabled" in data:
        values["is_enabled"] = bool(data["is_enabled"])

    main_product_id = data.get("main_product_id", combo.main_product_id)
    if "main_product_id" in data:
        main_product_id = _to_required_int(data["main_product_id"], "主商品")
        get_product_or_404(db, main_product_id)
        _assert_not_nested(db, main_product_id, exclude_combo_id=exclude_combo_id)
        _assert_main_not_accessory(db, main_product_id, exclude_combo_id=exclude_combo_id)
        values["main_product_id"] = main_product_id

    if "main_sku_id" in data:
        main_sku_id = _to_optional_int(data["main_sku_id"], "主商品规格")
        _validate_sku_of_product(db, main_product_id, main_sku_id, "主商品")
        values["main_sku_id"] = main_sku_id
    elif "main_product_id" in data and combo.main_sku_id:
        # 主商品变更后原主规格可能不再属于该商品。
        _validate_sku_of_product(db, main_product_id, combo.main_sku_id, "主商品")

    for field, value in values.items():
        setattr(combo, field, value)


def create_combo(
    db: Session,
    *,
    data: dict,
    items: list[dict],
    staff: Staff,
    commit: bool = True,
) -> ComboProduct:
    if "combo_no" not in data or "name" not in data or "main_product_id" not in data:
        raise BizException(ErrorCode.BAD_REQUEST, "组合编码、组合名称、主商品为必填项")

    _validate_combo_no_unique(db, _require_text(data["combo_no"], "组合编码", 64))
    main_product_id = _to_required_int(data["main_product_id"], "主商品")
    main_sku_id = _to_optional_int(data.get("main_sku_id"), "主商品规格")
    get_product_or_404(db, main_product_id)
    _validate_sku_of_product(db, main_product_id, main_sku_id, "主商品")
    _assert_not_nested(db, main_product_id)
    _assert_main_not_accessory(db, main_product_id)
    normalized = _validate_combo_items(
        db, main_product_id=main_product_id, main_sku_id=main_sku_id, items=items
    )

    combo = ComboProduct(
        combo_no="",
        name="",
        main_product_id=main_product_id,
        main_sku_id=main_sku_id,
        is_enabled=True,
        created_by=staff.id,
    )
    _apply_combo_fields(db, combo, data)
    db.add(combo)
    db.flush()
    for item in normalized:
        db.add(ComboProductItem(combo_id=combo.id, **item))

    write_audit(
        db,
        action="CREATE",
        target_type="combo_product",
        target_id=str(combo.id),
        summary=f"新增组合商品：{combo.name}",
        detail={"combo_no": combo.combo_no, "items": len(normalized)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return combo


def update_combo(
    db: Session,
    *,
    combo_id: int,
    data: dict,
    items: list[dict] | None = None,
    staff: Staff,
    commit: bool = True,
) -> ComboProduct:
    combo = get_combo_or_404(db, combo_id)
    _apply_combo_fields(db, combo, data, exclude_combo_id=combo.id)

    normalized = None
    if items is not None:
        normalized = _validate_combo_items(
            db,
            main_product_id=combo.main_product_id,
            main_sku_id=combo.main_sku_id,
            items=items,
            exclude_combo_id=combo.id,
        )
    if normalized is not None:
        for old in db.scalars(
            select(ComboProductItem).where(ComboProductItem.combo_id == combo.id)
        ):
            db.delete(old)
        db.flush()
        for item in normalized:
            db.add(ComboProductItem(combo_id=combo.id, **item))

    write_audit(
        db,
        action="UPDATE",
        target_type="combo_product",
        target_id=str(combo.id),
        summary=f"修改组合商品：{combo.name}",
        detail={"fields": sorted(data), "items_changed": items is not None},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return combo


def set_combo_enabled(
    db: Session, *, combo_id: int, enabled: bool, commit: bool = True
) -> ComboProduct:
    combo = get_combo_or_404(db, combo_id)
    combo.is_enabled = enabled
    _commit(db, commit)
    return combo


def delete_combo(db: Session, *, combo_id: int, commit: bool = True) -> None:
    combo = get_combo_or_404(db, combo_id)
    for item in db.scalars(
        select(ComboProductItem).where(ComboProductItem.combo_id == combo.id)
    ):
        db.delete(item)
    db.delete(combo)
    _commit(db, commit)


def find_combo_by_product(
    db: Session,
    *,
    product_id: int,
    sku_id: int | None = None,
    company_id: int | None = None,
) -> ComboProduct | None:
    """按主商品与主规格查找启用中的组合商品；未命中统一规格时回退到「不限定规格」的组合。"""
    conditions = [
        ComboProduct.main_product_id == product_id,
        ComboProduct.is_enabled.is_(True),
    ]
    if company_id is not None:
        conditions.append(ComboProduct.company_id == company_id)
    combos = list(db.scalars(select(ComboProduct).where(*conditions)))
    if not combos:
        return None
    for combo in combos:
        if combo.main_sku_id is not None and combo.main_sku_id == sku_id:
            return combo
    for combo in combos:
        if combo.main_sku_id is None:
            return combo
    return None


def expand_combo(
    db: Session, *, product_id: int, sku_id: int | None = None, qty: Any = "1"
) -> list[dict]:
    """展开销售组成项：组合商品按配件逐项扣库存/核算成本，普通商品原样返回。

    返回项 `kind`：`SINGLE` 普通商品；`MAIN` 组合主商品；`ACCESSORY` 组合配件。
    """
    quantity = _to_decimal(qty, "数量")
    if quantity is None or quantity <= 0:
        raise BizException(ErrorCode.BAD_REQUEST, "数量必须大于 0")

    combo = find_combo_by_product(db, product_id=product_id, sku_id=sku_id)
    if combo is None:
        return [
            {
                "combo_id": None,
                "kind": "SINGLE",
                "product_id": product_id,
                "sku_id": sku_id,
                "qty": quantity,
            }
        ]

    result = [
        {
            "combo_id": combo.id,
            "kind": "MAIN",
            "product_id": combo.main_product_id,
            "sku_id": combo.main_sku_id,
            "qty": quantity,
        }
    ]
    items = db.scalars(
        select(ComboProductItem)
        .where(ComboProductItem.combo_id == combo.id)
        .order_by(ComboProductItem.sort_order, ComboProductItem.id)
    )
    for item in items:
        result.append(
            {
                "combo_id": combo.id,
                "kind": "ACCESSORY",
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "qty": item.qty * quantity,
                "sort_order": item.sort_order,
            }
        )
    return result


def expand_combo_page(
    db: Session,
    *,
    combo_id: int,
    qty: Any = "1",
    page: int = 1,
    page_size: int = 20,
    company_id: int | None = None,
) -> dict:
    """按销售展开规则分页预览组合商品，不加载无界组成项。"""
    quantity = _to_decimal(qty, "数量")
    if quantity is None or quantity <= 0:
        raise BizException(ErrorCode.BAD_REQUEST, "数量必须大于 0")

    requested = get_combo_or_404(db, combo_id, company_id=company_id)
    combo = find_combo_by_product(
        db, product_id=requested.main_product_id, sku_id=requested.main_sku_id,
        company_id=company_id,
    )
    offset = (page - 1) * page_size
    if combo is None:
        product_stmt = select(Product).where(Product.id == requested.main_product_id)
        if company_id is not None:
            product_stmt = product_stmt.where(Product.company_id == company_id)
        product = db.scalar(product_stmt)
        sku_stmt = (
            select(ProductSku).where(ProductSku.id == requested.main_sku_id)
            if requested.main_sku_id else None
        )
        if company_id is not None and sku_stmt is not None:
            sku_stmt = sku_stmt.where(ProductSku.company_id == company_id)
        sku = db.scalar(sku_stmt) if sku_stmt is not None else None
        rows = []
        if offset == 0:
            rows.append({
                "combo_id": None, "kind": "SINGLE",
                "product_id": requested.main_product_id,
                "product_name": product.name if product else None,
                "sku_id": requested.main_sku_id,
                "sku_spec_code": sku.spec_code if sku else None,
                "qty": quantity, "sort_order": None,
            })
        total = 1
    else:
        item_conditions = [ComboProductItem.combo_id == combo.id]
        if company_id is not None:
            item_conditions.append(ComboProductItem.company_id == company_id)
        item_count = int(db.scalar(
            select(func.count(ComboProductItem.id)).where(*item_conditions)
        ) or 0)
        total = item_count + 1
        rows = []
        if offset == 0:
            main = db.execute(
                select(Product.name, ProductSku.spec_code)
                .join(ProductSku, and_(
                    ProductSku.id == combo.main_sku_id,
                    ProductSku.product_id == combo.main_product_id,
                    ProductSku.company_id == combo.company_id,
                ), isouter=True)
                .where(Product.id == combo.main_product_id)
                .where(Product.company_id == combo.company_id)
            ).one_or_none()
            rows.append({
                "combo_id": combo.id, "kind": "MAIN",
                "product_id": combo.main_product_id,
                "product_name": main[0] if main else None,
                "sku_id": combo.main_sku_id,
                "sku_spec_code": main[1] if main else None,
                "qty": quantity, "sort_order": None,
            })

        accessory_offset = max(0, offset - 1)
        accessory_limit = page_size - (1 if offset == 0 else 0)
        if accessory_limit > 0:
            items = db.execute(
                select(
                    ComboProductItem, Product.name, ProductSku.spec_code,
                )
                .join(Product, and_(
                    Product.id == ComboProductItem.product_id,
                    Product.company_id == combo.company_id,
                ))
                .outerjoin(ProductSku, and_(
                    ProductSku.id == ComboProductItem.sku_id,
                    ProductSku.product_id == ComboProductItem.product_id,
                    ProductSku.company_id == combo.company_id,
                ))
                .where(*item_conditions)
                .order_by(ComboProductItem.sort_order, ComboProductItem.id)
                .offset(accessory_offset).limit(accessory_limit)
            ).all()
            rows.extend({
                "combo_id": combo.id, "kind": "ACCESSORY",
                "product_id": item.product_id, "product_name": product_name,
                "sku_id": item.sku_id, "sku_spec_code": sku_spec_code,
                "qty": item.qty * quantity, "sort_order": item.sort_order,
            } for item, product_name, sku_spec_code in items)

    return {
        "items": rows, "page": page, "page_size": page_size,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# Excel 导入导出
# --------------------------------------------------------------------------- #


def export_products(db: Session, *, filters: dict | None = None) -> bytes:
    filters = filters or {}
    result = list_products(
        db,
        keyword=filters.get("keyword"),
        category_id=filters.get("category_id"),
        brand_id=filters.get("brand_id"),
        listed=filters.get("listed"),
        is_disabled=filters.get("is_disabled"),
        page=1,
        page_size=200,
    )
    rows = [
        {
            "code": item["code"],
            "name": item["name"],
            "art_no": item["art_no"],
            "category_name": item["category_name"],
            "brand_name": item["brand_name"],
            "unit_name": item["unit_name"],
            "barcode": item["barcode"],
            "online_price": item["online_price"],
            "tag_price": item["tag_price"],
            "cost_price": item["cost_price"],
            "warn_stock": item["warn_stock"],
            "description": item["description"],
        }
        for item in result["items"]
    ]
    return export_data(PRODUCT_TEMPLATE, rows)


def _lookup_by_name(
    db: Session, model, name: str | None, label: str
) -> int | None:
    if not name:
        return None
    row = db.scalar(select(model).where(model.name == name))
    if row is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不存在：{name}")
    return row.id


def _validate_import_row(db: Session, data: dict) -> None:
    """只校验不落库，保证导入为「全部成功或全部不写入」。"""
    _require_text(data.get("code"), "商品编码", 64)
    _require_text(data.get("name"), "商品名称", 128)
    for field, label in (
        ("online_price", "网上售价"),
        ("tag_price", "吊牌价"),
        ("cost_price", "成本价"),
    ):
        _to_money(data.get(field), label)
    warn_stock = data.get("warn_stock") or 0
    if int(warn_stock) < 0:
        raise BizException(ErrorCode.BAD_REQUEST, "警戒库存不能为负数")
    barcode = data.get("barcode")
    if barcode:
        if db.scalar(select(Product.id).where(Product.barcode == barcode)) is not None:
            raise BizException(ErrorCode.CONFLICT, f"商品条码已存在：{barcode}")
        if db.scalar(select(ProductSku.id).where(ProductSku.barcode == barcode)) is not None:
            raise BizException(ErrorCode.CONFLICT, f"商品条码已被规格占用：{barcode}")


def import_products(
    db: Session, *, file_bytes: bytes, staff: Staff, commit: bool = True
) -> dict:
    parsed: ParseResult = parse_data(file_bytes, PRODUCT_TEMPLATE)
    errors = list(parsed.errors)
    if errors and not parsed.rows:
        return {"created": 0, "updated": 0, "errors": errors}

    prepared: list[dict] = []
    seen_codes: set[str] = set()
    for index, row in enumerate(parsed.rows, start=2):
        code = str(row.get("code") or "").strip()
        if code in seen_codes:
            errors.append({"row": index, "field": "code", "message": f"文件内商品编码重复：{code}"})
            continue
        seen_codes.add(code)

        existing = db.scalar(select(Product).where(Product.code == code))
        if existing is not None:
            errors.append(
                {"row": index, "field": "code", "message": f"商品编码已存在：{code}，如需修改请使用编辑功能"}
            )
            continue

        try:
            category_id = _lookup_by_name(db, Category, row.get("category_name"), "分类")
            brand_id = _lookup_by_name(db, Brand, row.get("brand_name"), "品牌")
            unit_id = _lookup_by_name(db, Unit, row.get("unit_name"), "计量单位")
            data = {
                "code": code,
                "name": row.get("name"),
                "art_no": row.get("art_no"),
                "category_id": category_id,
                "brand_id": brand_id,
                "main_unit_id": unit_id,
                "barcode": row.get("barcode"),
                "online_price": row.get("online_price"),
                "tag_price": row.get("tag_price"),
                "cost_price": row.get("cost_price"),
                "warn_stock": row.get("warn_stock") or 0,
                "description": row.get("description"),
            }
            _validate_import_row(db, data)
            prepared.append(data)
        except BizException as exc:
            errors.append({"row": index, "field": "", "message": exc.message})

    if errors:
        return {"created": 0, "updated": 0, "errors": errors}

    for data in prepared:
        create_product(db, data=data, staff=staff, commit=False)
    created = len(prepared)

    write_audit(
        db,
        action="IMPORT",
        target_type="product",
        target_id="batch",
        summary=f"导入商品：{created} 条",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {"created": created, "updated": 0, "errors": []}


# --------------------------------------------------------------------------- #
# 商品设置：分类 / 品牌 / 计量单位 / 单位组 / 规格类型 / 规格明细
# --------------------------------------------------------------------------- #


def list_categories(db: Session) -> list[dict]:
    rows = db.scalars(select(Category).order_by(Category.sort_order, Category.id))
    return [
        {
            "id": row.id,
            "name": row.name,
            "parent_id": row.parent_id,
            "sort_order": row.sort_order,
        }
        for row in rows
    ]


def create_category(db: Session, *, name: str, parent_id: int | None = None,
                    sort_order: int = 0, commit: bool = True) -> Category:
    name = _require_text(name, "类别名称", 64)
    if parent_id is not None and db.get(Category, parent_id) is None:
        raise BizException(ErrorCode.BAD_REQUEST, "所属类别不存在")
    category = Category(name=name, parent_id=parent_id, sort_order=sort_order)
    db.add(category)
    _commit(db, commit)
    return category


def update_category(db: Session, *, category_id: int, data: dict, commit: bool = True) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise BizException(ErrorCode.NOT_FOUND, "商品分类不存在")
    if "name" in data:
        category.name = _require_text(data["name"], "类别名称", 64)
    if "parent_id" in data:
        parent_id = data["parent_id"]
        if parent_id == category_id:
            raise BizException(ErrorCode.BAD_REQUEST, "所属类别不能是自身")
        if parent_id is not None and db.get(Category, parent_id) is None:
            raise BizException(ErrorCode.BAD_REQUEST, "所属类别不存在")
        category.parent_id = parent_id
    if "sort_order" in data:
        category.sort_order = int(data["sort_order"] or 0)
    _commit(db, commit)
    return category


def delete_category(db: Session, *, category_id: int, commit: bool = True) -> None:
    category = db.get(Category, category_id)
    if category is None:
        raise BizException(ErrorCode.NOT_FOUND, "商品分类不存在")
    if db.scalar(select(Category.id).where(Category.parent_id == category_id)) is not None:
        raise BizException(ErrorCode.CONFLICT, "存在子类别，不能删除")
    if db.scalar(select(Product.id).where(Product.category_id == category_id)) is not None:
        raise BizException(ErrorCode.CONFLICT, "分类已被商品使用，不能删除")
    db.delete(category)
    _commit(db, commit)


def list_brands(db: Session) -> list[dict]:
    rows = db.scalars(select(Brand).order_by(Brand.id))
    return [{"id": row.id, "name": row.name, "image": row.image} for row in rows]


def create_brand(db: Session, *, name: str, image: str | None = None,
                 commit: bool = True) -> Brand:
    brand = Brand(name=_require_text(name, "品牌名称", 64), image=image)
    db.add(brand)
    _commit(db, commit)
    return brand


def update_brand(db: Session, *, brand_id: int, data: dict, commit: bool = True) -> Brand:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise BizException(ErrorCode.NOT_FOUND, "品牌不存在")
    if "name" in data:
        brand.name = _require_text(data["name"], "品牌名称", 64)
    if "image" in data:
        brand.image = data["image"]
    _commit(db, commit)
    return brand


def delete_brand(db: Session, *, brand_id: int, commit: bool = True) -> None:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise BizException(ErrorCode.NOT_FOUND, "品牌不存在")
    if db.scalar(select(Product.id).where(Product.brand_id == brand_id)) is not None:
        raise BizException(ErrorCode.CONFLICT, "品牌已被商品使用，不能删除")
    db.delete(brand)
    _commit(db, commit)


def list_units(db: Session) -> list[dict]:
    rows = db.scalars(select(Unit).order_by(Unit.id))
    return [{"id": row.id, "name": row.name, "code": row.code} for row in rows]


def create_unit(db: Session, *, name: str, code: str | None = None,
                commit: bool = True) -> Unit:
    unit = Unit(name=_require_text(name, "计量单位", 32), code=code)
    db.add(unit)
    _commit(db, commit)
    return unit


def update_unit(db: Session, *, unit_id: int, data: dict, commit: bool = True) -> Unit:
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise BizException(ErrorCode.NOT_FOUND, "计量单位不存在")
    if "name" in data:
        unit.name = _require_text(data["name"], "计量单位", 32)
    if "code" in data:
        unit.code = data["code"]
    _commit(db, commit)
    return unit


def delete_unit(db: Session, *, unit_id: int, commit: bool = True) -> None:
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise BizException(ErrorCode.NOT_FOUND, "计量单位不存在")
    if db.scalar(select(Product.id).where(Product.main_unit_id == unit_id)) is not None:
        raise BizException(ErrorCode.CONFLICT, "计量单位已被商品使用，不能删除")
    if db.scalar(select(UnitGroup.id).where(UnitGroup.main_unit_id == unit_id)) is not None:
        raise BizException(ErrorCode.CONFLICT, "计量单位被计量单位组引用，不能删除")
    if db.scalar(select(UnitGroupItem.id).where(UnitGroupItem.unit_id == unit_id)) is not None:
        raise BizException(ErrorCode.CONFLICT, "计量单位被计量单位组引用，不能删除")
    db.delete(unit)
    _commit(db, commit)


def list_unit_groups(db: Session) -> list[dict]:
    rows = db.scalars(select(UnitGroup).order_by(UnitGroup.id))
    result = []
    for group in rows:
        items = db.scalars(
            select(UnitGroupItem).where(UnitGroupItem.unit_group_id == group.id)
        )
        result.append(
            {
                "id": group.id,
                "name": group.name,
                "main_unit_id": group.main_unit_id,
                "items": [
                    {
                        "unit_id": item.unit_id,
                        "conversion_rate": item.conversion_rate,
                    }
                    for item in items
                ],
            }
        )
    return result


def create_unit_group(
    db: Session,
    *,
    name: str,
    main_unit_id: int,
    items: list[dict] | None = None,
    commit: bool = True,
) -> UnitGroup:
    if db.get(Unit, main_unit_id) is None:
        raise BizException(ErrorCode.BAD_REQUEST, "主计量单位不存在")
    normalized_items = _validate_unit_group_items(db, items or [])
    group = UnitGroup(name=_require_text(name, "计量单位组名称", 64), main_unit_id=main_unit_id)
    db.add(group)
    db.flush()
    _replace_unit_group_items(db, group.id, normalized_items)
    _commit(db, commit)
    return group


def update_unit_group(
    db: Session,
    *,
    group_id: int,
    data: dict,
    commit: bool = True,
) -> UnitGroup:
    group = db.get(UnitGroup, group_id)
    if group is None:
        raise BizException(ErrorCode.NOT_FOUND, "计量单位组不存在")

    values: dict[str, Any] = {}
    if "name" in data:
        values["name"] = _require_text(data["name"], "计量单位组名称", 64)
    if "main_unit_id" in data:
        main_unit_id = int(data["main_unit_id"])
        if db.get(Unit, main_unit_id) is None:
            raise BizException(ErrorCode.BAD_REQUEST, "主计量单位不存在")
        values["main_unit_id"] = main_unit_id
    if "items" in data:
        values["items"] = _validate_unit_group_items(db, data["items"] or [])

    for field, value in values.items():
        if field == "items":
            continue
        setattr(group, field, value)
    if "items" in values:
        _replace_unit_group_items(db, group.id, values["items"])
    _commit(db, commit)
    return group


def _validate_unit_group_items(db: Session, items: list[dict]) -> list[tuple[int, Decimal]]:
    """校验单位组明细并返回 (unit_id, 换算率)，保证校验通过后再落库。"""
    normalized: list[tuple[int, Decimal]] = []
    seen: set[int] = set()
    for item in items:
        try:
            unit_id = int(item["unit_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BizException(ErrorCode.BAD_REQUEST, "计量单位明细格式错误") from exc
        if unit_id in seen:
            raise BizException(ErrorCode.BAD_REQUEST, "计量单位组内单位重复")
        seen.add(unit_id)
        if db.get(Unit, unit_id) is None:
            raise BizException(ErrorCode.BAD_REQUEST, f"计量单位不存在：{unit_id}")
        rate = _to_decimal(item.get("conversion_rate"), "换算率")
        if rate is None or rate <= 0:
            raise BizException(ErrorCode.BAD_REQUEST, "换算率必须大于 0")
        normalized.append((unit_id, rate))
    return normalized


def _replace_unit_group_items(
    db: Session, group_id: int, items: list[tuple[int, Decimal]]
) -> None:
    for old in db.scalars(select(UnitGroupItem).where(UnitGroupItem.unit_group_id == group_id)):
        db.delete(old)
    db.flush()

    for unit_id, rate in items:
        db.add(
            UnitGroupItem(
                unit_group_id=group_id, unit_id=unit_id, conversion_rate=rate
            )
        )
    db.flush()


def delete_unit_group(db: Session, *, group_id: int, commit: bool = True) -> None:
    group = db.get(UnitGroup, group_id)
    if group is None:
        raise BizException(ErrorCode.NOT_FOUND, "计量单位组不存在")
    if db.scalar(select(Product.id).where(Product.unit_group_id == group_id)) is not None:
        raise BizException(ErrorCode.CONFLICT, "计量单位组已被商品使用，不能删除")
    for item in db.scalars(select(UnitGroupItem).where(UnitGroupItem.unit_group_id == group_id)):
        db.delete(item)
    db.delete(group)
    _commit(db, commit)


def list_spec_types(db: Session) -> list[dict]:
    rows = db.scalars(select(SpecType).order_by(SpecType.id))
    return [
        {"id": row.id, "name": row.name, "category_id": row.category_id} for row in rows
    ]


def create_spec_type(
    db: Session, *, name: str, category_id: int | None = None, commit: bool = True
) -> SpecType:
    if category_id is not None and db.get(Category, category_id) is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联分类不存在")
    spec_type = SpecType(name=_require_text(name, "规格类型名称", 64), category_id=category_id)
    db.add(spec_type)
    _commit(db, commit)
    return spec_type


def update_spec_type(
    db: Session, *, spec_type_id: int, data: dict, commit: bool = True
) -> SpecType:
    spec_type = db.get(SpecType, spec_type_id)
    if spec_type is None:
        raise BizException(ErrorCode.NOT_FOUND, "规格类型不存在")
    if "name" in data:
        spec_type.name = _require_text(data["name"], "规格类型名称", 64)
    if "category_id" in data:
        category_id = data["category_id"]
        if category_id is not None and db.get(Category, category_id) is None:
            raise BizException(ErrorCode.BAD_REQUEST, "关联分类不存在")
        spec_type.category_id = category_id
    _commit(db, commit)
    return spec_type


def delete_spec_type(db: Session, *, spec_type_id: int, commit: bool = True) -> None:
    spec_type = db.get(SpecType, spec_type_id)
    if spec_type is None:
        raise BizException(ErrorCode.NOT_FOUND, "规格类型不存在")
    for value in db.scalars(select(SpecValue).where(SpecValue.spec_type_id == spec_type_id)):
        db.delete(value)
    db.delete(spec_type)
    _commit(db, commit)


def list_spec_values(db: Session, spec_type_id: int) -> list[dict]:
    rows = db.scalars(
        select(SpecValue)
        .where(SpecValue.spec_type_id == spec_type_id)
        .order_by(SpecValue.sort_order, SpecValue.id)
    )
    return [
        {
            "id": row.id,
            "spec_type_id": row.spec_type_id,
            "name": row.name,
            "code": row.code,
            "sort_order": row.sort_order,
        }
        for row in rows
    ]


def _validate_spec_value_code(
    db: Session,
    spec_type_id: int,
    code: str | None,
    *,
    exclude_value_id: int | None = None,
) -> None:
    if not code:
        return
    stmt = select(SpecValue.id).where(
        SpecValue.spec_type_id == spec_type_id, SpecValue.code == code
    )
    if exclude_value_id is not None:
        stmt = stmt.where(SpecValue.id != exclude_value_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"规格明细编码已存在：{code}")


def create_spec_value(
    db: Session,
    *,
    spec_type_id: int,
    name: str,
    code: str | None = None,
    sort_order: int = 0,
    commit: bool = True,
) -> SpecValue:
    if db.get(SpecType, spec_type_id) is None:
        raise BizException(ErrorCode.BAD_REQUEST, "规格类型不存在")
    _validate_spec_value_code(db, spec_type_id, code)
    value = SpecValue(
        spec_type_id=spec_type_id,
        name=_require_text(name, "规格明细名称", 64),
        code=code,
        sort_order=sort_order,
    )
    db.add(value)
    _commit(db, commit)
    return value


def update_spec_value(
    db: Session, *, spec_value_id: int, data: dict, commit: bool = True
) -> SpecValue:
    value = db.get(SpecValue, spec_value_id)
    if value is None:
        raise BizException(ErrorCode.NOT_FOUND, "规格明细不存在")
    if "name" in data:
        value.name = _require_text(data["name"], "规格明细名称", 64)
    if "code" in data:
        _validate_spec_value_code(
            db, value.spec_type_id, data["code"], exclude_value_id=value.id
        )
        value.code = data["code"]
    if "sort_order" in data:
        value.sort_order = int(data["sort_order"] or 0)
    _commit(db, commit)
    return value


def delete_spec_value(db: Session, *, spec_value_id: int, commit: bool = True) -> None:
    value = db.get(SpecValue, spec_value_id)
    if value is None:
        raise BizException(ErrorCode.NOT_FOUND, "规格明细不存在")
    db.delete(value)
    _commit(db, commit)
