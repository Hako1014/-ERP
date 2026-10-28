from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.finance import AccountSubject, Voucher, VoucherItem
from app.repositories.finance_repo import get_voucher_by_no, list_voucher_items


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in [
        "account_subject",
        "project_category",
        "voucher",
        "voucher_item",
        "payment",
        "receipt",
        "expense",
        "settlement",
        "income",
        "invoice",
        "approval_flow",
        "approval_record",
    ]:
        assert name in tables


def test_voucher_no_unique():
    cols = {
        c for u in inspect(_engine()).get_unique_constraints("voucher") for c in u["column_names"]
    }
    assert "voucher_no" in cols


def test_finance_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        subject = AccountSubject(code="1001", name="库存现金")
        db.add(subject)
        db.flush()
        voucher = Voucher(voucher_no="PZ20260923-0001", total_debit=Decimal("100"), total_credit=Decimal("100"))
        db.add(voucher)
        db.flush()
        db.add(VoucherItem(voucher_id=voucher.id, subject_id=subject.id, debit=Decimal("100")))
        db.commit()
    with Session() as db:
        assert get_voucher_by_no(db, "PZ20260923-0001").total_debit == Decimal("100")
        assert len(list_voucher_items(db, 1)) == 1


def test_voucher_duplicate_no_rejected():
    Session = sessionmaker(bind=_engine())
    with Session() as db:
        db.add(Voucher(voucher_no="DUP"))
        db.add(Voucher(voucher_no="DUP"))
        with pytest.raises(IntegrityError):
            db.commit()
