from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.party import Account, Customer, Supplier, SupplierPrice


def get_supplier_by_code(db: Session, code: str) -> Supplier | None:
    return db.scalar(select(Supplier).where(Supplier.code == code))


def get_customer_by_code(db: Session, code: str) -> Customer | None:
    return db.scalar(select(Customer).where(Customer.code == code))


def list_suppliers(db: Session) -> list[Supplier]:
    return list(db.scalars(select(Supplier)))


def list_customers(db: Session) -> list[Customer]:
    return list(db.scalars(select(Customer)))


def list_accounts_by_supplier(db: Session, supplier_id: int) -> list[Account]:
    return list(db.scalars(select(Account).where(Account.supplier_id == supplier_id)))


def list_supplier_prices(db: Session, supplier_id: int) -> list[SupplierPrice]:
    return list(db.scalars(select(SupplierPrice).where(SupplierPrice.supplier_id == supplier_id)))
