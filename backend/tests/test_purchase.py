from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.party import Supplier
from app.models.purchase import PurchaseOrder, PurchasePlan
from app.repositories.purchase_repo import get_order_by_no, get_plan_by_no


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in [
        "purchase_plan",
        "purchase_plan_item",
        "purchase_order",
        "purchase_order_item",
        "purchase_prepay",
        "purchase_return",
        "purchase_return_item",
        "purchase_invoice",
    ]:
        assert name in tables


def test_document_no_unique():
    insp = inspect(_engine())
    assert {c for u in insp.get_unique_constraints("purchase_order") for c in u["column_names"]} == {"order_no"}
    assert {c for u in insp.get_unique_constraints("purchase_plan") for c in u["column_names"]} == {"plan_no"}


def test_purchase_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Supplier(code="GYS001", name="示例供应商"))
        db.flush()
        plan = PurchasePlan(plan_no="CGJH20260923-0001", supplier_id=1)
        db.add(plan)
        db.flush()
        db.add(PurchaseOrder(order_no="CGDD20260923-0001", supplier_id=1, plan_id=plan.id))
        db.commit()
    with Session() as db:
        assert get_plan_by_no(db, "CGJH20260923-0001").supplier_id == 1
        assert get_order_by_no(db, "CGDD20260923-0001").plan_id == 1


def test_order_duplicate_no_rejected():
    Session = sessionmaker(bind=_engine())
    with Session() as db:
        db.add(Supplier(code="GYS001", name="供应商"))
        db.add(PurchaseOrder(order_no="DUP", supplier_id=1))
        db.add(PurchaseOrder(order_no="DUP", supplier_id=1))
        with pytest.raises(IntegrityError):
            db.commit()
