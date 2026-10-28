from datetime import datetime, timedelta
from decimal import Decimal
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.purchase_plan import router as purchase_plan_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.basic import Brand
from app.models.order import Order, OrderItem
from app.models.org import Company, Staff
from app.models.party import Supplier, SupplierPrice
from app.models.platform import Platform, Shop
from app.models.product import Product, ProductSku
from app.models.purchase import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasePlan,
    PurchasePlanItem,
)
from app.models.rbac import Role, StaffRole
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, purchase_plan_service


def _session_factory(pool: bool = False):
    kwargs = (
        {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        if pool
        else {}
    )
    engine = create_engine("sqlite:///:memory:", future=True, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db, *, as_of: datetime | None = None) -> dict:
    now = as_of or datetime(2026, 9, 23, 10, 0, 0)
    db.add(Company(id=1, full_name="示例公司"))
    db.add_all(
        [
            Staff(id=1, company_id=1, name="采购员", phone="13900000001"),
            Staff(id=2, company_id=1, name="采购主管", phone="13900000002"),
            Staff(id=9, company_id=1, name="管理员", phone="13900000009"),
        ]
    )
    db.add(Supplier(id=1, code="S0001", name="示例供应商", is_active=True))
    db.add(Brand(id=1, name="示例品牌"))
    db.add_all(
        [
            Product(
                id=1,
                name="测试商品A",
                code="P0001",
                art_no="ART-1",
                brand_id=1,
                default_supplier_id=1,
                cost_price=Decimal("9.00"),
            ),
            Product(
                id=2,
                name="测试商品B",
                code="P0002",
                art_no="ART-2",
                brand_id=1,
                default_supplier_id=1,
                cost_price=Decimal("7.00"),
            ),
        ]
    )
    db.add_all(
        [
            ProductSku(
                id=1,
                product_id=1,
                spec_name="红色",
                spec_code="P0001-RED",
                cost_price=Decimal("8.50"),
                is_active=True,
            ),
            ProductSku(
                id=2,
                product_id=2,
                spec_name="蓝色",
                spec_code="P0002-BLUE",
                cost_price=Decimal("6.50"),
                is_active=True,
            ),
        ]
    )
    db.add_all(
        [
            SupplierPrice(
                supplier_id=1,
                product_id=1,
                sku_id=1,
                price=Decimal("10.00"),
                status="ACTIVE",
            ),
            SupplierPrice(
                supplier_id=1,
                product_id=2,
                sku_id=2,
                price=Decimal("8.00"),
                status="ACTIVE",
            ),
        ]
    )
    db.add(Warehouse(id=1, code="WH01", name="主仓", is_enabled=True))
    db.add(Location(id=1, warehouse_id=1, code="A-01", is_enabled=True))
    db.add_all(
        [
            StockLocation(
                location_id=1,
                product_sku_id=1,
                qty=Decimal("3.000"),
                status="NORMAL",
            ),
            StockLocation(
                location_id=1,
                product_sku_id=2,
                qty=Decimal("5.000"),
                status="NORMAL",
            ),
        ]
    )
    db.add(Platform(id=1, name="淘宝", code="TAOBAO"))
    db.add(Shop(id=1, platform_id=1, name="测试店"))

    db.add_all(
        [
            Order(
                id=1,
                order_no="DD-PAID-1",
                shop_id=1,
                status="COMPLETED",
                pay_status="PAID",
                order_time=now - timedelta(days=2),
                pay_time=now - timedelta(days=2),
            ),
            Order(
                id=2,
                order_no="DD-CANCELLED",
                shop_id=1,
                status="CANCELLED",
                pay_status="PAID",
                order_time=now - timedelta(days=2),
                pay_time=now - timedelta(days=2),
            ),
            Order(
                id=3,
                order_no="DD-OLD",
                shop_id=1,
                status="COMPLETED",
                pay_status="PAID",
                order_time=now - timedelta(days=30),
                pay_time=now - timedelta(days=30),
            ),
        ]
    )
    db.add_all(
        [
            OrderItem(
                order_id=1,
                product_id=1,
                sku_id=1,
                qty=Decimal("12.000"),
                price=Decimal("20.00"),
                amount=Decimal("240.00"),
            ),
            OrderItem(
                order_id=1,
                product_id=2,
                sku_id=2,
                qty=Decimal("2.000"),
                price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
            OrderItem(
                order_id=2,
                product_id=1,
                sku_id=1,
                qty=Decimal("7.000"),
                price=Decimal("20.00"),
                amount=Decimal("140.00"),
            ),
            OrderItem(
                order_id=3,
                product_id=1,
                sku_id=1,
                qty=Decimal("5.000"),
                price=Decimal("20.00"),
                amount=Decimal("100.00"),
            ),
        ]
    )
    db.add(
        PurchaseOrder(
            id=1,
            order_no="CGDD-IN-TRANSIT",
            supplier_id=1,
            status="APPROVED",
            audit_status="APPROVED",
        )
    )
    db.add(
        PurchaseOrderItem(
            order_id=1,
            product_id=1,
            sku_id=1,
            qty=Decimal("3.000"),
            arrived_qty=Decimal("1.000"),
        )
    )
    db.add(
        PurchasePlan(
            id=1,
            plan_no="CGJH-IN-PROGRESS",
            supplier_id=1,
            status="IN_PROGRESS",
            audit_status="PENDING",
            created_by=1,
        )
    )
    db.add(
        PurchasePlanItem(
            plan_id=1,
            product_id=1,
            sku_id=1,
            planned_qty=Decimal("2.000"),
            purchased_qty=Decimal("1.000"),
            purchase_price=Decimal("10.00"),
            amount=Decimal("20.00"),
        )
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))
    return {"as_of": now, "supplier_id": 1}


def _preview_item(preview: dict, product_id: int, sku_id: int) -> dict:
    return next(
        item
        for item in preview["items"]
        if item["product_id"] == product_id and item["sku_id"] == sku_id
    )


def test_preview_purchase_needs_formula_and_in_progress_hint():
    Session = _session_factory()
    with Session() as db:
        seeded = _seed(db)
        db.add(
            Product(
                id=3,
                name="无销量商品",
                code="P0003",
                default_supplier_id=1,
                cost_price=Decimal("5.00"),
            )
        )
        db.add(
            ProductSku(
                id=3,
                product_id=3,
                spec_name="默认",
                spec_code="P0003-DEFAULT",
                is_active=True,
            )
        )
        db.commit()
        preview = purchase_plan_service.preview_purchase_needs(
            db, supplier_id=seeded["supplier_id"], as_of=seeded["as_of"]
        )

        first = _preview_item(preview, 1, 1)
        assert first["sales_20d"] == Decimal("12.000")
        assert first["current_stock"] == Decimal("3.000")
        assert first["in_transit_stock"] == Decimal("2.000")
        assert first["in_progress_qty"] == Decimal("1.000")
        assert first["suggested_qty"] == Decimal("7.000")
        assert first["purchase_price"] == Decimal("10.00")
        assert first["estimated_amount"] == Decimal("70.00")

        second = _preview_item(preview, 2, 2)
        assert second["sales_20d"] == Decimal("2.000")
        assert second["current_stock"] == Decimal("5.000")
        assert second["suggested_qty"] == Decimal("0.000")

        no_history = _preview_item(preview, 3, 3)
        assert no_history["sales_20d"] == Decimal("0")
        assert no_history["suggested_qty"] == Decimal("0")


def test_create_update_batch_and_delete_plan(monkeypatch):
    Session = _session_factory()
    monkeypatch.setattr(
        purchase_plan_service,
        "generate_serial",
        lambda prefix, dt=None: "CGJH20260923-0001",
    )
    with Session() as db:
        seeded = _seed(db)
        staff = db.get(Staff, 1)
        plan = purchase_plan_service.create_plan(
            db,
            data={
                "supplier_id": seeded["supplier_id"],
                "plan_date": seeded["as_of"],
                "remark": "自动测算后生成",
                "items": [
                    {
                        "product_id": 1,
                        "sku_id": 1,
                        "planned_qty": "7.000",
                    }
                ],
            },
            staff=staff,
        )
        assert plan.plan_no == "CGJH20260923-0001"
        assert plan.status == "IN_PROGRESS"
        assert plan.audit_status == "PENDING"
        assert plan.total_qty == Decimal("7.000")
        assert plan.total_amount == Decimal("70.00")

        detail = purchase_plan_service.plan_dict(db, plan, with_approval=True)
        assert detail["items"][0]["purchase_price"] == Decimal("10.00")
        assert detail["approval"]["state"]["status"] == "NOT_SUBMITTED"

        updated = purchase_plan_service.update_plan(
            db,
            plan_id=plan.id,
            data={"remark": "修改备注"},
            staff=staff,
        )
        assert updated.remark == "修改备注"

        item_id = detail["items"][0]["id"]
        updated = purchase_plan_service.batch_update_plan_items(
            db,
            plan_id=plan.id,
            data={
                "item_ids": [item_id],
                "planned_qty": "10.000",
                "purchase_price": "9.50",
            },
            staff=staff,
        )
        assert updated.total_qty == Decimal("10.000")
        assert updated.total_amount == Decimal("95.00")

        with pytest.raises(BizException) as duplicate_exc:
            purchase_plan_service.create_plan(
                db,
                data={
                    "supplier_id": seeded["supplier_id"],
                    "items": [
                        {"product_id": 1, "sku_id": 1, "planned_qty": "1"},
                        {"product_id": 1, "sku_id": 1, "planned_qty": "2"},
                    ],
                },
                staff=staff,
            )
        assert "不能重复" in duplicate_exc.value.message

        with pytest.raises(BizException) as sku_exc:
            purchase_plan_service.create_plan(
                db,
                data={
                    "supplier_id": seeded["supplier_id"],
                    "items": [
                        {"product_id": 1, "sku_id": 2, "planned_qty": "1"}
                    ],
                },
                staff=staff,
            )
        assert "规格不属于" in sku_exc.value.message

        purchase_plan_service.delete_plan(db, plan_id=plan.id, staff=staff)
        assert db.get(PurchasePlan, plan.id) is None


def test_plan_approval_unaudit_reject_abort_and_finish(monkeypatch):
    Session = _session_factory()
    serial_seq = iter(
        [
            "CGJH20260923-1001",
            "CGJH20260923-1002",
            "CGJH20260923-1003",
        ]
    )
    monkeypatch.setattr(
        purchase_plan_service,
        "generate_serial",
        lambda prefix, dt=None: next(serial_seq),
    )
    with Session() as db:
        seeded = _seed(db)
        purchaser = db.get(Staff, 1)
        approver = db.get(Staff, 2)
        approval_service.create_flow(
            db,
            name="采购计划审批",
            doc_type="purchase_plan",
            approvers=[2],
            actor=purchaser,
        )

        plan = purchase_plan_service.create_plan(
            db,
            data={
                "supplier_id": seeded["supplier_id"],
                "items": [
                    {"product_id": 1, "sku_id": 1, "planned_qty": "7"}
                ],
            },
            staff=purchaser,
        )
        plan = purchase_plan_service.submit_plan(
            db, plan_id=plan.id, staff=purchaser
        )
        assert plan.audit_status == "PENDING"
        assert purchase_plan_service.plan_dict(
            db, plan, with_approval=True
        )["approval"]["state"]["current_step"] == 1

        plan = purchase_plan_service.approve_plan(
            db, plan_id=plan.id, staff=approver, comment="同意"
        )
        assert plan.audit_status == "APPROVED"
        assert plan.auditor_id == approver.id

        with pytest.raises(BizException) as edit_exc:
            purchase_plan_service.batch_update_plan_items(
                db,
                plan_id=plan.id,
                data={"item_ids": [1], "planned_qty": "8"},
                staff=purchaser,
            )
        assert "不能修改" in edit_exc.value.message

        plan = purchase_plan_service.unaudit_plan(
            db,
            plan_id=plan.id,
            staff=purchaser,
            reason="需要调整采购数量",
        )
        assert plan.audit_status == "PENDING"
        plan = purchase_plan_service.approve_plan(
            db, plan_id=plan.id, staff=approver
        )
        assert plan.audit_status == "APPROVED"

        item = db.scalar(
            select(PurchasePlanItem).where(PurchasePlanItem.plan_id == plan.id)
        )
        item.purchased_qty = item.planned_qty
        db.commit()
        plan = purchase_plan_service.finish_plan(
            db, plan_id=plan.id, staff=purchaser
        )
        assert plan.status == "FINISHED"

        rejected = purchase_plan_service.create_plan(
            db,
            data={
                "supplier_id": seeded["supplier_id"],
                "items": [
                    {"product_id": 2, "sku_id": 2, "planned_qty": "2"}
                ],
            },
            staff=purchaser,
        )
        purchase_plan_service.submit_plan(
            db, plan_id=rejected.id, staff=purchaser
        )
        rejected = purchase_plan_service.reject_plan(
            db,
            plan_id=rejected.id,
            staff=approver,
            comment="价格不合适",
        )
        assert rejected.audit_status == "REJECTED"
        rejected_detail = purchase_plan_service.plan_dict(
            db, rejected, with_items=True
        )
        rejected = purchase_plan_service.batch_update_plan_items(
            db,
            plan_id=rejected.id,
            data={
                "item_ids": [rejected_detail["items"][0]["id"]],
                "purchase_price": "7.50",
            },
            staff=purchaser,
        )
        assert rejected.total_amount == Decimal("15.00")

        aborted = purchase_plan_service.create_plan(
            db,
            data={
                "supplier_id": seeded["supplier_id"],
                "items": [
                    {"product_id": 1, "sku_id": 1, "planned_qty": "1"}
                ],
            },
            staff=purchaser,
        )
        aborted = purchase_plan_service.abort_plan(
            db, plan_id=aborted.id, staff=purchaser
        )
        assert aborted.status == "ABORTED"


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
    app.include_router(purchase_plan_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def _seed_roles(Session):
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="PURCHASER", name="采购员"),
                Role(id=2, company_id=1, code="VP", name="副总"),
                Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=2, role_id=2),
                StaffRole(staff_id=9, role_id=3),
            ]
        )
        db.commit()
        approval_service.create_flow(
            db,
            name="采购计划审批",
            doc_type="purchase_plan",
            approvers=[2],
            actor=db.get(Staff, 1),
        )


def test_api_purchase_plan_permissions_and_approval(monkeypatch):
    monkeypatch.setattr(
        purchase_plan_service,
        "generate_serial",
        lambda prefix, dt=None: "CGJH20260923-2001",
    )
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    preview = client.get(
        "/purchase-plans/needs",
        params={"supplier_id": 1},
        headers=_auth(1, "采购员"),
    )
    assert preview.status_code == 200
    assert preview.json()["data"]["items_count"] == 2

    created = client.post(
        "/purchase-plans",
        json={
            "supplier_id": 1,
            "items": [
                {"product_id": 1, "sku_id": 1, "planned_qty": "7"}
            ],
        },
        headers=_auth(1, "采购员"),
    )
    assert created.status_code == 200
    plan_id = created.json()["data"]["id"]

    denied = client.post(
        f"/purchase-plans/{plan_id}/approve",
        json={"comment": "同意"},
        headers=_auth(1, "采购员"),
    )
    assert denied.status_code == 403

    submitted = client.post(
        f"/purchase-plans/{plan_id}/submit",
        headers=_auth(1, "采购员"),
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["approval"]["state"]["current_step"] == 1

    approved = client.post(
        f"/purchase-plans/{plan_id}/approve",
        json={"comment": "同意"},
        headers=_auth(2, "副总"),
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["audit_status"] == "APPROVED"


def test_purchase_plan_idempotency_replays_result_and_scopes_identity(monkeypatch):
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        monkeypatch.setattr(
            purchase_plan_service,
            "generate_serial",
            lambda prefix, dt=None: "CGJH20260923-IDEMPOTENT",
        )
        payload = {
            "supplier_id": 1,
            "items": [{"product_id": 1, "sku_id": 1, "planned_qty": "7"}],
        }
        first = purchase_plan_service.create_plan_idempotent(
            db,
            data=payload,
            staff=staff,
            request_id="6a531d78-3077-40ca-9d0a-8f947848bf2b",
            assistant_draft_id="00000000-0000-4000-8000-000000000086",
        )
        replay = purchase_plan_service.create_plan_idempotent(
            db,
            data=payload,
            staff=staff,
            request_id="6a531d78-3077-40ca-9d0a-8f947848bf2b",
            assistant_draft_id="00000000-0000-4000-8000-000000000086",
        )
        assert replay == first
        assert db.query(PurchasePlan).count() == 2  # 含 _seed 写入的既有测试计划
        assert purchase_plan_service.get_creation_request_result(
            db,
            staff=staff,
            request_id="6a531d78-3077-40ca-9d0a-8f947848bf2b",
        ) == {"status": "SUCCEEDED", "result": first, "message": None}
        assert purchase_plan_service.get_creation_request_result(
            db,
            staff=db.get(Staff, 2),
            request_id="6a531d78-3077-40ca-9d0a-8f947848bf2b",
        ) == {"status": "NOT_FOUND", "result": None, "message": None}

        changed = {
            **payload,
            "items": [{"product_id": 1, "sku_id": 1, "planned_qty": "8"}],
        }
        with pytest.raises(BizException, match="不同的表单内容"):
            purchase_plan_service.create_plan_idempotent(
                db,
                data=changed,
                staff=staff,
                request_id="6a531d78-3077-40ca-9d0a-8f947848bf2b",
            )

        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.target_type == "purchase_plan",
                AuditLog.target_id == str(first["id"]),
            )
        )
        audit_detail = json.loads(audit.detail)
        assert audit.actor_id == staff.id
        assert audit.actor_name == staff.name
        assert audit_detail["source"] == "assistant_draft"
        assert audit_detail["assistant_draft_id"] == "00000000-0000-4000-8000-000000000086"
        assert audit_detail["request_id"] == "6a531d78-3077-40ca-9d0a-8f947848bf2b"


def test_idempotent_validation_failure_is_recorded_without_partial_document():
    Session = _session_factory()
    request_id = "42cf2366-4c4a-47b3-b534-075bcc1b3f12"
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        payload = {
            "supplier_id": 999,
            "items": [{"product_id": 1, "sku_id": 1, "planned_qty": "3"}],
        }
        with pytest.raises(BizException, match="供应商不存在"):
            purchase_plan_service.create_plan_idempotent(
                db, data=payload, staff=staff, request_id=request_id
            )
        assert db.query(PurchasePlan).count() == 1
        assert purchase_plan_service.get_creation_request_result(
            db, staff=staff, request_id=request_id
        )["status"] == "FAILED"
        with pytest.raises(BizException, match="供应商不存在"):
            purchase_plan_service.create_plan_idempotent(
                db, data=payload, staff=staff, request_id=request_id
            )


def test_idempotent_creation_rolls_back_plan_and_can_resume_pending_request(monkeypatch):
    Session = _session_factory()
    request_id = "42cf2366-4c4a-47b3-b534-075bcc1b3f11"
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        payload = {
            "supplier_id": 1,
            "items": [{"product_id": 1, "sku_id": 1, "planned_qty": "3"}],
        }
        original_plan_dict = purchase_plan_service.plan_dict

        def fail_after_business_rows(*args, **kwargs):
            raise RuntimeError("injected response serialization failure")

        monkeypatch.setattr(purchase_plan_service, "plan_dict", fail_after_business_rows)
        with pytest.raises(RuntimeError, match="injected"):
            purchase_plan_service.create_plan_idempotent(
                db, data=payload, staff=staff, request_id=request_id
            )
        assert db.query(PurchasePlan).count() == 1
        request = db.scalar(
            select(purchase_plan_service.PurchasePlanCreationRequest).where(
                purchase_plan_service.PurchasePlanCreationRequest.request_id == request_id
            )
        )
        assert request.status == "PENDING"

        monkeypatch.setattr(purchase_plan_service, "plan_dict", original_plan_dict)
        monkeypatch.setattr(
            purchase_plan_service,
            "generate_serial",
            lambda prefix, dt=None: "CGJH20260923-RESUMED",
        )
        result = purchase_plan_service.create_plan_idempotent(
            db, data=payload, staff=staff, request_id=request_id
        )
        assert result["plan_no"] == "CGJH20260923-RESUMED"
        assert db.query(PurchasePlan).count() == 2


def test_api_idempotency_key_conflict_result_query_and_permission(monkeypatch):
    monkeypatch.setattr(
        purchase_plan_service,
        "generate_serial",
        lambda prefix, dt=None: "CGJH20260923-API-IDEMPOTENT",
    )
    app, Session = _make_api()
    _seed_roles(Session)
    with Session() as db:
        db.add(Staff(id=3, company_id=1, name="另一采购员", phone="13900000003"))
        db.flush()
        db.add(StaffRole(staff_id=3, role_id=1))
        db.commit()
    client = TestClient(app)
    request_id = "7ab09bdf-9e59-421e-bd91-ed9d95a6f178"
    payload = {
        "supplier_id": 1,
        "items": [{"product_id": 1, "sku_id": 1, "planned_qty": "4"}],
    }
    headers = {
        **_auth(1, "采购员"),
        "Idempotency-Key": request_id,
        "X-Assistant-Draft-Id": "20261010-0000000000860001",
    }
    first = client.post("/purchase-plans", json=payload, headers=headers)
    replay = client.post("/purchase-plans", json=payload, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["data"] == first.json()["data"]

    changed = {**payload, "items": [{"product_id": 1, "sku_id": 1, "planned_qty": "5"}]}
    conflict = client.post("/purchase-plans", json=changed, headers=headers)
    assert conflict.status_code == 409

    result = client.get(
        f"/purchase-plans/creation-requests/{request_id}",
        headers=_auth(1, "采购员"),
    )
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "SUCCEEDED"
    assert result.json()["data"]["result"] == first.json()["data"]

    other_staff = client.get(
        f"/purchase-plans/creation-requests/{request_id}",
        headers=_auth(3, "另一采购员"),
    )
    assert other_staff.status_code == 200
    assert other_staff.json()["data"]["status"] == "NOT_FOUND"
    revoked = client.get(
        f"/purchase-plans/creation-requests/{request_id}",
        headers=_auth(2, "副总"),
    )
    assert revoked.status_code == 403

    denied_create = client.post(
        "/purchase-plans", json=payload, headers=_auth(2, "副总")
    )
    assert denied_create.status_code == 403
    tampered = client.post(
        "/purchase-plans",
        json={**payload, "status": "APPROVED", "company_id": 2},
        headers=_auth(1, "采购员"),
    )
    assert tampered.status_code == 422
    with Session() as db:
        assert db.query(PurchasePlan).count() == 2


def test_purchase_plan_create_rechecks_permission_after_role_revocation():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)
    request_id = "01efce1d-7e62-4ff9-9a38-36c7e6c5e088"
    payload = {
        "supplier_id": 1,
        "items": [{"product_id": 1, "sku_id": 1, "planned_qty": "2"}],
    }

    # 模拟草稿形成后、用户点击保存前管理员撤销采购制单角色。
    with Session() as db:
        assignment = db.scalar(
            select(StaffRole).where(StaffRole.staff_id == 1, StaffRole.role_id == 1)
        )
        assert assignment is not None
        db.delete(assignment)
        db.commit()

    response = client.post(
        "/purchase-plans",
        json=payload,
        headers={**_auth(1, "采购员"), "Idempotency-Key": request_id},
    )
    assert response.status_code == 403
    with Session() as db:
        assert db.query(PurchasePlan).count() == 1  # 仅保留 _seed 的既有隔离记录
        assert db.query(PurchasePlanItem).count() == 1
        assert (
            db.query(purchase_plan_service.PurchasePlanCreationRequest)
            .filter_by(request_id=request_id)
            .count()
            == 0
        )
