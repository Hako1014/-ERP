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
from app.services import product_service

router = APIRouter(tags=["商品管理"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

read_product = require_permission("module:product")
manage_product = require_permission("product:manage")
read_settings = require_permission("module:product", "module:settings")
manage_settings = require_permission("settings:manage")


class SkuRequest(BaseModel):
    spec_code: str = Field(..., min_length=1, max_length=64)
    spec_name: str | None = Field(default=None, max_length=128)
    barcode: str | None = Field(default=None, max_length=64)
    tag_price: Decimal | None = None
    sale_price: Decimal | None = None
    cost_price: Decimal | None = None
    min_price: Decimal | None = None
    image: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class SkuUpdateRequest(BaseModel):
    spec_code: str | None = Field(default=None, min_length=1, max_length=64)
    spec_name: str | None = Field(default=None, max_length=128)
    barcode: str | None = Field(default=None, max_length=64)
    tag_price: Decimal | None = None
    sale_price: Decimal | None = None
    cost_price: Decimal | None = None
    min_price: Decimal | None = None
    image: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class ProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    code: str = Field(..., min_length=1, max_length=64)
    art_no: str | None = Field(default=None, max_length=64)
    category_id: int | None = None
    brand_id: int | None = None
    default_supplier_id: int | None = None
    measure_type: str = "SINGLE"
    unit_group_id: int | None = None
    main_unit_id: int | None = None
    online_price: Decimal | None = None
    tag_price: Decimal | None = None
    min_price: Decimal | None = None
    cost_price: Decimal | None = None
    weight: Decimal | None = None
    volume: Decimal | None = None
    min_stock: int = 0
    max_stock: int = 0
    warn_stock: int = 0
    tax_rate: Decimal | None = None
    tax_code: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    is_gift: bool = False
    use_float_cost: bool = False
    is_disabled: bool = False
    description: str | None = None
    image: str | None = Field(default=None, max_length=255)
    skus: list[SkuRequest] = Field(default_factory=list)


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    code: str | None = Field(default=None, min_length=1, max_length=64)
    art_no: str | None = Field(default=None, max_length=64)
    category_id: int | None = None
    brand_id: int | None = None
    default_supplier_id: int | None = None
    measure_type: str | None = None
    unit_group_id: int | None = None
    main_unit_id: int | None = None
    online_price: Decimal | None = None
    tag_price: Decimal | None = None
    min_price: Decimal | None = None
    cost_price: Decimal | None = None
    weight: Decimal | None = None
    volume: Decimal | None = None
    min_stock: int | None = None
    max_stock: int | None = None
    warn_stock: int | None = None
    tax_rate: Decimal | None = None
    tax_code: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    is_gift: bool | None = None
    use_float_cost: bool | None = None
    is_disabled: bool | None = None
    description: str | None = None
    image: str | None = Field(default=None, max_length=255)


class BatchIdsRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1)


class ListingRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1)
    listed: bool


class CategoryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    parent_id: int | None = None
    sort_order: int = 0


class CategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = None
    sort_order: int | None = None


class BrandRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    image: str | None = Field(default=None, max_length=255)


class BrandUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    image: str | None = Field(default=None, max_length=255)


class UnitRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    code: str | None = Field(default=None, max_length=32)


class UnitUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=32)
    code: str | None = Field(default=None, max_length=32)


class UnitGroupItemRequest(BaseModel):
    unit_id: int
    conversion_rate: Decimal


class UnitGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    main_unit_id: int
    items: list[UnitGroupItemRequest] = Field(default_factory=list)


class UnitGroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    main_unit_id: int | None = None
    items: list[UnitGroupItemRequest] | None = None


class SpecTypeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    category_id: int | None = None


class SpecTypeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    category_id: int | None = None


class SpecValueRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    code: str | None = Field(default=None, max_length=64)
    sort_order: int = 0


class SpecValueUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    code: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None


class ComboItemRequest(BaseModel):
    product_id: int
    sku_id: int | None = None
    qty: Decimal
    sort_order: int = 0


class ComboCreateRequest(BaseModel):
    combo_no: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    main_product_id: int
    main_sku_id: int | None = None
    is_enabled: bool = True
    remark: str | None = Field(default=None, max_length=255)
    items: list[ComboItemRequest] = Field(..., min_length=1)


class ComboUpdateRequest(BaseModel):
    combo_no: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    main_product_id: int | None = None
    main_sku_id: int | None = None
    is_enabled: bool | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[ComboItemRequest] | None = None


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- #
# 商品资料
# --------------------------------------------------------------------------- #


@router.get("/products", response_model=Result[dict])
def list_products(
    keyword: str | None = None,
    category_id: int | None = None,
    brand_id: int | None = None,
    listed: bool | None = None,
    is_disabled: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_product),
    db: Session = Depends(get_db),
):
    return Result.ok(
        product_service.list_products(
            db,
            keyword=keyword,
            category_id=category_id,
            brand_id=brand_id,
            listed=listed,
            is_disabled=is_disabled,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/products/export")
def export_products(
    keyword: str | None = None,
    category_id: int | None = None,
    brand_id: int | None = None,
    listed: bool | None = None,
    is_disabled: bool | None = None,
    staff: Staff = Depends(read_product),
    db: Session = Depends(get_db),
):
    data = product_service.export_products(
        db,
        filters={
            "keyword": keyword,
            "category_id": category_id,
            "brand_id": brand_id,
            "listed": listed,
            "is_disabled": is_disabled,
        },
    )
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="products.xlsx"'},
    )


@router.post("/products/import", response_model=Result[dict])
async def import_products(
    file: UploadFile = File(...),
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    data = await file.read()
    limit = get_settings().max_upload_size_mb * 1024 * 1024
    if len(data) > limit:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"文件大小不能超过 {limit // (1024 * 1024)}MB"
        )
    return Result.ok(
        product_service.import_products(db, file_bytes=data, staff=staff)
    )


@router.post("/products", response_model=Result[dict])
def create_product(
    req: ProductCreateRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    skus = payload.pop("skus", [])
    product = product_service.create_product(
        db, data=payload, staff=staff, skus=skus
    )
    return Result.ok(
        product_service.product_dict(db, product, with_skus=True), message="商品已创建"
    )


@router.post("/products/batch-delete", response_model=Result[dict])
def batch_delete_products(
    req: BatchIdsRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    return Result.ok(product_service.delete_products(db, product_ids=req.ids, staff=staff))


@router.post("/products/listing", response_model=Result[dict])
def batch_listing(
    req: ListingRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    count = product_service.set_listing(
        db, product_ids=req.ids, listed=req.listed, staff=staff
    )
    return Result.ok({"count": count}, message="操作成功")


@router.get("/products/{product_id}", response_model=Result[dict])
def get_product(
    product_id: int,
    staff: Staff = Depends(read_product),
    db: Session = Depends(get_db),
):
    product = product_service.get_product_or_404(db, product_id)
    return Result.ok(product_service.product_dict(db, product, with_skus=True))


@router.put("/products/{product_id}", response_model=Result[dict])
def update_product(
    product_id: int,
    req: ProductUpdateRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    product = product_service.update_product(
        db, product_id=product_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        product_service.product_dict(db, product, with_skus=True), message="商品已更新"
    )


@router.delete("/products/{product_id}", response_model=Result[dict])
def delete_product(
    product_id: int,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    return Result.ok(product_service.delete_products(db, product_ids=[product_id], staff=staff))


@router.post("/products/{product_id}/disable", response_model=Result[dict])
def disable_product(
    product_id: int,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    product = product_service.set_disabled(
        db, product_id=product_id, disabled=True, staff=staff
    )
    return Result.ok(product_service.product_dict(db, product), message="商品已禁用")


@router.post("/products/{product_id}/enable", response_model=Result[dict])
def enable_product(
    product_id: int,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    product = product_service.set_disabled(
        db, product_id=product_id, disabled=False, staff=staff
    )
    return Result.ok(product_service.product_dict(db, product), message="商品已启用")


# --------------------------------------------------------------------------- #
# 商品规格
# --------------------------------------------------------------------------- #


@router.get("/products/{product_id}/skus", response_model=Result[list[dict]])
def list_skus(
    product_id: int,
    staff: Staff = Depends(read_product),
    db: Session = Depends(get_db),
):
    product_service.get_product_or_404(db, product_id)
    return Result.ok([product_service.sku_dict(s) for s in product_service.list_skus(db, product_id)])


@router.post("/products/{product_id}/skus", response_model=Result[dict])
def create_sku(
    product_id: int,
    req: SkuRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    sku = product_service.create_sku(db, product_id=product_id, data=_payload(req))
    return Result.ok(product_service.sku_dict(sku), message="规格已创建")


@router.put("/product-skus/{sku_id}", response_model=Result[dict])
def update_sku(
    sku_id: int,
    req: SkuUpdateRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    sku = product_service.update_sku(db, sku_id=sku_id, data=_payload(req))
    return Result.ok(product_service.sku_dict(sku), message="规格已更新")


@router.delete("/product-skus/{sku_id}", response_model=Result[dict])
def delete_sku(
    sku_id: int,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    product_service.delete_sku(db, sku_id=sku_id)
    return Result.ok(message="规格已删除")


# --------------------------------------------------------------------------- #
# 组合商品
# --------------------------------------------------------------------------- #


@router.get("/combo-products", response_model=Result[dict])
def list_combos(
    keyword: str | None = None,
    is_enabled: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_product),
    db: Session = Depends(get_db),
):
    return Result.ok(
        product_service.list_combos(
            db, keyword=keyword, is_enabled=is_enabled, page=page, page_size=page_size
        )
    )


@router.post("/combo-products", response_model=Result[dict])
def create_combo(
    req: ComboCreateRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    items = payload.pop("items", [])
    combo = product_service.create_combo(db, data=payload, items=items, staff=staff)
    return Result.ok(
        product_service.combo_dict(db, combo), message="组合商品已创建"
    )


@router.get("/combo-products/{combo_id}", response_model=Result[dict])
def get_combo(
    combo_id: int,
    staff: Staff = Depends(read_product),
    db: Session = Depends(get_db),
):
    combo = product_service.get_combo_or_404(db, combo_id)
    return Result.ok(product_service.combo_dict(db, combo))


@router.get("/combo-products/{combo_id}/expand", response_model=Result[list[dict]])
def expand_combo(
    combo_id: int,
    qty: Decimal = Query(default=Decimal("1"), gt=0),
    staff: Staff = Depends(read_product),
    db: Session = Depends(get_db),
):
    combo = product_service.get_combo_or_404(db, combo_id)
    return Result.ok(
        product_service.expand_combo(
            db,
            product_id=combo.main_product_id,
            sku_id=combo.main_sku_id,
            qty=qty,
        )
    )


@router.put("/combo-products/{combo_id}", response_model=Result[dict])
def update_combo(
    combo_id: int,
    req: ComboUpdateRequest,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    items = payload.pop("items", None)
    combo = product_service.update_combo(
        db, combo_id=combo_id, data=payload, items=items, staff=staff
    )
    return Result.ok(
        product_service.combo_dict(db, combo), message="组合商品已更新"
    )


@router.delete("/combo-products/{combo_id}", response_model=Result[dict])
def delete_combo(
    combo_id: int,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    product_service.delete_combo(db, combo_id=combo_id)
    return Result.ok(message="组合商品已删除")


@router.post("/combo-products/{combo_id}/enable", response_model=Result[dict])
def enable_combo(
    combo_id: int,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    product_service.set_combo_enabled(db, combo_id=combo_id, enabled=True)
    return Result.ok(message="组合商品已启用")


@router.post("/combo-products/{combo_id}/disable", response_model=Result[dict])
def disable_combo(
    combo_id: int,
    staff: Staff = Depends(manage_product),
    db: Session = Depends(get_db),
):
    product_service.set_combo_enabled(db, combo_id=combo_id, enabled=False)
    return Result.ok(message="组合商品已停用")


# --------------------------------------------------------------------------- #
# 商品设置：分类 / 品牌 / 计量单位 / 单位组 / 规格类型 / 规格明细
# --------------------------------------------------------------------------- #


@router.get("/product-settings/categories", response_model=Result[list[dict]])
def list_categories(
    staff: Staff = Depends(read_settings), db: Session = Depends(get_db)
):
    return Result.ok(product_service.list_categories(db))


@router.post("/product-settings/categories", response_model=Result[dict])
def create_category(
    req: CategoryRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    category = product_service.create_category(
        db, name=req.name, parent_id=req.parent_id, sort_order=req.sort_order
    )
    return Result.ok(
        {"id": category.id, "name": category.name}, message="分类已创建"
    )


@router.put("/product-settings/categories/{category_id}", response_model=Result[dict])
def update_category(
    category_id: int,
    req: CategoryUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    category = product_service.update_category(
        db, category_id=category_id, data=_payload(req)
    )
    return Result.ok({"id": category.id, "name": category.name}, message="分类已更新")


@router.delete("/product-settings/categories/{category_id}", response_model=Result[dict])
def delete_category(
    category_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    product_service.delete_category(db, category_id=category_id)
    return Result.ok(message="分类已删除")


@router.get("/product-settings/brands", response_model=Result[list[dict]])
def list_brands(staff: Staff = Depends(read_settings), db: Session = Depends(get_db)):
    return Result.ok(product_service.list_brands(db))


@router.post("/product-settings/brands", response_model=Result[dict])
def create_brand(
    req: BrandRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    brand = product_service.create_brand(db, name=req.name, image=req.image)
    return Result.ok({"id": brand.id, "name": brand.name}, message="品牌已创建")


@router.put("/product-settings/brands/{brand_id}", response_model=Result[dict])
def update_brand(
    brand_id: int,
    req: BrandUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    brand = product_service.update_brand(db, brand_id=brand_id, data=_payload(req))
    return Result.ok({"id": brand.id, "name": brand.name}, message="品牌已更新")


@router.delete("/product-settings/brands/{brand_id}", response_model=Result[dict])
def delete_brand(
    brand_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    product_service.delete_brand(db, brand_id=brand_id)
    return Result.ok(message="品牌已删除")


@router.get("/product-settings/units", response_model=Result[list[dict]])
def list_units(staff: Staff = Depends(read_settings), db: Session = Depends(get_db)):
    return Result.ok(product_service.list_units(db))


@router.post("/product-settings/units", response_model=Result[dict])
def create_unit(
    req: UnitRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    unit = product_service.create_unit(db, name=req.name, code=req.code)
    return Result.ok({"id": unit.id, "name": unit.name}, message="计量单位已创建")


@router.put("/product-settings/units/{unit_id}", response_model=Result[dict])
def update_unit(
    unit_id: int,
    req: UnitUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    unit = product_service.update_unit(db, unit_id=unit_id, data=_payload(req))
    return Result.ok({"id": unit.id, "name": unit.name}, message="计量单位已更新")


@router.delete("/product-settings/units/{unit_id}", response_model=Result[dict])
def delete_unit(
    unit_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    product_service.delete_unit(db, unit_id=unit_id)
    return Result.ok(message="计量单位已删除")


@router.get("/product-settings/unit-groups", response_model=Result[list[dict]])
def list_unit_groups(
    staff: Staff = Depends(read_settings), db: Session = Depends(get_db)
):
    return Result.ok(product_service.list_unit_groups(db))


@router.post("/product-settings/unit-groups", response_model=Result[dict])
def create_unit_group(
    req: UnitGroupRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    group = product_service.create_unit_group(
        db,
        name=req.name,
        main_unit_id=req.main_unit_id,
        items=[item.model_dump() for item in req.items],
    )
    return Result.ok({"id": group.id, "name": group.name}, message="计量单位组已创建")


@router.put("/product-settings/unit-groups/{group_id}", response_model=Result[dict])
def update_unit_group(
    group_id: int,
    req: UnitGroupUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    payload = _payload(req)
    group = product_service.update_unit_group(db, group_id=group_id, data=payload)
    return Result.ok({"id": group.id, "name": group.name}, message="计量单位组已更新")


@router.delete("/product-settings/unit-groups/{group_id}", response_model=Result[dict])
def delete_unit_group(
    group_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    product_service.delete_unit_group(db, group_id=group_id)
    return Result.ok(message="计量单位组已删除")


@router.get("/product-settings/spec-types", response_model=Result[list[dict]])
def list_spec_types(
    staff: Staff = Depends(read_settings), db: Session = Depends(get_db)
):
    return Result.ok(product_service.list_spec_types(db))


@router.post("/product-settings/spec-types", response_model=Result[dict])
def create_spec_type(
    req: SpecTypeRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    spec_type = product_service.create_spec_type(
        db, name=req.name, category_id=req.category_id
    )
    return Result.ok({"id": spec_type.id, "name": spec_type.name}, message="规格类型已创建")


@router.put("/product-settings/spec-types/{spec_type_id}", response_model=Result[dict])
def update_spec_type(
    spec_type_id: int,
    req: SpecTypeUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    spec_type = product_service.update_spec_type(
        db, spec_type_id=spec_type_id, data=_payload(req)
    )
    return Result.ok({"id": spec_type.id, "name": spec_type.name}, message="规格类型已更新")


@router.delete("/product-settings/spec-types/{spec_type_id}", response_model=Result[dict])
def delete_spec_type(
    spec_type_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    product_service.delete_spec_type(db, spec_type_id=spec_type_id)
    return Result.ok(message="规格类型已删除")


@router.get(
    "/product-settings/spec-types/{spec_type_id}/values",
    response_model=Result[list[dict]],
)
def list_spec_values(
    spec_type_id: int,
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    return Result.ok(product_service.list_spec_values(db, spec_type_id))


@router.post(
    "/product-settings/spec-types/{spec_type_id}/values", response_model=Result[dict]
)
def create_spec_value(
    spec_type_id: int,
    req: SpecValueRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    value = product_service.create_spec_value(
        db,
        spec_type_id=spec_type_id,
        name=req.name,
        code=req.code,
        sort_order=req.sort_order,
    )
    return Result.ok({"id": value.id, "name": value.name}, message="规格明细已创建")


@router.put("/product-settings/spec-values/{spec_value_id}", response_model=Result[dict])
def update_spec_value(
    spec_value_id: int,
    req: SpecValueUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    value = product_service.update_spec_value(
        db, spec_value_id=spec_value_id, data=_payload(req)
    )
    return Result.ok({"id": value.id, "name": value.name}, message="规格明细已更新")


@router.delete("/product-settings/spec-values/{spec_value_id}", response_model=Result[dict])
def delete_spec_value(
    spec_value_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    product_service.delete_spec_value(db, spec_value_id=spec_value_id)
    return Result.ok(message="规格明细已删除")
