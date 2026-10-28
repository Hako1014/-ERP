"""供应商与客户服务（TASK-021）。

覆盖：供应商/客户 CRUD、账户信息（多账户 + 默认账户）、供应商商品价格维护。
供应商账户余额与金蝶对接属于 TASK-046，不在本模块内实现。
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money, to_decimal
from app.db.base import Base
from app.models.org import Staff
from app.models.party import Account, Customer, Supplier, SupplierPrice
from app.models.product import Product, ProductSku

ACCOUNT_FIELDS = {"bank", "branch", "bank_no", "account_no", "address", "is_default"}
_PARTY_FIELDS = {
    "code",
    "name",
    "contact",
    "phone",
    "fax",
    "address",
    "settlement_type",
    "email",
    "remark",
    "is_active",
}

# 这些表属于往来单位的子表，删除主体时一并清理，不视为“被引用”。
_PARTY_CHILD_TABLES = {"account", "supplier_price"}


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


def _to_money(value: Any, label: str) -> Decimal:
    try:
        return round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc


# --------------------------------------------------------------------------- #
# 供应商
# --------------------------------------------------------------------------- #


def get_supplier(
    db: Session, supplier_id: int, *, company_id: int | None = None,
) -> Supplier | None:
    stmt = select(Supplier).where(Supplier.id == supplier_id)
    if company_id is not None:
        stmt = stmt.where(Supplier.company_id == company_id)
    return db.scalar(stmt)


def get_supplier_or_404(
    db: Session, supplier_id: int, *, company_id: int | None = None,
) -> Supplier:
    supplier = get_supplier(db, supplier_id, company_id=company_id)
    if supplier is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商不存在")
    return supplier


def supplier_dict(db: Session, supplier: Supplier, *, with_accounts: bool = False) -> dict:
    data = {
        "id": supplier.id,
        "code": supplier.code,
        "name": supplier.name,
        "contact": supplier.contact,
        "phone": supplier.phone,
        "fax": supplier.fax,
        "address": supplier.address,
        "settlement_type": supplier.settlement_type,
        "email": supplier.email,
        "remark": supplier.remark,
        "is_active": supplier.is_active,
    }
    if with_accounts:
        data["accounts"] = [
            account_dict(account) for account in list_accounts(db, supplier_id=supplier.id)
        ]
    return data


def _validate_supplier_code_unique(
    db: Session, code: str, *, exclude_id: int | None = None
) -> None:
    stmt = select(Supplier.id).where(Supplier.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Supplier.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"供应商编码已存在：{code}")


def _apply_party_fields(db: Session, party, data: dict, model, label: str) -> None:
    unknown = set(data) - _PARTY_FIELDS
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")

    values: dict[str, Any] = {}
    if "code" in data:
        code = _require_text(data["code"], f"{label}编码", 64)
        if model is Supplier:
            _validate_supplier_code_unique(db, code, exclude_id=party.id)
        else:
            _validate_customer_code_unique(db, code, exclude_id=party.id)
        values["code"] = code
    if "name" in data:
        values["name"] = _require_text(data["name"], f"{label}名称", 128)
    for field in _PARTY_FIELDS - {"code", "name", "is_active"}:
        if field in data:
            value = data[field]
            values[field] = str(value).strip() if value not in (None, "") else None
    if "is_active" in data:
        values["is_active"] = bool(data["is_active"])

    for field, value in values.items():
        setattr(party, field, value)


def create_supplier(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Supplier:
    if "code" not in data or "name" not in data:
        raise BizException(ErrorCode.BAD_REQUEST, "供应商编码与名称为必填项")
    _validate_supplier_code_unique(db, _require_text(data["code"], "供应商编码", 64))
    supplier = Supplier(code="", name="", is_active=True)
    _apply_party_fields(db, supplier, data, Supplier, "供应商")
    db.add(supplier)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="supplier",
        target_id=str(supplier.id),
        summary=f"新增供应商：{supplier.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return supplier


def update_supplier(
    db: Session, *, supplier_id: int, data: dict, staff: Staff, commit: bool = True
) -> Supplier:
    supplier = get_supplier_or_404(db, supplier_id)
    _apply_party_fields(db, supplier, data, Supplier, "供应商")
    write_audit(
        db,
        action="UPDATE",
        target_type="supplier",
        target_id=str(supplier.id),
        summary=f"修改供应商：{supplier.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return supplier


def list_suppliers(
    db: Session,
    *,
    keyword: str | None = None,
    is_active: bool | None = None,
    company_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if company_id is not None:
        conditions.append(Supplier.company_id == company_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(Supplier.name.like(like) | Supplier.code.like(like))
    if is_active is not None:
        conditions.append(Supplier.is_active.is_(is_active))
    total = db.scalar(select(func.count(Supplier.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Supplier)
            .where(*conditions)
            .order_by(Supplier.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [supplier_dict(db, row) for row in rows],
    }


def set_supplier_active(
    db: Session, *, supplier_id: int, active: bool, staff: Staff, commit: bool = True
) -> Supplier:
    supplier = get_supplier_or_404(db, supplier_id)
    supplier.is_active = active
    write_audit(
        db,
        action="ENABLE" if active else "DISABLE",
        target_type="supplier",
        target_id=str(supplier.id),
        summary=f"{'启用' if active else '停用'}供应商：{supplier.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return supplier


def _referenced_tables(db: Session, column_name: str, party_id: int) -> list[str]:
    used: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name in _PARTY_CHILD_TABLES:
            continue
        column = table.columns.get(column_name)
        if column is None:
            continue
        if not any(fk.target_fullname in {"supplier.id", "customer.id"} for fk in column.foreign_keys):
            continue
        stmt = select(literal(1)).select_from(table).where(column == party_id).limit(1)
        if db.execute(stmt).first() is not None:
            used.append(table.name)
    return used


def delete_supplier(
    db: Session, *, supplier_id: int, staff: Staff, commit: bool = True
) -> None:
    supplier = get_supplier_or_404(db, supplier_id)
    referenced = _referenced_tables(db, "supplier_id", supplier.id)
    if referenced:
        raise BizException(
            ErrorCode.CONFLICT,
            f"供应商已被 {','.join(sorted(referenced))} 引用，不能删除，请改为停用",
        )
    for account in db.scalars(select(Account).where(Account.supplier_id == supplier.id)):
        db.delete(account)
    for price in db.scalars(
        select(SupplierPrice).where(SupplierPrice.supplier_id == supplier.id)
    ):
        db.delete(price)
    db.delete(supplier)
    write_audit(
        db,
        action="DELETE",
        target_type="supplier",
        target_id=str(supplier.id),
        summary=f"删除供应商：{supplier.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 客户
# --------------------------------------------------------------------------- #


def get_customer(
    db: Session, customer_id: int, *, company_id: int | None = None,
) -> Customer | None:
    stmt = select(Customer).where(Customer.id == customer_id)
    if company_id is not None:
        stmt = stmt.where(Customer.company_id == company_id)
    return db.scalar(stmt)


def get_customer_or_404(
    db: Session, customer_id: int, *, company_id: int | None = None,
) -> Customer:
    customer = get_customer(db, customer_id, company_id=company_id)
    if customer is None:
        raise BizException(ErrorCode.NOT_FOUND, "客户不存在")
    return customer


def customer_dict(db: Session, customer: Customer, *, with_accounts: bool = False) -> dict:
    data = {
        "id": customer.id,
        "code": customer.code,
        "name": customer.name,
        "contact": customer.contact,
        "phone": customer.phone,
        "fax": customer.fax,
        "address": customer.address,
        "settlement_type": customer.settlement_type,
        "email": customer.email,
        "remark": customer.remark,
        "is_active": customer.is_active,
    }
    if with_accounts:
        data["accounts"] = [
            account_dict(account) for account in list_accounts(db, customer_id=customer.id)
        ]
    return data


def _validate_customer_code_unique(
    db: Session, code: str, *, exclude_id: int | None = None
) -> None:
    stmt = select(Customer.id).where(Customer.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Customer.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"客户编码已存在：{code}")


def create_customer(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Customer:
    if "code" not in data or "name" not in data:
        raise BizException(ErrorCode.BAD_REQUEST, "客户编码与名称为必填项")
    _validate_customer_code_unique(db, _require_text(data["code"], "客户编码", 64))
    customer = Customer(code="", name="", is_active=True)
    _apply_party_fields(db, customer, data, Customer, "客户")
    db.add(customer)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="customer",
        target_id=str(customer.id),
        summary=f"新增客户：{customer.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return customer


def update_customer(
    db: Session, *, customer_id: int, data: dict, staff: Staff, commit: bool = True
) -> Customer:
    customer = get_customer_or_404(db, customer_id)
    _apply_party_fields(db, customer, data, Customer, "客户")
    write_audit(
        db,
        action="UPDATE",
        target_type="customer",
        target_id=str(customer.id),
        summary=f"修改客户：{customer.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return customer


def list_customers(
    db: Session,
    *,
    keyword: str | None = None,
    is_active: bool | None = None,
    company_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if company_id is not None:
        conditions.append(Customer.company_id == company_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(Customer.name.like(like) | Customer.code.like(like))
    if is_active is not None:
        conditions.append(Customer.is_active.is_(is_active))
    total = db.scalar(select(func.count(Customer.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Customer)
            .where(*conditions)
            .order_by(Customer.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [customer_dict(db, row) for row in rows],
    }


def set_customer_active(
    db: Session, *, customer_id: int, active: bool, staff: Staff, commit: bool = True
) -> Customer:
    customer = get_customer_or_404(db, customer_id)
    customer.is_active = active
    write_audit(
        db,
        action="ENABLE" if active else "DISABLE",
        target_type="customer",
        target_id=str(customer.id),
        summary=f"{'启用' if active else '停用'}客户：{customer.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return customer


def delete_customer(
    db: Session, *, customer_id: int, staff: Staff, commit: bool = True
) -> None:
    customer = get_customer_or_404(db, customer_id)
    referenced = _referenced_tables(db, "customer_id", customer.id)
    if referenced:
        raise BizException(
            ErrorCode.CONFLICT,
            f"客户已被 {','.join(sorted(referenced))} 引用，不能删除，请改为停用",
        )
    for account in db.scalars(select(Account).where(Account.customer_id == customer.id)):
        db.delete(account)
    db.delete(customer)
    write_audit(
        db,
        action="DELETE",
        target_type="customer",
        target_id=str(customer.id),
        summary=f"删除客户：{customer.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 账户信息
# --------------------------------------------------------------------------- #


def account_dict(account: Account) -> dict:
    return {
        "id": account.id,
        "supplier_id": account.supplier_id,
        "customer_id": account.customer_id,
        "bank": account.bank,
        "branch": account.branch,
        "bank_no": account.bank_no,
        "account_no": account.account_no,
        "address": account.address,
        "is_default": account.is_default,
        "balance": account.balance,
        "kingdee_account_code": account.kingdee_account_code,
        "kingdee_sync_status": account.kingdee_sync_status,
        "kingdee_sync_message": account.kingdee_sync_message,
        "balance_synced_at": account.balance_synced_at,
    }


def list_accounts(
    db: Session, *, supplier_id: int | None = None, customer_id: int | None = None
) -> list[Account]:
    if (supplier_id is None) == (customer_id is None):
        raise BizException(ErrorCode.BAD_REQUEST, "必须且只能指定供应商或客户")
    condition = (
        Account.supplier_id == supplier_id
        if supplier_id is not None
        else Account.customer_id == customer_id
    )
    return list(
        db.scalars(
            select(Account)
            .where(condition)
            .order_by(Account.is_default.desc(), Account.id)
        )
    )


def _party_scope(
    db: Session, *, supplier_id: int | None, customer_id: int | None
) -> dict:
    if (supplier_id is None) == (customer_id is None):
        raise BizException(ErrorCode.BAD_REQUEST, "必须且只能指定供应商或客户")
    if supplier_id is not None:
        get_supplier_or_404(db, supplier_id)
        return {"supplier_id": supplier_id, "customer_id": None}
    get_customer_or_404(db, customer_id)
    return {"supplier_id": None, "customer_id": customer_id}


def create_account(
    db: Session,
    *,
    supplier_id: int | None = None,
    customer_id: int | None = None,
    data: dict,
    commit: bool = True,
) -> Account:
    scope = _party_scope(db, supplier_id=supplier_id, customer_id=customer_id)
    unknown = set(data) - ACCOUNT_FIELDS
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")
    account = Account(
        supplier_id=scope["supplier_id"],
        customer_id=scope["customer_id"],
        bank=data.get("bank"),
        branch=data.get("branch"),
        bank_no=data.get("bank_no"),
        account_no=data.get("account_no"),
        address=data.get("address"),
        is_default=bool(data.get("is_default", False)),
    )
    db.add(account)
    db.flush()
    if account.is_default:
        _reset_default_account(db, scope, keep_id=account.id)
    _commit(db, commit)
    return account


def _reset_default_account(db: Session, scope: dict, *, keep_id: int) -> None:
    condition = (
        Account.supplier_id == scope["supplier_id"]
        if scope["supplier_id"] is not None
        else Account.customer_id == scope["customer_id"]
    )
    for account in db.scalars(select(Account).where(condition)):
        account.is_default = account.id == keep_id
    db.flush()


def update_account(
    db: Session, *, account_id: int, data: dict, commit: bool = True
) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise BizException(ErrorCode.NOT_FOUND, "账户信息不存在")
    unknown = set(data) - ACCOUNT_FIELDS
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")

    for field in ACCOUNT_FIELDS - {"is_default"}:
        if field in data:
            value = data[field]
            setattr(account, field, str(value).strip() if value not in (None, "") else None)
    if "is_default" in data:
        account.is_default = bool(data["is_default"])
        if account.is_default:
            scope = {
                "supplier_id": account.supplier_id,
                "customer_id": account.customer_id,
            }
            db.flush()
            _reset_default_account(db, scope, keep_id=account.id)
    _commit(db, commit)
    return account


def delete_account(db: Session, *, account_id: int, commit: bool = True) -> None:
    account = db.get(Account, account_id)
    if account is None:
        raise BizException(ErrorCode.NOT_FOUND, "账户信息不存在")
    db.delete(account)
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 供应商商品价格
# --------------------------------------------------------------------------- #


def supplier_price_dict(db: Session, price: SupplierPrice) -> dict:
    product = db.get(Product, price.product_id)
    sku = db.get(ProductSku, price.sku_id) if price.sku_id else None
    return {
        "id": price.id,
        "supplier_id": price.supplier_id,
        "product_id": price.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "product_status": "DISABLED" if product and product.is_disabled else "ACTIVE",
        "sku_id": price.sku_id,
        "sku_spec_code": sku.spec_code if sku else None,
        "price": price.price,
        "status": price.status,
    }


def list_supplier_prices(
    db: Session,
    *,
    supplier_id: int,
    product_id: int | None = None,
    company_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    get_supplier_or_404(db, supplier_id, company_id=company_id)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = [SupplierPrice.supplier_id == supplier_id]
    if company_id is not None:
        conditions.append(SupplierPrice.company_id == company_id)
    if product_id:
        conditions.append(SupplierPrice.product_id == product_id)
    total = db.scalar(select(func.count(SupplierPrice.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(SupplierPrice)
            .where(*conditions)
            .order_by(SupplierPrice.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [supplier_price_dict(db, row) for row in rows],
    }


def _validate_price_target(
    db: Session, *, supplier_id: int, product_id: int, sku_id: int | None
) -> None:
    get_supplier_or_404(db, supplier_id)
    if db.get(Product, product_id) is None:
        raise BizException(ErrorCode.BAD_REQUEST, "商品不存在")
    if sku_id is not None:
        sku = db.get(ProductSku, sku_id)
        if sku is None or sku.product_id != product_id:
            raise BizException(ErrorCode.BAD_REQUEST, "商品规格不属于该商品")


def _validate_price_unique(
    db: Session,
    *,
    supplier_id: int,
    product_id: int,
    sku_id: int | None,
    exclude_id: int | None = None,
) -> None:
    stmt = select(SupplierPrice.id).where(
        SupplierPrice.supplier_id == supplier_id,
        SupplierPrice.product_id == product_id,
    )
    stmt = (
        stmt.where(SupplierPrice.sku_id.is_(None))
        if sku_id is None
        else stmt.where(SupplierPrice.sku_id == sku_id)
    )
    if exclude_id is not None:
        stmt = stmt.where(SupplierPrice.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, "该供应商商品价格已存在")


def create_supplier_price(
    db: Session,
    *,
    supplier_id: int,
    data: dict,
    commit: bool = True,
) -> SupplierPrice:
    unknown = set(data) - {"product_id", "sku_id", "price", "status"}
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")
    try:
        product_id = int(data["product_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "商品为必填项") from exc
    sku_id = data.get("sku_id")
    sku_id = int(sku_id) if sku_id not in (None, "") else None
    price = _to_money(data.get("price"), "定价")
    if price <= 0:
        raise BizException(ErrorCode.BAD_REQUEST, "定价必须大于 0")

    _validate_price_target(db, supplier_id=supplier_id, product_id=product_id, sku_id=sku_id)
    _validate_price_unique(
        db, supplier_id=supplier_id, product_id=product_id, sku_id=sku_id
    )
    record = SupplierPrice(
        supplier_id=supplier_id,
        product_id=product_id,
        sku_id=sku_id,
        price=price,
        status=data.get("status") or "ACTIVE",
    )
    db.add(record)
    _commit(db, commit)
    return record


def update_supplier_price(
    db: Session, *, price_id: int, data: dict, commit: bool = True
) -> SupplierPrice:
    record = db.get(SupplierPrice, price_id)
    if record is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商价格不存在")
    unknown = set(data) - {"product_id", "sku_id", "price", "status"}
    if unknown:
        raise BizException(ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}")

    product_id = int(data.get("product_id", record.product_id))
    sku_id = data.get("sku_id", record.sku_id)
    sku_id = int(sku_id) if sku_id not in (None, "") else None
    _validate_price_target(
        db, supplier_id=record.supplier_id, product_id=product_id, sku_id=sku_id
    )
    _validate_price_unique(
        db,
        supplier_id=record.supplier_id,
        product_id=product_id,
        sku_id=sku_id,
        exclude_id=record.id,
    )

    record.product_id = product_id
    record.sku_id = sku_id
    if "price" in data:
        price = _to_money(data["price"], "定价")
        if price <= 0:
            raise BizException(ErrorCode.BAD_REQUEST, "定价必须大于 0")
        record.price = price
    if "status" in data:
        record.status = data["status"]
    _commit(db, commit)
    return record


def set_supplier_price_status(
    db: Session, *, price_id: int, status: str, commit: bool = True
) -> SupplierPrice:
    record = db.get(SupplierPrice, price_id)
    if record is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商价格不存在")
    record.status = status
    _commit(db, commit)
    return record


def delete_supplier_price(db: Session, *, price_id: int, commit: bool = True) -> None:
    record = db.get(SupplierPrice, price_id)
    if record is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商价格不存在")
    db.delete(record)
    _commit(db, commit)


def get_supplier_price(
    db: Session, *, supplier_id: int, product_id: int, sku_id: int | None = None,
    company_id: int | None = None,
) -> Decimal | None:
    """采购单据取价：优先精确匹配规格价，其次回退到商品级价格。"""
    company_condition = (
        [SupplierPrice.company_id == company_id]
        if company_id is not None else []
    )
    if sku_id is not None:
        exact = db.scalar(
            select(SupplierPrice.price).where(
                SupplierPrice.supplier_id == supplier_id,
                SupplierPrice.product_id == product_id,
                SupplierPrice.sku_id == sku_id,
                SupplierPrice.status == "ACTIVE",
                *company_condition,
            )
        )
        if exact is not None:
            return exact
    return db.scalar(
        select(SupplierPrice.price).where(
            SupplierPrice.supplier_id == supplier_id,
            SupplierPrice.product_id == product_id,
            SupplierPrice.sku_id.is_(None),
            SupplierPrice.status == "ACTIVE",
            *company_condition,
        )
    )
