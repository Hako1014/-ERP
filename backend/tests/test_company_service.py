import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.company import router as company_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Staff
from app.models.purchase import PurchaseOrder
from app.models.rbac import Role, StaffRole
from app.services import auth_service, company_service


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
    db.add(
        Staff(
            id=1,
            company_id=1,
            name="管理员",
            phone="13900000001",
            password_hash=hash_password("admin123"),
        )
    )
    db.commit()
    return db.get(Staff, 1)


def test_company_department_and_position_crud():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)

        company = company_service.update_company(
            db,
            company_id=1,
            data={
                "full_name": "示例电商有限公司",
                "short_name": "示例电商",
                "legal_person": "张三",
                "phone": "13800000000",
                "tax_no": "91310000TEST",
            },
            staff=staff,
        )
        assert company.full_name == "示例电商有限公司"
        assert company_service.company_dict(company)["tax_no"] == "91310000TEST"

        operations = company_service.create_department(
            db,
            company_id=1,
            data={"name": "运营中心", "sort_order": 1},
            staff=staff,
        )
        warehouse = company_service.create_department(
            db,
            company_id=1,
            data={"name": "仓库组", "parent_id": operations.id, "sort_order": 2},
            staff=staff,
        )
        assert [item.id for item in company_service.list_departments(db, 1)] == [
            operations.id,
            warehouse.id,
        ]

        updated = company_service.update_department(
            db,
            department_id=warehouse.id,
            company_id=1,
            data={"name": "仓储组", "sort_order": 3},
            staff=staff,
        )
        assert updated.name == "仓储组"
        with pytest.raises(BizException) as exc:
            company_service.update_department(
                db,
                department_id=operations.id,
                company_id=1,
                data={"parent_id": warehouse.id},
                staff=staff,
            )
        assert "循环" in exc.value.message

        position = company_service.create_position(
            db, company_id=1, data={"name": "采购主管"}, staff=staff
        )
        assert company_service.position_dict(position)["name"] == "采购主管"
        position = company_service.update_position(
            db,
            position_id=position.id,
            company_id=1,
            data={"name": "采购经理"},
            staff=staff,
        )
        assert position.name == "采购经理"

        company_service.delete_position(
            db, position_id=position.id, company_id=1, staff=staff
        )
        company_service.delete_department(
            db, department_id=warehouse.id, company_id=1, staff=staff
        )
        company_service.delete_department(
            db, department_id=operations.id, company_id=1, staff=staff
        )
        assert company_service.list_departments(db, 1) == []


def test_department_and_position_delete_guards():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        department = company_service.create_department(
            db, company_id=1, data={"name": "运营中心"}, staff=staff
        )
        position = company_service.create_position(
            db, company_id=1, data={"name": "运营专员"}, staff=staff
        )
        company_service.create_staff(
            db,
            company_id=1,
            data={
                "name": "员工甲",
                "phone": "13900000002",
                "department_id": department.id,
                "position_id": position.id,
            },
            staff=staff,
        )

        with pytest.raises(BizException) as department_exc:
            company_service.delete_department(
                db, department_id=department.id, company_id=1, staff=staff
            )
        assert "员工" in department_exc.value.message

        with pytest.raises(BizException) as position_exc:
            company_service.delete_position(
                db, position_id=position.id, company_id=1, staff=staff
            )
        assert "员工" in position_exc.value.message


def test_staff_crud_login_account_and_password_reset():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        department = company_service.create_department(
            db, company_id=1, data={"name": "采购部"}, staff=staff
        )
        position = company_service.create_position(
            db, company_id=1, data={"name": "采购员"}, staff=staff
        )

        employee = company_service.create_staff(
            db,
            company_id=1,
            data={
                "name": "采购员甲",
                "staff_no": "E0001",
                "login_account": "buyer01",
                "phone": "13900000002",
                "password": "old-pass1",
                "department_id": department.id,
                "position_id": position.id,
                "is_distribution": True,
            },
            staff=staff,
        )
        assert employee.password_hash != "old-pass1"
        assert auth_service.authenticate(db, "13900000002", "old-pass1").id == employee.id

        with pytest.raises(BizException) as exc:
            company_service.create_staff(
                db,
                company_id=1,
                data={
                    "name": "采购员乙",
                    "login_account": "buyer01",
                    "phone": "13900000003",
                },
                staff=staff,
            )
        assert "登录账户已存在" in exc.value.message

        employee = company_service.update_staff(
            db,
            staff_id=employee.id,
            company_id=1,
            data={"name": "采购员甲（主管）", "region": "华东"},
            staff=staff,
        )
        detail = company_service.staff_dict(db, employee)
        assert detail["department_name"] == "采购部"
        assert detail["position_name"] == "采购员"
        assert detail["region"] == "华东"
        assert company_service.list_staff(db, company_id=1, keyword="主管")["total"] == 1

        company_service.reset_staff_password(
            db,
            staff_id=employee.id,
            company_id=1,
            new_password="new-pass1",
            staff=staff,
        )
        assert auth_service.authenticate(db, "13900000002", "new-pass1").id == employee.id
        with pytest.raises(BizException):
            auth_service.authenticate(db, "13900000002", "old-pass1")

        company_service.set_staff_active(
            db, staff_id=employee.id, company_id=1, active=False, staff=staff
        )
        with pytest.raises(BizException) as inactive_exc:
                auth_service.authenticate(db, "13900000002", "new-pass1")
        assert "账号已停用" in inactive_exc.value.message

        company_service.set_staff_active(
            db, staff_id=employee.id, company_id=1, active=True, staff=staff
        )
        company_service.delete_staff(
            db, staff_id=employee.id, company_id=1, staff=staff
        )
        assert company_service.list_staff(db, company_id=1, keyword="采购员甲")["total"] == 0


def test_staff_delete_blocked_when_referenced():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        employee = company_service.create_staff(
            db,
            company_id=1,
            data={"name": "采购员甲", "phone": "13900000002"},
            staff=staff,
        )
        db.add(
            PurchaseOrder(
                order_no="PO-001",
                supplier_id=1,
                purchaser_id=employee.id,
            )
        )
        db.commit()

        with pytest.raises(BizException) as exc:
            company_service.delete_staff(
                db, staff_id=employee.id, company_id=1, staff=staff
            )
        assert "不能删除" in exc.value.message
        assert "purchase_order" in exc.value.message


def test_ip_rule_crud_validation_and_uniqueness():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        rule = company_service.create_ip_rule(
            db,
            company_id=1,
            data={"rule_type": "ALLOW", "ip_address": "192.168.1.10", "remark": "办公网"},
            staff=staff,
        )
        assert rule.rule_type == "ALLOW"

        with pytest.raises(BizException) as duplicate_exc:
            company_service.create_ip_rule(
                db,
                company_id=1,
                data={"rule_type": "ALLOW", "ip_address": "192.168.1.10"},
                staff=staff,
            )
        assert "已存在" in duplicate_exc.value.message

        with pytest.raises(BizException) as invalid_exc:
            company_service.create_ip_rule(
                db,
                company_id=1,
                data={"rule_type": "ALLOW", "ip_address": "not-an-ip"},
                staff=staff,
            )
        assert "格式不正确" in invalid_exc.value.message

        updated = company_service.update_ip_rule(
            db,
            rule_id=rule.id,
            company_id=1,
            data={"rule_type": "DENY", "ip_address": "10.0.0.0/24"},
            staff=staff,
        )
        assert updated.rule_type == "DENY"
        assert len(company_service.list_ip_rules(db, 1)) == 1

        company_service.delete_ip_rule(
            db, rule_id=rule.id, company_id=1, staff=staff
        )
        assert company_service.list_ip_rules(db, 1) == []


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
    app.include_router(company_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def _seed_roles(Session):
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add_all(
            [
                Staff(
                    id=1,
                    company_id=1,
                    name="采购员",
                    phone="13900000001",
                ),
                Staff(
                    id=9,
                    company_id=1,
                    name="管理员",
                    phone="13900000009",
                    password_hash=hash_password("admin123"),
                ),
            ]
        )
        db.add_all(
            [
                Role(id=1, company_id=1, code="PURCHASER", name="采购员"),
                Role(id=2, company_id=1, code="ADMIN", name="管理员"),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=9, role_id=2),
            ]
        )
        db.commit()


def test_api_company_settings_requires_admin_permission():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    denied = client.get("/company", headers=_auth(1))
    assert denied.status_code == 403


def test_api_admin_manages_company_department_position_staff_and_ip():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)
    headers = _auth(9, "管理员")

    company = client.put(
        "/company",
        json={"short_name": "示例电商"},
        headers=headers,
    )
    assert company.status_code == 200
    assert company.json()["data"]["short_name"] == "示例电商"

    department = client.post(
        "/departments",
        json={"name": "采购部"},
        headers=headers,
    )
    assert department.status_code == 200
    department_id = department.json()["data"]["id"]

    position = client.post(
        "/positions",
        json={"name": "采购员"},
        headers=headers,
    )
    assert position.status_code == 200
    position_id = position.json()["data"]["id"]

    employee = client.post(
        "/staff",
        json={
            "name": "采购员甲",
            "staff_no": "E0001",
            "login_account": "buyer01",
            "phone": "13900000002",
            "password": "old-pass1",
            "department_id": department_id,
            "position_id": position_id,
        },
        headers=headers,
    )
    assert employee.status_code == 200
    employee_id = employee.json()["data"]["id"]

    reset = client.post(
        f"/staff/{employee_id}/reset-password",
        json={"new_password": "new-pass1"},
        headers=headers,
    )
    assert reset.status_code == 200

    rules = client.post(
        "/ip-rules",
        json={"rule_type": "ALLOW", "ip_address": "192.168.1.10"},
        headers=headers,
    )
    assert rules.status_code == 200
    assert client.get("/staff", headers=headers).json()["data"]["total"] == 3
