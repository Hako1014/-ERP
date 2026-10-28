from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.stock_in import router as stock_in_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Unit
from app.models.finance import Voucher, VoucherItem
from app.models.org import Company, Staff
from app.models.notification import NotificationOutbox
from app.models.party import Supplier
from app.models.product import Product, ProductSku
from app.models.purchase import PurchaseOrder, PurchaseOrderItem
from app.models.rbac import Role, StaffRole
from app.models.stock import Putaway, StockFlow
from app.models.warehouse import Location, StockLocation, Warehouse, Zone
from app.services import approval_service, stock_in_service, voucher_service


def _session_factory(pool: bool = False):
    kwargs = (
        {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        if pool
        else {}
    )
    engine = create_engine("sqlite:///:memory:", future=True, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db, *, capacity: int = 0) -> None:
    db.add(Company(id=1, full_name="示例公司"))
    db.add_all(
        [
            Staff(id=1, company_id=1, name="仓管员", phone="13900000001"),
            Staff(id=3, company_id=1, name="审核员", phone="13900000003"),
            Staff(id=9, company_id=1, name="管理员", phone="13900000009"),
        ]
    )
    db.add(Supplier(id=1, code="S0001", name="示例供应商", is_active=True))
    db.add(Unit(id=1, name="件", code="PCS"))
    db.add(
        Product(
            id=1,
            name="测试商品",
            code="P0001",
            main_unit_id=1,
            cost_price=Decimal("8.00"),
        )
    )
    db.add(
        ProductSku(
            id=1,
            product_id=1,
            spec_name="红色",
            spec_code="P0001-RED",
            barcode="690000000001",
            cost_price=Decimal("9.00"),
            is_active=True,
        )
    )
    db.add(
        Warehouse(
            id=1,
            code="WH01",
            name="主仓",
            warehouse_type="FINISHED",
            is_enabled=True,
            is_virtual=False,
            enable_location=True,
            lock_type="NONE",
        )
    )
    db.add(Zone(id=1, warehouse_id=1, code="MD02", name="MD02区"))
    db.add(
        Location(
            id=1,
            warehouse_id=1,
            zone_id=1,
            code="WH01-MD02区-01行-01列-01层",
            row_no=1,
            col_no=1,
            layer_no=1,
            capacity=capacity,
            is_enabled=True,
        )
    )
    db.add(
        PurchaseOrder(
            id=1,
            order_no="CGDD20260923-0001",
            supplier_id=1,
            warehouse_id=1,
            status="APPROVED",
            audit_status="APPROVED",
            total_qty=Decimal("10.000"),
            arrived_qty=Decimal("0.000"),
        )
    )
    db.flush()
    db.add(
        PurchaseOrderItem(
            id=1,
            order_id=1,
            product_id=1,
            sku_id=1,
            unit_id=1,
            purchase_price=Decimal("10.00"),
            qty=Decimal("10.000"),
            in_transit_qty=Decimal("10.000"),
            arrived_qty=Decimal("0.000"),
            amount=Decimal("100.00"),
        )
    )
    db.commit()
    approval_service.create_flow(
        db,
        name="采购入库审批",
        doc_type="stock_in",
        approvers=[3],
        actor=db.get(Staff, 1),
    )
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_stock_in(
    db,
    *,
    purchase_order_id: int | None = None,
    qty: str = "6.000",
    stock_in_type: str = "PURCHASE",
):
    return stock_in_service.create_stock_in(
        db,
        data={
            "warehouse_id": 1,
            "purchase_order_id": purchase_order_id,
            "stock_in_type": stock_in_type,
            "remark": "测试入库",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "qty": qty,
                }
            ],
        },
        staff=db.get(Staff, 1),
    )


def test_purchase_stock_in_approval_putaway_and_order_arrival(monkeypatch):
    monkeypatch.setattr(
        stock_in_service,
        "generate_serial",
        lambda prefix, dt=None: "RKD20260923-0001",
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        stock_in = _create_stock_in(
            db,
            purchase_order_id=1,
            qty="6.000",
        )
        assert stock_in.stock_in_no == "RKD20260923-0001"
        assert stock_in.total_qty == Decimal("6.000")

        stock_in_service.submit_stock_in(
            db,
            stock_in_id=stock_in.id,
            staff=db.get(Staff, 1),
        )
        approved = stock_in_service.approve_stock_in(
            db,
            stock_in_id=stock_in.id,
            staff=db.get(Staff, 3),
            comment="同意",
        )
        assert approved.status == "APPROVED"
        arrival_event = db.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.event_code
                == "purchase_order.arrival_progress_changed"
            )
        )
        assert arrival_event is not None
        assert arrival_event.source_id == stock_in.id
        assert arrival_event.aggregate_id == 1
        order = db.get(PurchaseOrder, 1)
        assert order.arrived_qty == Decimal("6.000")
        assert order.status == "RECEIVING"

        voucher = db.scalar(
            select(Voucher).where(
                Voucher.reference == "采购入库单:RKD20260923-0001"
            )
        )
        assert voucher is not None
        assert voucher.status == "DRAFT"
        assert voucher.total_debit == Decimal("60.00")
        assert voucher.total_credit == Decimal("60.00")
        assert db.scalar(
            select(func.count(VoucherItem.id)).where(
                VoucherItem.voucher_id == voucher.id
            )
        ) == 2

        pending = stock_in_service.list_pending_putaways(db, warehouse_id=1)
        assert len(pending) == 1
        assert pending[0]["pending_putaway_qty"] == Decimal("6.000")

        detail = stock_in_service.stock_in_dict(db, approved, with_items=True)
        item_id = detail["items"][0]["id"]
        stock_in_service.putaway_stock(
            db,
            stock_in_id=stock_in.id,
            data={"items": [{"stock_in_item_id": item_id, "location_id": 1, "qty": "4"}]},
            staff=db.get(Staff, 1),
        )
        stock_location = db.scalar(
            select(StockLocation).where(
                StockLocation.location_id == 1,
                StockLocation.product_sku_id == 1,
            )
        )
        assert stock_location.qty == Decimal("4.000")
        assert db.scalar(select(func.count(StockFlow.id))) == 1
        pending = stock_in_service.list_pending_putaways(db, warehouse_id=1)
        assert pending[0]["pending_putaway_qty"] == Decimal("2.000")

        stock_in_service.putaway_stock(
            db,
            stock_in_id=stock_in.id,
            data={"items": [{"stock_in_item_id": item_id, "location_id": 1, "qty": "2"}]},
            staff=db.get(Staff, 1),
        )
        assert stock_location.qty == Decimal("6.000")
        assert db.scalar(select(func.count(Putaway.id))) == 2
        assert stock_in_service.list_pending_putaways(db, warehouse_id=1) == []


def test_manual_stock_in_capacity_and_putaway_overrun(monkeypatch):
    monkeypatch.setattr(
        stock_in_service,
        "generate_serial",
        lambda prefix, dt=None: "RKD20260923-0002",
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db, capacity=5)
        stock_in = _create_stock_in(
            db,
            purchase_order_id=None,
            qty="6.000",
            stock_in_type="MANUAL",
        )
        stock_in_service.submit_stock_in(
            db,
            stock_in_id=stock_in.id,
            staff=db.get(Staff, 1),
        )
        stock_in_service.approve_stock_in(
            db,
            stock_in_id=stock_in.id,
            staff=db.get(Staff, 3),
        )
        voucher = db.scalar(
            select(Voucher).where(
                Voucher.reference == "采购入库单:RKD20260923-0002"
            )
        )
        assert voucher is not None
        voucher_detail = voucher_service.voucher_dict(db, voucher)
        assert [item["subject_code"] for item in voucher_detail["items"]] == [
            "1405",
            "1901",
        ]
        item_id = stock_in_service._stock_in_items(db, stock_in.id)[0].id

        with pytest.raises(BizException) as capacity_exc:
            stock_in_service.putaway_stock(
                db,
                stock_in_id=stock_in.id,
                data={
                    "items": [
                        {
                            "stock_in_item_id": item_id,
                            "location_id": 1,
                            "qty": "6",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "容量不足" in capacity_exc.value.message
        assert db.scalar(select(func.count(StockLocation.id))) == 0
        assert db.scalar(select(func.count(StockFlow.id))) == 0

        stock_in_service.putaway_stock(
            db,
            stock_in_id=stock_in.id,
            data={"items": [{"stock_in_item_id": item_id, "location_id": 1, "qty": "5"}]},
            staff=db.get(Staff, 1),
        )
        with pytest.raises(BizException) as pending_exc:
            stock_in_service.putaway_stock(
                db,
                stock_in_id=stock_in.id,
                data={
                    "items": [
                        {
                            "stock_in_item_id": item_id,
                            "location_id": 1,
                            "qty": "2",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "待上架数量" in pending_exc.value.message


def test_stock_in_validation_edit_and_delete_rules(monkeypatch):
    serials = iter(
        [
            "RKD20260923-0010",
            "RKD20260923-0011",
        ]
    )
    monkeypatch.setattr(
        stock_in_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        with pytest.raises(BizException) as over_exc:
            _create_stock_in(db, purchase_order_id=1, qty="11.000")
        assert "未到货数量" in over_exc.value.message

        stock_in = _create_stock_in(
            db,
            purchase_order_id=None,
            qty="2.000",
            stock_in_type="MANUAL",
        )
        updated = stock_in_service.update_stock_in(
            db,
            stock_in_id=stock_in.id,
            data={
                "remark": "已修改",
                "items": [
                    {
                        "product_id": 1,
                        "sku_id": 1,
                        "qty": "3.000",
                        "cost_price": "9.50",
                    }
                ],
            },
            staff=db.get(Staff, 1),
        )
        assert updated.total_qty == Decimal("3.000")

        stock_in_service.submit_stock_in(
            db,
            stock_in_id=stock_in.id,
            staff=db.get(Staff, 1),
        )
        with pytest.raises(BizException) as edit_exc:
            stock_in_service.update_stock_in(
                db,
                stock_in_id=stock_in.id,
                data={"remark": "审批中修改"},
                staff=db.get(Staff, 1),
            )
        assert "审批流程" in edit_exc.value.message
        with pytest.raises(BizException) as delete_exc:
            stock_in_service.delete_stock_in(
                db,
                stock_in_id=stock_in.id,
                staff=db.get(Staff, 1),
            )
        assert "审批流程" in delete_exc.value.message


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
    app.include_router(stock_in_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def _seed_roles(Session):
    with Session() as db:
        _seed(db)
        db.add(Staff(id=10, company_id=1, name="质检员", phone="13900000010"))
        db.add_all(
            [
                Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
                Role(id=2, company_id=1, code="QUALITY", name="质检"),
                Role(id=3, company_id=1, code="ADMIN", name="管理员"),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=10, role_id=2),
                StaffRole(staff_id=3, role_id=3),
                StaffRole(staff_id=9, role_id=3),
            ]
        )
        db.commit()


def test_api_stock_in_permissions_approval_and_putaway(monkeypatch):
    monkeypatch.setattr(
        stock_in_service,
        "generate_serial",
        lambda prefix, dt=None: "RKD20260923-0100",
    )
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)
    warehouse_headers = _auth(1, "仓管员")
    admin_headers = _auth(3, "管理员")

    created = client.post(
        "/stock-ins",
        json={
            "warehouse_id": 1,
            "purchase_order_id": 1,
            "stock_in_type": "PURCHASE",
            "items": [{"product_id": 1, "sku_id": 1, "qty": "2.000"}],
        },
        headers=warehouse_headers,
    )
    assert created.status_code == 200
    stock_in_id = created.json()["data"]["id"]
    item_id = created.json()["data"]["items"][0]["id"]

    denied = client.post(
        "/stock-ins",
        json={
            "warehouse_id": 1,
            "stock_in_type": "MANUAL",
            "items": [{"product_id": 1, "sku_id": 1, "qty": "1"}],
        },
        headers=_auth(10, "质检员"),
    )
    assert denied.status_code == 403

    submitted = client.post(
        f"/stock-ins/{stock_in_id}/submit",
        headers=warehouse_headers,
    )
    assert submitted.status_code == 200

    approved = client.post(
        f"/stock-ins/{stock_in_id}/approve",
        json={"comment": "同意"},
        headers=admin_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"

    pending = client.get(
        "/stock-ins/putaway/pending?warehouse_id=1",
        headers=warehouse_headers,
    )
    assert pending.status_code == 200
    assert pending.json()["data"][0]["pending_putaway_qty"] == "2.000"

    putaway = client.post(
        f"/stock-ins/{stock_in_id}/putaway",
        json={
            "items": [
                {
                    "stock_in_item_id": item_id,
                    "location_id": 1,
                    "qty": "2.000",
                }
            ]
        },
        headers=warehouse_headers,
    )
    assert putaway.status_code == 200
    assert putaway.json()["data"][0]["qty"] == "2.000"

    history = client.get("/putaways?stock_in_id=" + str(stock_in_id), headers=warehouse_headers)
    assert history.status_code == 200
    assert len(history.json()["data"]) == 1
