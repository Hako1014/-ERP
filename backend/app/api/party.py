from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import party_service

router = APIRouter(tags=["往来单位"])

read_party = require_permission("module:settings", "module:purchase", "module:order")
manage_party = require_permission("settings:manage")


class SupplierCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    fax: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=255)
    settlement_type: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class SupplierUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    fax: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=255)
    settlement_type: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class CustomerCreateRequest(SupplierCreateRequest):
    pass


class CustomerUpdateRequest(SupplierUpdateRequest):
    pass


class AccountRequest(BaseModel):
    bank: str | None = Field(default=None, max_length=64)
    branch: str | None = Field(default=None, max_length=64)
    bank_no: str | None = Field(default=None, max_length=64)
    account_no: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    is_default: bool = False


class AccountUpdateRequest(BaseModel):
    bank: str | None = Field(default=None, max_length=64)
    branch: str | None = Field(default=None, max_length=64)
    bank_no: str | None = Field(default=None, max_length=64)
    account_no: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    is_default: bool | None = None


class SupplierPriceRequest(BaseModel):
    product_id: int
    sku_id: int | None = None
    price: Decimal
    status: str = Field(default="ACTIVE", max_length=16)


class SupplierPriceUpdateRequest(BaseModel):
    product_id: int | None = None
    sku_id: int | None = None
    price: Decimal | None = None
    status: str | None = Field(default=None, max_length=16)


class PriceStatusRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=16)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- #
# 供应商
# --------------------------------------------------------------------------- #


@router.get("/suppliers", response_model=Result[dict])
def list_suppliers(
    keyword: str | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_party),
    db: Session = Depends(get_db),
):
    return Result.ok(
        party_service.list_suppliers(
            db, keyword=keyword, is_active=is_active, page=page, page_size=page_size
        )
    )


@router.post("/suppliers", response_model=Result[dict])
def create_supplier(
    req: SupplierCreateRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    supplier = party_service.create_supplier(db, data=_payload(req), staff=staff)
    return Result.ok(
        party_service.supplier_dict(db, supplier, with_accounts=True), message="供应商已创建"
    )


@router.get("/suppliers/{supplier_id}", response_model=Result[dict])
def get_supplier(
    supplier_id: int,
    staff: Staff = Depends(read_party),
    db: Session = Depends(get_db),
):
    supplier = party_service.get_supplier_or_404(db, supplier_id)
    return Result.ok(party_service.supplier_dict(db, supplier, with_accounts=True))


@router.put("/suppliers/{supplier_id}", response_model=Result[dict])
def update_supplier(
    supplier_id: int,
    req: SupplierUpdateRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    supplier = party_service.update_supplier(
        db, supplier_id=supplier_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        party_service.supplier_dict(db, supplier, with_accounts=True), message="供应商已更新"
    )


@router.delete("/suppliers/{supplier_id}", response_model=Result[dict])
def delete_supplier(
    supplier_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.delete_supplier(db, supplier_id=supplier_id, staff=staff)
    return Result.ok(message="供应商已删除")


@router.post("/suppliers/{supplier_id}/enable", response_model=Result[dict])
def enable_supplier(
    supplier_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.set_supplier_active(
        db, supplier_id=supplier_id, active=True, staff=staff
    )
    return Result.ok(message="供应商已启用")


@router.post("/suppliers/{supplier_id}/disable", response_model=Result[dict])
def disable_supplier(
    supplier_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.set_supplier_active(
        db, supplier_id=supplier_id, active=False, staff=staff
    )
    return Result.ok(message="供应商已停用")


@router.get("/suppliers/{supplier_id}/accounts", response_model=Result[list[dict]])
def list_supplier_accounts(
    supplier_id: int,
    staff: Staff = Depends(read_party),
    db: Session = Depends(get_db),
):
    accounts = party_service.list_accounts(db, supplier_id=supplier_id)
    return Result.ok([party_service.account_dict(a) for a in accounts])


@router.post("/suppliers/{supplier_id}/accounts", response_model=Result[dict])
def create_supplier_account(
    supplier_id: int,
    req: AccountRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    account = party_service.create_account(
        db, supplier_id=supplier_id, data=_payload(req)
    )
    return Result.ok(party_service.account_dict(account), message="账户信息已创建")


# --------------------------------------------------------------------------- #
# 客户
# --------------------------------------------------------------------------- #


@router.get("/customers", response_model=Result[dict])
def list_customers(
    keyword: str | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_party),
    db: Session = Depends(get_db),
):
    return Result.ok(
        party_service.list_customers(
            db, keyword=keyword, is_active=is_active, page=page, page_size=page_size
        )
    )


@router.post("/customers", response_model=Result[dict])
def create_customer(
    req: CustomerCreateRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    customer = party_service.create_customer(db, data=_payload(req), staff=staff)
    return Result.ok(
        party_service.customer_dict(db, customer, with_accounts=True), message="客户已创建"
    )


@router.get("/customers/{customer_id}", response_model=Result[dict])
def get_customer(
    customer_id: int,
    staff: Staff = Depends(read_party),
    db: Session = Depends(get_db),
):
    customer = party_service.get_customer_or_404(db, customer_id)
    return Result.ok(party_service.customer_dict(db, customer, with_accounts=True))


@router.put("/customers/{customer_id}", response_model=Result[dict])
def update_customer(
    customer_id: int,
    req: CustomerUpdateRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    customer = party_service.update_customer(
        db, customer_id=customer_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        party_service.customer_dict(db, customer, with_accounts=True), message="客户已更新"
    )


@router.delete("/customers/{customer_id}", response_model=Result[dict])
def delete_customer(
    customer_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.delete_customer(db, customer_id=customer_id, staff=staff)
    return Result.ok(message="客户已删除")


@router.post("/customers/{customer_id}/enable", response_model=Result[dict])
def enable_customer(
    customer_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.set_customer_active(
        db, customer_id=customer_id, active=True, staff=staff
    )
    return Result.ok(message="客户已启用")


@router.post("/customers/{customer_id}/disable", response_model=Result[dict])
def disable_customer(
    customer_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.set_customer_active(
        db, customer_id=customer_id, active=False, staff=staff
    )
    return Result.ok(message="客户已停用")


@router.get("/customers/{customer_id}/accounts", response_model=Result[list[dict]])
def list_customer_accounts(
    customer_id: int,
    staff: Staff = Depends(read_party),
    db: Session = Depends(get_db),
):
    accounts = party_service.list_accounts(db, customer_id=customer_id)
    return Result.ok([party_service.account_dict(a) for a in accounts])


@router.post("/customers/{customer_id}/accounts", response_model=Result[dict])
def create_customer_account(
    customer_id: int,
    req: AccountRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    account = party_service.create_account(
        db, customer_id=customer_id, data=_payload(req)
    )
    return Result.ok(party_service.account_dict(account), message="账户信息已创建")


# --------------------------------------------------------------------------- #
# 账户通用操作
# --------------------------------------------------------------------------- #


@router.put("/accounts/{account_id}", response_model=Result[dict])
def update_account(
    account_id: int,
    req: AccountUpdateRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    account = party_service.update_account(
        db, account_id=account_id, data=_payload(req)
    )
    return Result.ok(party_service.account_dict(account), message="账户信息已更新")


@router.post("/accounts/{account_id}/default", response_model=Result[dict])
def set_default_account(
    account_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    account = party_service.update_account(
        db, account_id=account_id, data={"is_default": True}
    )
    return Result.ok(party_service.account_dict(account), message="已设为默认账户")


@router.delete("/accounts/{account_id}", response_model=Result[dict])
def delete_account(
    account_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.delete_account(db, account_id=account_id)
    return Result.ok(message="账户信息已删除")


# --------------------------------------------------------------------------- #
# 供应商商品价格
# --------------------------------------------------------------------------- #


@router.get("/suppliers/{supplier_id}/prices", response_model=Result[dict])
def list_supplier_prices(
    supplier_id: int,
    product_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    staff: Staff = Depends(read_party),
    db: Session = Depends(get_db),
):
    return Result.ok(
        party_service.list_supplier_prices(
            db,
            supplier_id=supplier_id,
            product_id=product_id,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/suppliers/{supplier_id}/prices", response_model=Result[dict])
def create_supplier_price(
    supplier_id: int,
    req: SupplierPriceRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    record = party_service.create_supplier_price(
        db, supplier_id=supplier_id, data=_payload(req)
    )
    return Result.ok(
        party_service.supplier_price_dict(db, record), message="供应商价格已创建"
    )


@router.put("/supplier-prices/{price_id}", response_model=Result[dict])
def update_supplier_price(
    price_id: int,
    req: SupplierPriceUpdateRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    record = party_service.update_supplier_price(
        db, price_id=price_id, data=_payload(req)
    )
    return Result.ok(
        party_service.supplier_price_dict(db, record), message="供应商价格已更新"
    )


@router.post("/supplier-prices/{price_id}/status", response_model=Result[dict])
def set_supplier_price_status(
    price_id: int,
    req: PriceStatusRequest,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    record = party_service.set_supplier_price_status(
        db, price_id=price_id, status=req.status
    )
    return Result.ok(
        party_service.supplier_price_dict(db, record), message="供应商价格状态已更新"
    )


@router.delete("/supplier-prices/{price_id}", response_model=Result[dict])
def delete_supplier_price(
    price_id: int,
    staff: Staff = Depends(manage_party),
    db: Session = Depends(get_db),
):
    party_service.delete_supplier_price(db, price_id=price_id)
    return Result.ok(message="供应商价格已删除")
