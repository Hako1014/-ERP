from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.logistics import router as logistics_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services import logistics_service


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


def _create_logistics(db, staff: Staff, name: str = "顺丰"):
    return logistics_service.create_logistics(
        db,
        data={"name": name, "company": "顺丰速运", "phone": "95338"},
        staff=staff,
    )


def _create_template(
    db,
    staff: Staff,
    logistics_id: int,
    name: str,
    **overrides,
):
    data = {
        "name": name,
        "logistics_id": logistics_id,
        "charge_type": "BY_COUNT",
        "first_count": 2,
        "continue_count": 2,
        "base_fee": "10.00",
        "continue_fee": "2.00",
    }
    data.update(overrides)
    return logistics_service.create_freight_template(db, data=data, staff=staff)


def test_logistics_crud_default_mutex_and_reference_guard():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        first = _create_logistics(db, staff, "顺丰")
        second = _create_logistics(db, staff, "中通")

        logistics_service.set_default_logistics(
            db, logistics_id=first.id, staff=staff
        )
        logistics_service.set_default_logistics(
            db, logistics_id=second.id, staff=staff
        )
        rows = logistics_service.list_logistics(db)
        assert [row.id for row in rows if row.is_default] == [second.id]

        disabled = logistics_service.set_logistics_enabled(
            db, logistics_id=second.id, enabled=False, staff=staff
        )
        assert disabled.is_enabled is False
        assert disabled.is_default is False

        updated = logistics_service.update_logistics(
            db,
            logistics_id=first.id,
            data={"contact": "王五", "is_default": True},
            staff=staff,
        )
        assert updated.is_default is True
        assert updated.is_enabled is True

        template = _create_template(db, staff, first.id, "默认运费")
        with pytest.raises(BizException) as exc:
            logistics_service.delete_logistics(
                db, logistics_id=first.id, staff=staff
            )
        assert "freight_template" in exc.value.message

        logistics_service.delete_freight_template(
            db, template_id=template.id, staff=staff
        )
        logistics_service.delete_logistics(
            db, logistics_id=first.id, staff=staff
        )
        assert len(logistics_service.list_logistics(db)) == 1


def test_freight_by_count_tax_and_weight_rules():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        logistics = _create_logistics(db, staff)
        count_template = _create_template(
            db,
            staff,
            logistics.id,
            "按件模板",
            first_count=2,
            continue_count=2,
            base_fee="10.00",
            continue_fee="2.00",
            operation_fee="1.50",
            consumable_fee="0.50",
            ewaybill_fee="1.00",
            tax_fee="0.30",
            tax_point="10.00",
        )
        count_result = logistics_service.calculate_freight(
            db, template_id=count_template.id, count=5
        )
        assert count_result["additional_units"] == 2
        assert count_result["freight_fee"] == Decimal("14.00")
        assert count_result["percentage_tax"] == Decimal("1.70")
        assert count_result["tax_amount"] == Decimal("2.00")
        assert count_result["total_fee"] == Decimal("19.00")

        weight_template = _create_template(
            db,
            staff,
            logistics.id,
            "按重模板",
            charge_type="BY_WEIGHT",
            first_weight="1.000",
            continue_weight="0.500",
            deduct_weight="0.200",
            base_fee="8.00",
            continue_fee="1.50",
        )
        weight_result = logistics_service.calculate_freight(
            db, template_id=weight_template.id, weight="2.200"
        )
        assert weight_result["charge_weight"] == Decimal("2.000")
        assert weight_result["additional_units"] == 2
        assert weight_result["freight_fee"] == Decimal("11.00")

        with pytest.raises(BizException) as switch_exc:
            logistics_service.update_freight_template(
                db,
                template_id=count_template.id,
                data={"charge_type": "BY_WEIGHT"},
                staff=staff,
            )
        assert "首重" in switch_exc.value.message


def test_area_rule_priority_weight_and_no_match():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        logistics = _create_logistics(db, staff)
        general_template = _create_template(db, staff, logistics.id, "通用模板")
        province_template = _create_template(db, staff, logistics.id, "浙江模板")
        city_template = _create_template(db, staff, logistics.id, "杭州模板")
        city_only_template = _create_template(db, staff, logistics.id, "仅市模板")

        logistics_service.create_area_rule(
            db,
            data={
                "logistics_id": logistics.id,
                "freight_template_id": general_template.id,
            },
            staff=staff,
        )
        logistics_service.create_area_rule(
            db,
            data={
                "logistics_id": logistics.id,
                "province": "浙江省",
                "freight_template_id": province_template.id,
            },
            staff=staff,
        )
        logistics_service.create_area_rule(
            db,
            data={
                "logistics_id": logistics.id,
                "city": "宁波市",
                "freight_template_id": city_only_template.id,
            },
            staff=staff,
        )
        logistics_service.create_area_rule(
            db,
            data={
                "logistics_id": logistics.id,
                "province": "浙江省",
                "city": "杭州市",
                "match_weight_min": "1.000",
                "match_weight_max": "5.000",
                "freight_template_id": city_template.id,
            },
            staff=staff,
        )

        city_match = logistics_service.match_area_rule(
            db,
            logistics_id=logistics.id,
            province="浙江省",
            city="杭州市",
            weight="2.000",
        )
        assert city_match["matched"] is True
        assert city_match["freight_template"]["name"] == "杭州模板"

        province_match = logistics_service.match_area_rule(
            db,
            logistics_id=logistics.id,
            province="浙江省",
            city="温州市",
            weight="2.000",
        )
        assert province_match["freight_template"]["name"] == "浙江模板"

        city_only_match = logistics_service.match_area_rule(
            db,
            logistics_id=logistics.id,
            province="浙江省",
            city="宁波市",
            weight="2.000",
        )
        assert city_only_match["matched"] is True
        assert city_only_match["area_rule"]["province"] is None
        assert city_only_match["area_rule"]["city"] == "宁波市"
        assert city_only_match["freight_template"]["name"] == "仅市模板"

        general_match = logistics_service.match_area_rule(
            db,
            logistics_id=logistics.id,
            province="江苏省",
            city="苏州市",
            weight="2.000",
        )
        assert general_match["freight_template"]["name"] == "通用模板"

        empty_logistics = _create_logistics(db, staff, "无规则物流")
        no_match = logistics_service.match_area_rule(
            db,
            logistics_id=empty_logistics.id,
            province="浙江省",
            city="杭州市",
            weight="2.000",
        )
        assert no_match["matched"] is False
        assert no_match["freight_template"] is None

        with pytest.raises(BizException) as weight_exc:
            logistics_service.create_area_rule(
                db,
                data={
                    "logistics_id": logistics.id,
                    "match_weight_min": "5.000",
                    "match_weight_max": "1.000",
                },
                staff=staff,
            )
        assert "不能大于" in weight_exc.value.message

        other_logistics = _create_logistics(db, staff, "另一物流")
        with pytest.raises(BizException) as template_exc:
            logistics_service.create_area_rule(
                db,
                data={
                    "logistics_id": other_logistics.id,
                    "freight_template_id": general_template.id,
                },
                staff=staff,
            )
        assert "不属于" in template_exc.value.message


def test_print_template_default_mutex_and_validation():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        logistics = _create_logistics(db, staff)
        first = logistics_service.create_print_template(
            db,
            data={
                "name": "拣货单A",
                "template_type": "PICK_LIST",
                "logistics_id": logistics.id,
                "paper_width": "100.00",
                "paper_height": "150.00",
                "is_default": True,
            },
            staff=staff,
        )
        second = logistics_service.create_print_template(
            db,
            data={
                "name": "拣货单B",
                "template_type": "PICK_LIST",
                "logistics_id": logistics.id,
                "is_default": True,
            },
            staff=staff,
        )
        assert db.get(type(first), first.id).is_default is False
        assert second.is_default is True

        other_type = logistics_service.create_print_template(
            db,
            data={
                "name": "配货单A",
                "template_type": "DELIVERY_LIST",
                "logistics_id": logistics.id,
                "is_default": True,
            },
            staff=staff,
        )
        assert other_type.is_default is True
        assert second.is_default is True

        with pytest.raises(BizException) as exc:
            logistics_service.create_print_template(
                db,
                data={
                    "name": "错误纸张",
                    "template_type": "PICK_LIST",
                    "paper_width": "0",
                },
                staff=staff,
            )
        assert "大于 0" in exc.value.message


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
    app.include_router(logistics_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def _seed_roles(Session):
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add_all(
            [
                Staff(id=1, company_id=1, name="仓管员", phone="13900000001"),
                Staff(id=9, company_id=1, name="管理员", phone="13900000009"),
            ]
        )
        db.add_all(
            [
                Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
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


def test_api_logistics_role_can_read_but_not_manage():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    listed = client.get("/logistics", headers=_auth(1))
    assert listed.status_code == 200
    assert listed.json()["data"] == []

    denied = client.post(
        "/logistics",
        json={"name": "顺丰"},
        headers=_auth(1),
    )
    assert denied.status_code == 403


def test_api_admin_creates_freight_template_and_calculates():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)
    headers = _auth(9, "管理员")

    logistics = client.post(
        "/logistics",
        json={"name": "顺丰", "company": "顺丰速运"},
        headers=headers,
    )
    assert logistics.status_code == 200
    logistics_id = logistics.json()["data"]["id"]

    template = client.post(
        "/freight-templates",
        json={
            "name": "标准运费",
            "logistics_id": logistics_id,
            "charge_type": "BY_COUNT",
            "first_count": 1,
            "continue_count": 1,
            "base_fee": "6.00",
            "continue_fee": "1.50",
            "tax_point": "10.00",
        },
        headers=headers,
    )
    assert template.status_code == 200
    template_id = template.json()["data"]["id"]

    calculated = client.get(
        f"/freight-templates/{template_id}/calculate",
        params={"count": 2},
        headers=headers,
    )
    assert calculated.status_code == 200
    assert calculated.json()["data"]["freight_fee"] == "7.50"
    assert calculated.json()["data"]["percentage_tax"] == "0.75"
    assert calculated.json()["data"]["total_fee"] == "8.25"
