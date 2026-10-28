from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.party import router as party_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Staff
from app.models.party import Account, SupplierPrice
from app.models.product import Product
from app.models.purchase import PurchasePlan
from app.models.rbac import Role, StaffRole
from app.services import party_service, product_service


def _session_factory(pool: bool = False):
    kwargs = (
        {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        if pool
        else {}
    )
    engine = create_engine("sqlite:///:memory:", future=True, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db) -> Staff:
    db.add(Company(id=1, full_name="示例公司"))
    db.add(Staff(id=1, company_id=1, name="管理员", phone="13900000001"))
    db.commit()
    return db.get(Staff, 1)


def _supplier_data(**overrides) -> dict:
    data = {
        "code": "S0001",
        "name": "示例供应商",
        "contact": "张三",
        "phone": "13800000000",
        "settlement_type": "MONTHLY",
    }
    data.update(overrides)
    return data


def test_supplier_crud_and_code_unique():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        supplier = party_service.create_supplier(db, data=_supplier_data(), staff=staff)
        assert supplier.id == 1
        assert supplier.is_active is True

        with pytest.raises(BizException) as exc:
            party_service.create_supplier(
                db, data=_supplier_data(name="另一个"), staff=staff
            )
        assert "供应商编码已存在" in exc.value.message

        updated = party_service.update_supplier(
            db, supplier_id=supplier.id, data={"contact": "李四"}, staff=staff
        )
        assert updated.contact == "李四"

        result = party_service.list_suppliers(db, keyword="示例")
        assert result["total"] == 1

        party_service.set_supplier_active(
            db, supplier_id=supplier.id, active=False, staff=staff
        )
        assert party_service.list_suppliers(db, is_active=False)["total"] == 1


def test_supplier_delete_blocked_when_referenced():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        supplier = party_service.create_supplier(db, data=_supplier_data(), staff=staff)
        db.add(
            PurchasePlan(
                plan_no="PP-1", supplier_id=supplier.id, status="IN_PROGRESS"
            )
        )
        db.commit()
        with pytest.raises(BizException) as exc:
            party_service.delete_supplier(db, supplier_id=supplier.id, staff=staff)
        assert "不能删除" in exc.value.message


def test_customer_crud_and_delete_removes_accounts():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        customer = party_service.create_customer(
            db, data={"code": "C0001", "name": "示例客户"}, staff=staff
        )
        party_service.create_account(
            db,
            customer_id=customer.id,
            data={"bank": "工商银行", "account_no": "6222", "is_default": True},
        )
        assert len(party_service.list_accounts(db, customer_id=customer.id)) == 1

        party_service.delete_customer(db, customer_id=customer.id, staff=staff)
        assert db.get(type(customer), customer.id) is None
        assert db.scalars(select(Account).where(Account.customer_id == customer.id)).all() == []


def test_account_default_switch_and_scope_validation():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        supplier = party_service.create_supplier(db, data=_supplier_data(), staff=staff)
        first = party_service.create_account(
            db, supplier_id=supplier.id, data={"bank": "A", "is_default": True}
        )
        second = party_service.create_account(
            db, supplier_id=supplier.id, data={"bank": "B", "is_default": True}
        )
        accounts = party_service.list_accounts(db, supplier_id=supplier.id)
        defaults = [a.id for a in accounts if a.is_default]
        assert defaults == [second.id]
        assert first.id != second.id

        party_service.update_account(db, account_id=first.id, data={"is_default": True})
        accounts = party_service.list_accounts(db, supplier_id=supplier.id)
        assert [a.id for a in accounts if a.is_default] == [first.id]

        with pytest.raises(BizException):
            party_service.list_accounts(db)
        with pytest.raises(BizException):
            party_service.create_account(
                db,
                supplier_id=supplier.id,
                customer_id=1,
                data={"bank": "C"},
            )


def test_supplier_price_crud_and_uniqueness():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        supplier = party_service.create_supplier(db, data=_supplier_data(), staff=staff)
        product = product_service.create_product(
            db, data={"code": "P1", "name": "商品"}, staff=staff
        )
        sku = product_service.create_sku(
            db, product_id=product.id, data={"spec_code": "P1-RED"}
        )

        record = party_service.create_supplier_price(
            db,
            supplier_id=supplier.id,
            data={"product_id": product.id, "sku_id": sku.id, "price": "9.90"},
        )
        assert record.price == Decimal("9.90")

        with pytest.raises(BizException) as exc:
            party_service.create_supplier_price(
                db,
                supplier_id=supplier.id,
                data={"product_id": product.id, "sku_id": sku.id, "price": "10.00"},
            )
        assert "价格已存在" in exc.value.message

        # 同一商品不同规格 + 商品级价格（sku 为空）互不冲突
        party_service.create_supplier_price(
            db,
            supplier_id=supplier.id,
            data={"product_id": product.id, "price": "12.00"},
        )
        with pytest.raises(BizException):
            party_service.create_supplier_price(
                db,
                supplier_id=supplier.id,
                data={"product_id": product.id, "price": "13.00"},
            )

        assert party_service.get_supplier_price(
            db, supplier_id=supplier.id, product_id=product.id, sku_id=sku.id
        ) == Decimal("9.90")
        assert party_service.get_supplier_price(
            db, supplier_id=supplier.id, product_id=product.id
        ) == Decimal("12.00")


def test_supplier_price_validation():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        supplier = party_service.create_supplier(db, data=_supplier_data(), staff=staff)
        product = product_service.create_product(
            db, data={"code": "P1", "name": "商品"}, staff=staff
        )
        other = product_service.create_product(
            db, data={"code": "P2", "name": "另一商品"}, staff=staff
        )
        sku = product_service.create_sku(
            db, product_id=other.id, data={"spec_code": "P2-A"}
        )

        with pytest.raises(BizException) as exc:
            party_service.create_supplier_price(
                db,
                supplier_id=supplier.id,
                data={"product_id": product.id, "sku_id": sku.id, "price": "1.00"},
            )
        assert "不属于该商品" in exc.value.message

        with pytest.raises(BizException) as exc:
            party_service.create_supplier_price(
                db,
                supplier_id=supplier.id,
                data={"product_id": product.id, "price": 1.5},
            )
        assert "金额" in exc.value.message

        with pytest.raises(BizException) as exc:
            party_service.create_supplier_price(
                db,
                supplier_id=supplier.id,
                data={"product_id": product.id, "price": "0"},
            )
        assert "大于 0" in exc.value.message


def test_supplier_price_update_and_status():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        supplier = party_service.create_supplier(db, data=_supplier_data(), staff=staff)
        product = product_service.create_product(
            db, data={"code": "P1", "name": "商品"}, staff=staff
        )
        record = party_service.create_supplier_price(
            db,
            supplier_id=supplier.id,
            data={"product_id": product.id, "price": "5.00"},
        )
        updated = party_service.update_supplier_price(
            db, price_id=record.id, data={"price": "6.50", "status": "DISABLED"}
        )
        assert updated.price == Decimal("6.50")
        assert updated.status == "DISABLED"

        # 停用后不再作为采购取价结果
        assert (
            party_service.get_supplier_price(
                db, supplier_id=supplier.id, product_id=product.id
            )
            is None
        )

        party_service.delete_supplier_price(db, price_id=record.id)
        assert db.get(SupplierPrice, record.id) is None


def test_supplier_price_requires_supplier_product():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        product = product_service.create_product(
            db, data={"code": "P1", "name": "商品"}, staff=staff
        )
        with pytest.raises(BizException) as exc:
            party_service.create_supplier_price(
                db,
                supplier_id=999,
                data={"product_id": product.id, "price": "1.00"},
            )
        assert "供应商不存在" in exc.value.message


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
    app.include_router(party_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def _seed_roles(Session, codes=(("ADMIN", 9), ("PURCHASER", 1))):
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add_all(
            [
                Staff(id=1, company_id=1, name="采购员", phone="13900000001"),
                Staff(id=9, company_id=1, name="管理员", phone="13900000009"),
            ]
        )
        for role_id, (code, _) in enumerate(codes, start=1):
            db.add(Role(id=role_id, company_id=1, code=code, name=code))
        db.flush()
        for role_id, (_, staff_id) in enumerate(codes, start=1):
            db.add(StaffRole(staff_id=staff_id, role_id=role_id))
        db.commit()


def test_api_purchaser_can_read_but_not_write_parties():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    listed = client.get("/suppliers", headers=_auth(1))
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 0

    denied = client.post(
        "/suppliers",
        json={"code": "S1", "name": "供应商"},
        headers=_auth(1),
    )
    assert denied.status_code == 403


def test_api_admin_manages_supplier_account_and_price():
    app, Session = _make_api()
    _seed_roles(Session)
    with Session() as db:
        staff = db.get(Staff, 9)
        product = product_service.create_product(
            db, data={"code": "P1", "name": "商品"}, staff=staff
        )
        product_id = product.id

    client = TestClient(app)
    created = client.post(
        "/suppliers",
        json={"code": "S1", "name": "供应商", "contact": "王五"},
        headers=_auth(9, "管理员"),
    )
    assert created.status_code == 200
    supplier_id = created.json()["data"]["id"]

    account = client.post(
        f"/suppliers/{supplier_id}/accounts",
        json={"bank": "中国银行", "account_no": "6222", "is_default": True},
        headers=_auth(9, "管理员"),
    )
    assert account.status_code == 200
    assert account.json()["data"]["is_default"] is True

    price = client.post(
        f"/suppliers/{supplier_id}/prices",
        json={"product_id": product_id, "price": "8.80"},
        headers=_auth(9, "管理员"),
    )
    assert price.status_code == 200
    assert price.json()["data"]["price"] == "8.80"

    detail = client.get(f"/suppliers/{supplier_id}", headers=_auth(9, "管理员"))
    assert len(detail.json()["data"]["accounts"]) == 1
    assert client.get("/suppliers", headers=_auth(9, "管理员")).json()["data"]["total"] == 1
