from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.voucher import router as voucher_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.finance import AccountSubject, Voucher
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services import voucher_service


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
            Staff(id=1, company_id=1, name="财务", phone="13800003001"),
            Staff(id=2, company_id=1, name="仓管", phone="13800003002"),
        ]
    )
    db.add_all(
        [
            AccountSubject(
                id=1,
                code="1002",
                name="银行存款",
                category="ASSET",
                balance_direction="DEBIT",
                is_bank=True,
            ),
            AccountSubject(
                id=2,
                code="2202",
                name="应付账款",
                category="LIABILITY",
                balance_direction="CREDIT",
            ),
            AccountSubject(
                id=3,
                code="6602",
                name="管理费用",
                category="PROFIT_LOSS",
                balance_direction="DEBIT",
            ),
            AccountSubject(
                id=4,
                code="1405",
                name="库存商品",
                category="ASSET",
                balance_direction="DEBIT",
                is_enabled=False,
            ),
        ]
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _items(amount: str = "100.00") -> list[dict]:
    return [
        {
            "subject_id": 3,
            "summary": "报销",
            "debit": amount,
            "credit": "0.00",
        },
        {
            "subject_id": 1,
            "summary": "报销",
            "debit": "0.00",
            "credit": amount,
        },
    ]


def test_voucher_crud_audit_unaudit_and_soft_delete():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        voucher = voucher_service.create_voucher(
            db,
            data={
                "voucher_word": "记",
                "voucher_no": "PZ-001",
                "reference": "手工凭证",
                "items": _items(),
            },
            staff=staff,
        )
        assert voucher.status == "DRAFT"
        assert voucher.total_debit == Decimal("100.00")
        assert voucher.total_credit == Decimal("100.00")

        voucher = voucher_service.update_voucher(
            db,
            voucher_id=voucher.id,
            data={"items": _items("120.00"), "reference": "调整后"},
            staff=staff,
        )
        detail = voucher_service.voucher_dict(db, voucher)
        assert detail["reference"] == "调整后"
        assert detail["total_debit"] == Decimal("120.00")
        assert [item["subject_code"] for item in detail["items"]] == [
            "6602",
            "1002",
        ]

        voucher = voucher_service.audit_voucher(
            db, voucher_id=voucher.id, staff=staff, comment="审核通过"
        )
        assert voucher.status == "APPROVED"
        assert voucher.auditor_id == staff.id

        with pytest.raises(BizException) as edit_exc:
            voucher_service.update_voucher(
                db,
                voucher_id=voucher.id,
                data={"reference": "不允许修改"},
                staff=staff,
            )
        assert "仅草稿" in edit_exc.value.message

        with pytest.raises(BizException) as delete_exc:
            voucher_service.delete_voucher(
                db, voucher_id=voucher.id, staff=staff
            )
        assert "仅草稿" in delete_exc.value.message

        voucher = voucher_service.unaudit_voucher(
            db,
            voucher_id=voucher.id,
            staff=staff,
            reason="调整科目",
        )
        assert voucher.status == "DRAFT"
        assert voucher.auditor_id is None

        voucher_service.delete_voucher(
            db,
            voucher_id=voucher.id,
            staff=staff,
            reason="错误凭证",
        )
        assert voucher.status == "INVALID"
        listed = voucher_service.list_vouchers(db, status="INVALID")
        assert listed["total"] == 1
        assert listed["items"][0]["voucher_no"] == "PZ-001"


def test_voucher_balance_subject_and_duplicate_validation():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)

        with pytest.raises(BizException) as unbalanced:
            voucher_service.create_voucher(
                db,
                data={
                    "voucher_no": "PZ-BAD-1",
                    "items": [
                        {
                            "subject_id": 3,
                            "debit": "100.00",
                            "credit": "0.00",
                        },
                        {
                            "subject_id": 1,
                            "debit": "90.00",
                            "credit": "0.00",
                        },
                    ],
                },
                staff=staff,
            )
        assert "借贷不平衡" in unbalanced.value.message

        with pytest.raises(BizException) as both:
            voucher_service.create_voucher(
                db,
                data={
                    "voucher_no": "PZ-BAD-2",
                    "items": [
                        {
                            "subject_id": 3,
                            "debit": "10.00",
                            "credit": "10.00",
                        },
                        {
                            "subject_id": 1,
                            "debit": "0.00",
                            "credit": "10.00",
                        },
                    ],
                },
                staff=staff,
            )
        assert "不能同时" in both.value.message

        with pytest.raises(BizException) as disabled:
            voucher_service.create_voucher(
                db,
                data={
                    "voucher_no": "PZ-BAD-3",
                    "items": [
                        {
                            "subject_id": 4,
                            "debit": "10.00",
                            "credit": "0.00",
                        },
                        {
                            "subject_id": 1,
                            "debit": "0.00",
                            "credit": "10.00",
                        },
                    ],
                },
                staff=staff,
            )
        assert "已停用" in disabled.value.message

        voucher_service.create_voucher(
            db,
            data={"voucher_no": "PZ-DUP", "items": _items()},
            staff=staff,
        )
        with pytest.raises(BizException) as duplicate:
            voucher_service.create_voucher(
                db,
                data={"voucher_no": "PZ-DUP", "items": _items()},
                staff=staff,
            )
        assert "已存在" in duplicate.value.message


def test_business_voucher_is_idempotent_and_regenerates_after_invalidation(
    monkeypatch,
):
    serials = iter(["PZ20260923-0001", "PZ20260923-0002"])
    monkeypatch.setattr(
        voucher_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        first = voucher_service.create_prepay_voucher(
            db,
            prepay_no="YFSQ20260923-0001",
            amount=Decimal("300.00"),
            supplier_name="示例供应商",
            staff=staff,
        )
        same = voucher_service.create_prepay_voucher(
            db,
            prepay_no="YFSQ20260923-0001",
            amount=Decimal("300.00"),
            supplier_name="示例供应商",
            staff=staff,
        )
        assert same.id == first.id

        voucher_service.invalidate_voucher_by_reference(
            db,
            reference="采购预付款申请:YFSQ20260923-0001",
            staff=staff,
            reason="弃审重审",
        )
        regenerated = voucher_service.create_prepay_voucher(
            db,
            prepay_no="YFSQ20260923-0001",
            amount=Decimal("300.00"),
            supplier_name="示例供应商",
            staff=staff,
        )
        assert regenerated.id != first.id
        assert regenerated.voucher_no == "PZ20260923-0002"
        assert db.scalar(
            select(Voucher).where(Voucher.id == first.id)
        ).status == "INVALID"


@pytest.mark.parametrize(
    ("stock_in_type", "credit_code"),
    [
        ("PURCHASE", "2202"),
        ("AFTER_SALE", "6401"),
        ("MANUAL", "1901"),
        ("CHECK", "1901"),
        ("PUSH", "1901"),
        ("ADJUST", "1901"),
    ],
)
def test_stock_in_voucher_type_mapping(
    stock_in_type: str, credit_code: str, monkeypatch
):
    monkeypatch.setattr(
        voucher_service,
        "generate_serial",
        lambda prefix, dt=None: "PZ20260923-0001",
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        disabled = db.get(AccountSubject, 4)
        disabled.is_enabled = True
        db.commit()

        voucher = voucher_service.create_stock_in_voucher(
            db,
            stock_in_type=stock_in_type,
            stock_in_no="RKD20260923-0001",
            amount=Decimal("99.50"),
            staff=db.get(Staff, 2),
        )
        detail = voucher_service.voucher_dict(db, voucher)
        assert [item["subject_code"] for item in detail["items"]] == [
            "1405",
            credit_code,
        ]
        assert detail["total_debit"] == Decimal("99.50")
        assert detail["total_credit"] == Decimal("99.50")


def test_transfer_stock_in_does_not_generate_company_voucher():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        with pytest.raises(BizException) as exc:
            voucher_service.create_stock_in_voucher(
                db,
                stock_in_type="TRANSFER",
                stock_in_no="RKD20260923-0002",
                amount=Decimal("50.00"),
                staff=db.get(Staff, 2),
            )
        assert "不生成公司级会计凭证" in exc.value.message


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
    app.include_router(voucher_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _seed_roles(Session) -> None:
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="FINANCE", name="财务"),
                Role(id=2, company_id=1, code="WAREHOUSE", name="仓库"),
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


def test_api_voucher_permissions_workflow_and_kingdee_adapter():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    denied = client.get("/vouchers", headers=_auth(2))
    assert denied.status_code == 403

    created = client.post(
        "/vouchers",
        json={
            "voucher_word": "记",
            "voucher_no": "PZ-API-001",
            "items": _items("88.00"),
        },
        headers=_auth(1),
    )
    assert created.status_code == 200
    voucher_id = created.json()["data"]["id"]

    audited = client.post(
        f"/vouchers/{voucher_id}/audit",
        json={"comment": "同意"},
        headers=_auth(1),
    )
    assert audited.status_code == 200
    assert audited.json()["data"]["status"] == "APPROVED"

    mock_synced = client.post(
        f"/vouchers/{voucher_id}/kingdee-sync",
        headers=_auth(1),
    )
    assert mock_synced.status_code == 200
    assert mock_synced.json()["data"]["response"]["kingdee_no"].startswith("MOCK-")

    voucher_service.register_kingdee_transporter(
        lambda payload: {
            "success": True,
            "kingdee_no": f"KD-{payload['voucher_no']}",
        }
    )
    try:
        synced = client.post(
            f"/vouchers/{voucher_id}/kingdee-sync",
            headers=_auth(1),
        )
        assert synced.status_code == 200
        assert synced.json()["data"]["response"]["success"] is True
    finally:
        voucher_service.register_kingdee_transporter(None)

    unaudited = client.post(
        f"/vouchers/{voucher_id}/unaudit",
        json={"reason": "复核调整"},
        headers=_auth(1),
    )
    assert unaudited.status_code == 200
    assert unaudited.json()["data"]["status"] == "DRAFT"
