from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.account_subject import router as account_subject_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.finance import AccountSubject, Voucher, VoucherItem
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services import account_subject_service


def _session_factory(pool: bool = False):
    kwargs = (
        {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        if pool
        else {}
    )
    engine = create_engine("sqlite:///:memory:", future=True, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db) -> None:
    db.add(Company(id=1, full_name="示例公司"))
    db.add_all(
        [
            Staff(id=1, company_id=1, name="财务", phone="13800002001"),
            Staff(id=2, company_id=1, name="采购员", phone="13800002002"),
        ]
    )
    db.commit()


def _staff(db, staff_id: int = 1) -> Staff:
    return db.get(Staff, staff_id)


def test_subject_and_project_category_crud():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        category = account_subject_service.create_project_category(
            db,
            data={"code": "DEPT", "name": "部门", "remark": "按部门核算"},
            staff=_staff(db),
        )
        assert category.code == "DEPT"

        subject = account_subject_service.create_subject(
            db,
            data={
                "code": "6602",
                "name": "管理费用",
                "category": "PROFIT_LOSS",
                "balance_direction": "DEBIT",
                "is_auxiliary": True,
                "is_qty_amount": False,
                "project_category_id": category.id,
            },
            staff=_staff(db),
        )
        detail = account_subject_service.subject_dict(db, subject)
        assert detail["category_name"] == "损益"
        assert detail["balance_direction_name"] == "借方"
        assert detail["project_category_name"] == "部门"

        listed = account_subject_service.list_subjects(
            db, keyword="管理", category="PROFIT_LOSS"
        )
        assert listed["total"] == 1
        assert listed["items"][0]["code"] == "6602"

        updated = account_subject_service.update_subject(
            db,
            subject_id=subject.id,
            data={"name": "管理费用-电商", "is_enabled": False},
            staff=_staff(db),
        )
        assert updated.name == "管理费用-电商"
        assert updated.is_enabled is False

        with pytest.raises(BizException) as exc:
            account_subject_service.delete_project_category(
                db, category_id=category.id, staff=_staff(db)
            )
        assert "会计科目使用" in exc.value.message

        account_subject_service.delete_subject(
            db, subject_id=subject.id, staff=_staff(db)
        )
        account_subject_service.delete_project_category(
            db, category_id=category.id, staff=_staff(db)
        )
        assert db.get(AccountSubject, subject.id) is None


def test_subject_validation_and_voucher_reference_protection():
    Session = _session_factory()
    with Session() as db:
        _seed(db)

        with pytest.raises(BizException) as exc:
            account_subject_service.create_subject(
                db,
                data={
                    "code": "1405",
                    "name": "库存商品",
                    "category": "ASSET",
                    "is_qty_amount": True,
                },
                staff=_staff(db),
            )
        assert "计量单位" in exc.value.message

        subject = account_subject_service.create_subject(
            db,
            data={
                "code": "1001",
                "name": "库存现金",
                "category": "ASSET",
                "is_cash": True,
            },
            staff=_staff(db),
        )
        with pytest.raises(BizException) as duplicate:
            account_subject_service.create_subject(
                db,
                data={"code": "1001", "name": "重复科目"},
                staff=_staff(db),
            )
        assert "已存在" in duplicate.value.message

        voucher = Voucher(
            voucher_no="PZ-001",
            total_debit=Decimal("100.00"),
            total_credit=Decimal("100.00"),
        )
        db.add(voucher)
        db.flush()
        db.add(
            VoucherItem(
                voucher_id=voucher.id,
                subject_id=subject.id,
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
            )
        )
        db.commit()

        with pytest.raises(BizException) as used:
            account_subject_service.delete_subject(
                db, subject_id=subject.id, staff=_staff(db)
            )
        assert "不能删除" in used.value.message


def test_kingdee_seed_import_is_idempotent_and_does_not_override_by_default():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        first = account_subject_service.import_kingdee_subjects(
            db, staff=_staff(db)
        )
        second = account_subject_service.import_kingdee_subjects(
            db, staff=_staff(db)
        )
        assert first["created"] == len(account_subject_service.KINGDEE_SUBJECT_SEED)
        assert first["skipped"] == 0
        assert second["created"] == 0
        assert second["skipped"] == first["created"]

        bank = db.scalar(
            select(AccountSubject).where(AccountSubject.code == "1002")
        )
        assert bank.is_bank is True
        prepay = db.scalar(
            select(AccountSubject).where(AccountSubject.code == "1123")
        )
        assert prepay.is_auxiliary is True

        bank.name = "人工维护银行科目"
        db.commit()
        account_subject_service.import_kingdee_subjects(
            db, staff=_staff(db)
        )
        db.refresh(bank)
        assert bank.name == "人工维护银行科目"

        account_subject_service.import_kingdee_subjects(
            db, staff=_staff(db), update_existing=True
        )
        db.refresh(bank)
        assert bank.name == "银行存款"


def _make_api():
    Session = _session_factory(pool=True)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_subject_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _seed_roles(Session) -> None:
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="FINANCE", name="财务"),
                Role(id=2, company_id=1, code="PURCHASER", name="采购员"),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=2, role_id=2),
            ]
        )
        db.commit()


def _auth(staff_id: int) -> dict:
    token = create_access_token(staff_id, f"staff-{staff_id}", [])
    return {"Authorization": f"Bearer {token}"}


def test_api_finance_permissions_and_subject_crud():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    denied = client.get("/account-subjects", headers=_auth(2))
    assert denied.status_code == 403

    created_category = client.post(
        "/project-categories",
        json={"code": "PROJECT", "name": "项目"},
        headers=_auth(1),
    )
    assert created_category.status_code == 200

    created_subject = client.post(
        "/account-subjects",
        json={
            "code": "6601",
            "name": "销售费用",
            "category": "PROFIT_LOSS",
            "balance_direction": "DEBIT",
            "project_category_id": created_category.json()["data"]["id"],
        },
        headers=_auth(1),
    )
    assert created_subject.status_code == 200
    assert created_subject.json()["data"]["project_category_code"] == "PROJECT"

    listed = client.get(
        "/account-subjects",
        params={"keyword": "销售"},
        headers=_auth(1),
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
