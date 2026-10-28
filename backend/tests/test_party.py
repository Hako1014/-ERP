from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.party import Supplier
from app.models.product import Product
from app.repositories.party_repo import get_customer_by_code, get_supplier_by_code


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in ["supplier", "customer", "account", "supplier_price"]:
        assert name in tables


def test_unique_indexes():
    insp = inspect(_engine())
    supplier_unique = {
        col for uniq in insp.get_unique_constraints("supplier") for col in uniq["column_names"]
    }
    customer_unique = {
        col for uniq in insp.get_unique_constraints("customer") for col in uniq["column_names"]
    }
    assert "code" in supplier_unique
    assert "code" in customer_unique


def test_product_supplier_fk():
    fks = inspect(_engine()).get_foreign_keys("product")
    assert any(
        fk["referred_table"] == "supplier" and "default_supplier_id" in fk["constrained_columns"]
        for fk in fks
    )


def test_party_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Supplier(code="GYS001", name="示例供应商"))
        db.add(Product(name="示例商品", code="SP001"))
        db.commit()
    with Session() as db:
        assert get_supplier_by_code(db, "GYS001").name == "示例供应商"


def test_supplier_duplicate_code_rejected():
    Session = sessionmaker(bind=_engine())
    with Session() as db:
        db.add(Supplier(code="DUP", name="A"))
        db.add(Supplier(code="DUP", name="B"))
        with pytest.raises(IntegrityError):
            db.commit()
