from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.transfer import router as transfer_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.basic import Unit
from app.models.org import Company, Staff
from app.models.product import Product, ProductSku
from app.models.rbac import Role, StaffRole
from app.models.stock import (
    Putaway,
    StockFlow,
    Transfer,
    TransferIn,
    TransferInItem,
    TransferItem,
    TransferOut,
    TransferOutItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, transfer_service


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
            Staff(id=1, company_id=1, name="仓库", phone="13900000001"),
            Staff(id=2, company_id=1, name="审核员", phone="13900000002"),
            Staff(id=3, company_id=1, name="质检", phone="13900000003"),
        ]
    )
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add(
        Product(
            id=1,
            name="测试商品",
            code="P0001",
            main_unit_id=1,
            cost_price=Decimal("12.00"),
        )
    )
    db.add(
        ProductSku(
            id=1,
            product_id=1,
            spec_name="默认",
            spec_code="P0001-01",
            cost_price=Decimal("12.00"),
            is_active=True,
        )
    )
    db.add_all(
        [
            Warehouse(
                id=1,
                code="WH01",
                name="调出仓",
                warehouse_type="FINISHED",
                is_enabled=True,
                is_virtual=False,
                lock_type="NONE",
            ),
            Warehouse(
                id=2,
                code="WH02",
                name="调入仓",
                warehouse_type="FINISHED",
                is_enabled=True,
                is_virtual=False,
                lock_type="NONE",
            ),
            Warehouse(
                id=3,
                code="WH03",
                name="锁定仓",
                warehouse_type="FINISHED",
                is_enabled=True,
                is_virtual=False,
                lock_type="MANUAL",
            ),
        ]
    )
    db.add_all(
        [
            Location(id=1, warehouse_id=1, code="WH01-A01", is_enabled=True),
            Location(id=2, warehouse_id=1, code="WH01-A02", is_enabled=True),
            Location(id=3, warehouse_id=2, code="WH02-A01", is_enabled=True),
            Location(
                id=4,
                warehouse_id=2,
                code="WH02-A02",
                is_enabled=True,
            ),
            Location(id=5, warehouse_id=3, code="WH03-A01", is_enabled=True),
        ]
    )
    db.add_all(
        [
            StockLocation(
                id=1,
                location_id=1,
                product_sku_id=1,
                qty=Decimal("6.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=2,
                location_id=2,
                product_sku_id=1,
                qty=Decimal("4.000"),
                status="NORMAL",
            ),
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=2, company_id=1, code="ADMIN", name="管理员"),
            Role(id=3, company_id=1, code="QUALITY", name="质检"),
        ]
    )
    db.flush()
    db.add_all(
        [
            StaffRole(staff_id=1, role_id=1),
            StaffRole(staff_id=2, role_id=2),
            StaffRole(staff_id=3, role_id=3),
        ]
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flows(db) -> None:
    for name, doc_type in (
        ("调拨单审批", transfer_service.DOC_TYPE),
        ("调拨出库审批", transfer_service.OUT_DOC_TYPE),
        ("调拨入库审批", transfer_service.IN_DOC_TYPE),
    ):
        approval_service.create_flow(
            db,
            name=name,
            doc_type=doc_type,
            approvers=[2],
            actor=db.get(Staff, 1),
        )


def _patch_serial(monkeypatch) -> None:
    counters: dict[str, int] = {}

    def fake_serial(prefix, dt=None):
        code = prefix.value if hasattr(prefix, "value") else str(prefix)
        counters[code] = counters.get(code, 0) + 1
        return f"{code}20260923-{counters[code]:04d}"

    monkeypatch.setattr(transfer_service, "generate_serial", fake_serial)


def _create_transfer(
    db,
    *,
    qty: str = "5.000",
    sync_stock: bool = True,
    from_warehouse_id: int = 1,
    to_warehouse_id: int = 2,
) -> Transfer:
    return transfer_service.create_transfer(
        db,
        data={
            "from_warehouse_id": from_warehouse_id,
            "to_warehouse_id": to_warehouse_id,
            "sync_stock": sync_stock,
            "remark": "测试调拨",
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


def _approve_transfer(db, transfer_id: int) -> tuple[TransferOut, TransferIn]:
    transfer_service.submit_transfer(
        db,
        transfer_id=transfer_id,
        staff=db.get(Staff, 1),
    )
    transfer_service.approve_transfer(
        db,
        transfer_id=transfer_id,
        staff=db.get(Staff, 2),
        comment="同意",
    )
    transfer_out = db.scalar(
        select(TransferOut).where(TransferOut.transfer_id == transfer_id)
    )
    transfer_in = db.scalar(
        select(TransferIn).where(TransferIn.transfer_id == transfer_id)
    )
    assert transfer_out is not None
    assert transfer_in is not None
    return transfer_out, transfer_in


def _approve_out(db, transfer_out_id: int) -> TransferOut:
    transfer_service.submit_transfer_out(
        db,
        transfer_out_id=transfer_out_id,
        staff=db.get(Staff, 1),
    )
    return transfer_service.approve_transfer_out(
        db,
        transfer_out_id=transfer_out_id,
        staff=db.get(Staff, 2),
    )


def _approve_in(db, transfer_in_id: int) -> TransferIn:
    transfer_service.submit_transfer_in(
        db,
        transfer_in_id=transfer_in_id,
        staff=db.get(Staff, 1),
    )
    return transfer_service.approve_transfer_in(
        db,
        transfer_in_id=transfer_in_id,
        staff=db.get(Staff, 2),
    )


def test_transfer_full_flow_updates_both_warehouses_and_sync_flag(
    monkeypatch,
):
    _patch_serial(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        transfer = _create_transfer(db, qty="5.000", sync_stock=True)
        detail = transfer_service.transfer_dict(db, transfer)
        assert detail["status"] == "PENDING"
        assert detail["sync_stock"] is True
        assert detail["items"][0]["available_qty"] == Decimal("10.000")

        transfer_out, transfer_in = _approve_transfer(db, transfer.id)
        assert transfer_out.transfer_out_no.startswith("DBC20260923-")
        assert transfer_in.transfer_in_no.startswith("DBR20260923-")
        assert transfer_out.status == "PENDING"
        assert transfer_in.status == "PENDING"

        _approve_out(db, transfer_out.id)
        with pytest.raises(BizException) as early_in:
            transfer_service.submit_transfer_in(
                db,
                transfer_in_id=transfer_in.id,
                staff=db.get(Staff, 1),
            )
        assert "调拨出库完成后" in early_in.value.message

        out_items = list(
            db.scalars(
                select(TransferOutItem)
                .where(TransferOutItem.transfer_out_id == transfer_out.id)
                .order_by(TransferOutItem.id)
            )
        )
        transfer_service.off_shelf_transfer_out(
            db,
            transfer_out_id=transfer_out.id,
            data={
                "items": [
                    {
                        "transfer_out_item_id": out_items[0].id,
                        "location_id": 1,
                        "qty": "3.000",
                    },
                    {
                        "transfer_out_item_id": out_items[0].id,
                        "location_id": 2,
                        "qty": "2.000",
                    },
                ]
            },
            staff=db.get(Staff, 1),
        )
        assert db.get(Transfer, transfer.id).status == "OUTBOUND"
        assert db.get(TransferOut, transfer_out.id).status == "COMPLETED"
        assert db.get(StockLocation, 1).qty == Decimal("3.000")
        assert db.get(StockLocation, 2).qty == Decimal("2.000")
        assert db.get(StockLocation, 3) is None
        out_flows = list(
            db.scalars(
                select(StockFlow)
                .where(StockFlow.source_no == transfer_out.transfer_out_no)
                .order_by(StockFlow.location_id)
            )
        )
        assert [(flow.direction, flow.qty) for flow in out_flows] == [
            ("OUT", Decimal("3.000")),
            ("OUT", Decimal("2.000")),
        ]

        _approve_in(db, transfer_in.id)
        in_items = list(
            db.scalars(
                select(TransferInItem)
                .where(TransferInItem.transfer_in_id == transfer_in.id)
                .order_by(TransferInItem.id)
            )
        )
        transfer_service.receive_transfer_in(
            db,
            transfer_in_id=transfer_in.id,
            data={
                "items": [
                    {
                        "transfer_in_item_id": in_items[0].id,
                        "location_id": 3,
                        "qty": "2.000",
                    },
                    {
                        "transfer_in_item_id": in_items[0].id,
                        "location_id": 4,
                        "qty": "3.000",
                    },
                ]
            },
            staff=db.get(Staff, 1),
        )
        assert db.get(Transfer, transfer.id).status == "COMPLETED"
        assert db.get(TransferIn, transfer_in.id).status == "COMPLETED"
        assert db.get(StockLocation, 3).qty == Decimal("2.000")
        assert db.get(StockLocation, 4).qty == Decimal("3.000")
        assert (
            db.scalar(
                select(func.count(Putaway.id)).where(
                    Putaway.source_type == "TRANSFER_IN",
                    Putaway.source_id == transfer_in.id,
                )
            )
            == 2
        )
        in_flows = list(
            db.scalars(
                select(StockFlow)
                .where(StockFlow.source_no == transfer_in.transfer_in_no)
                .order_by(StockFlow.location_id)
            )
        )
        assert [(flow.direction, flow.qty) for flow in in_flows] == [
            ("IN", Decimal("2.000")),
            ("IN", Decimal("3.000")),
        ]
        assert (
            db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "SYNC_STOCK_REQUESTED",
                    AuditLog.target_id == str(transfer.id),
                )
            )
            == 1
        )


def test_outbound_validation_rolls_back_without_partial_stock(monkeypatch):
    _patch_serial(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        with pytest.raises(BizException) as over_create:
            _create_transfer(db, qty="11.000")
        assert "超过调出仓可用库存" in over_create.value.message

        transfer = _create_transfer(db, qty="6.000")
        transfer_out, _ = _approve_transfer(db, transfer.id)
        _approve_out(db, transfer_out.id)
        item = db.scalar(
            select(TransferOutItem).where(
                TransferOutItem.transfer_out_id == transfer_out.id
            )
        )

        with pytest.raises(BizException) as mismatch:
            transfer_service.off_shelf_transfer_out(
                db,
                transfer_out_id=transfer_out.id,
                data={
                    "items": [
                        {
                            "transfer_out_item_id": item.id,
                            "location_id": 1,
                            "qty": "5.000",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "合计必须等于" in mismatch.value.message

        with pytest.raises(BizException) as foreign:
            transfer_service.off_shelf_transfer_out(
                db,
                transfer_out_id=transfer_out.id,
                data={
                    "items": [
                        {
                            "transfer_out_item_id": item.id,
                            "location_id": 3,
                            "qty": "6.000",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "不属于当前仓库" in foreign.value.message

        with pytest.raises(BizException) as insufficient:
            transfer_service.off_shelf_transfer_out(
                db,
                transfer_out_id=transfer_out.id,
                data={
                    "items": [
                        {
                            "transfer_out_item_id": item.id,
                            "location_id": 2,
                            "qty": "6.000",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "超过库位库存" in insufficient.value.message

        assert db.get(StockLocation, 1).qty == Decimal("6.000")
        assert db.get(StockLocation, 2).qty == Decimal("4.000")
        assert db.scalar(select(func.count(StockFlow.id))) == 0
        assert db.get(Transfer, transfer.id).status == "APPROVED"
        assert db.get(TransferOut, transfer_out.id).status == "APPROVED"
        assert (
            db.scalar(
                select(func.count(TransferOutItem.id)).where(
                    TransferOutItem.transfer_out_id == transfer_out.id
                )
            )
            == 1
        )

        completed = transfer_service.off_shelf_transfer_out(
            db,
            transfer_out_id=transfer_out.id,
            data={
                "items": [
                    {
                        "transfer_out_item_id": item.id,
                        "location_id": 1,
                        "qty": "5.000",
                    },
                    {
                        "transfer_out_item_id": item.id,
                        "location_id": 2,
                        "qty": "1.000",
                    },
                ]
            },
            staff=db.get(Staff, 1),
        )
        assert completed.status == "COMPLETED"
        assert db.get(StockLocation, 1).qty == Decimal("1.000")
        assert db.get(StockLocation, 2).qty == Decimal("3.000")


def test_inbound_capacity_validation_is_atomic(monkeypatch):
    _patch_serial(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        db.get(Location, 3).capacity = 4
        db.commit()

        transfer = _create_transfer(db, qty="5.000")
        transfer_out, transfer_in = _approve_transfer(db, transfer.id)
        _approve_out(db, transfer_out.id)
        out_item = db.scalar(
            select(TransferOutItem).where(
                TransferOutItem.transfer_out_id == transfer_out.id
            )
        )
        transfer_service.off_shelf_transfer_out(
            db,
            transfer_out_id=transfer_out.id,
            data={
                "items": [
                    {
                        "transfer_out_item_id": out_item.id,
                        "location_id": 1,
                        "qty": "5.000",
                    }
                ]
            },
            staff=db.get(Staff, 1),
        )
        _approve_in(db, transfer_in.id)
        in_item = db.scalar(
            select(TransferInItem).where(
                TransferInItem.transfer_in_id == transfer_in.id
            )
        )

        with pytest.raises(BizException) as capacity:
            transfer_service.receive_transfer_in(
                db,
                transfer_in_id=transfer_in.id,
                data={
                    "items": [
                        {
                            "transfer_in_item_id": in_item.id,
                            "location_id": 3,
                            "qty": "5.000",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "容量不足" in capacity.value.message
        assert db.scalar(
            select(StockLocation).where(StockLocation.location_id == 3)
        ) is None
        assert db.scalar(select(func.count(Putaway.id))) == 0
        assert db.get(TransferIn, transfer_in.id).status == "APPROVED"
        assert db.get(Transfer, transfer.id).status == "OUTBOUND"

        transfer_service.receive_transfer_in(
            db,
            transfer_in_id=transfer_in.id,
            data={
                "items": [
                    {
                        "transfer_in_item_id": in_item.id,
                        "location_id": 3,
                        "qty": "4.000",
                    },
                    {
                        "transfer_in_item_id": in_item.id,
                        "location_id": 4,
                        "qty": "1.000",
                    },
                ]
            },
            staff=db.get(Staff, 1),
        )
        assert db.scalar(
            select(StockLocation).where(StockLocation.location_id == 3)
        ).qty == Decimal("4.000")
        assert db.scalar(
            select(StockLocation).where(StockLocation.location_id == 4)
        ).qty == Decimal("1.000")
        assert db.get(Transfer, transfer.id).status == "COMPLETED"


def test_transfer_crud_and_approval_rules(monkeypatch):
    _patch_serial(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)

        with pytest.raises(BizException) as same:
            _create_transfer(db, from_warehouse_id=1, to_warehouse_id=1)
        assert "不能相同" in same.value.message
        with pytest.raises(BizException) as locked:
            _create_transfer(db, from_warehouse_id=1, to_warehouse_id=3)
        assert "锁定" in locked.value.message
        with pytest.raises(BizException) as duplicate:
            transfer_service.create_transfer(
                db,
                data={
                    "from_warehouse_id": 1,
                    "to_warehouse_id": 2,
                    "items": [
                        {"product_id": 1, "sku_id": 1, "qty": "1.000"},
                        {"product_id": 1, "sku_id": 1, "qty": "2.000"},
                    ],
                },
                staff=db.get(Staff, 1),
            )
        assert "不能重复" in duplicate.value.message

        transfer = _create_transfer(db, qty="2.000", sync_stock=False)
        updated = transfer_service.update_transfer(
            db,
            transfer_id=transfer.id,
            data={
                "sync_stock": True,
                "remark": "已修改",
                "items": [{"product_id": 1, "sku_id": 1, "qty": "3.000"}],
            },
            staff=db.get(Staff, 1),
        )
        assert updated.sync_stock is True
        assert updated.remark == "已修改"
        assert updated.total_qty == Decimal("3.000")

        transfer_service.delete_transfer(
            db,
            transfer_id=updated.id,
            staff=db.get(Staff, 1),
        )
        assert db.get(Transfer, updated.id) is None

        rejected = _create_transfer(db, qty="2.000")
        transfer_service.submit_transfer(
            db,
            transfer_id=rejected.id,
            staff=db.get(Staff, 1),
        )
        with pytest.raises(BizException) as delete_submitted:
            transfer_service.delete_transfer(
                db,
                transfer_id=rejected.id,
                staff=db.get(Staff, 1),
            )
        assert "审批流程" in delete_submitted.value.message

        rejected = transfer_service.reject_transfer(
            db,
            transfer_id=rejected.id,
            staff=db.get(Staff, 2),
            comment="资料有误",
        )
        assert rejected.status == "REJECTED"
        updated = transfer_service.update_transfer(
            db,
            transfer_id=rejected.id,
            data={"remark": "驳回后修改"},
            staff=db.get(Staff, 1),
        )
        assert updated.remark == "驳回后修改"
        resubmitted = transfer_service.submit_transfer(
            db,
            transfer_id=updated.id,
            staff=db.get(Staff, 1),
        )
        assert resubmitted.status == "PENDING"


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
    app.include_router(transfer_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def test_api_transfer_permissions_and_complete_flow(monkeypatch):
    _patch_serial(monkeypatch)
    app, Session = _make_api()
    with Session() as db:
        _seed(db)
        _create_flows(db)

    client = TestClient(app)
    warehouse_headers = _auth(1, "仓库")
    admin_headers = _auth(2, "审核员")
    quality_headers = _auth(3, "质检")

    denied = client.post(
        "/transfers",
        json={
            "from_warehouse_id": 1,
            "to_warehouse_id": 2,
            "items": [{"product_id": 1, "sku_id": 1, "qty": "1"}],
        },
        headers=quality_headers,
    )
    assert denied.status_code == 403

    created = client.post(
        "/transfers",
        json={
            "from_warehouse_id": 1,
            "to_warehouse_id": 2,
            "sync_stock": True,
            "remark": "API 调拨",
            "items": [{"product_id": 1, "sku_id": 1, "qty": "5.000"}],
        },
        headers=warehouse_headers,
    )
    assert created.status_code == 200
    transfer_id = created.json()["data"]["id"]

    listed = client.get("/transfers", headers=quality_headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 0

    submitted = client.post(
        f"/transfers/{transfer_id}/submit",
        headers=warehouse_headers,
    )
    assert submitted.status_code == 200

    approved = client.post(
        f"/transfers/{transfer_id}/approve",
        json={"comment": "同意"},
        headers=admin_headers,
    )
    assert approved.status_code == 200
    approved_data = approved.json()["data"]
    assert approved_data["status"] == "APPROVED"
    transfer_out_id = approved_data["transfer_out"]["id"]
    transfer_in_id = approved_data["transfer_in"]["id"]
    out_item_id = approved_data["transfer_out"]["items"][0]["id"]
    in_item_id = approved_data["transfer_in"]["items"][0]["id"]

    client.post(
        f"/transfer-outs/{transfer_out_id}/submit",
        headers=warehouse_headers,
    )
    client.post(
        f"/transfer-outs/{transfer_out_id}/approve",
        json={"comment": "出库同意"},
        headers=admin_headers,
    )
    outbound = client.post(
        f"/transfer-outs/{transfer_out_id}/off-shelf",
        json={
            "items": [
                {
                    "transfer_out_item_id": out_item_id,
                    "location_id": 1,
                    "qty": "5.000",
                }
            ]
        },
        headers=warehouse_headers,
    )
    assert outbound.status_code == 200
    assert outbound.json()["data"]["status"] == "COMPLETED"

    client.post(
        f"/transfer-ins/{transfer_in_id}/submit",
        headers=warehouse_headers,
    )
    client.post(
        f"/transfer-ins/{transfer_in_id}/approve",
        json={"comment": "入库同意"},
        headers=admin_headers,
    )
    received = client.post(
        f"/transfer-ins/{transfer_in_id}/receive",
        json={
            "items": [
                {
                    "transfer_in_item_id": in_item_id,
                    "location_id": 3,
                    "qty": "5.000",
                }
            ]
        },
        headers=warehouse_headers,
    )
    assert received.status_code == 200
    assert received.json()["data"]["status"] == "COMPLETED"

    detail = client.get(
        f"/transfers/{transfer_id}",
        headers=quality_headers,
    )
    assert detail.status_code == 404
    detail = client.get(
        f"/transfers/{transfer_id}",
        headers=warehouse_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "COMPLETED"
    assert detail.json()["data"]["sync_stock"] is True

    with Session() as db:
        assert db.get(StockLocation, 1).qty == Decimal("1.000")
        assert db.scalar(
            select(StockLocation).where(StockLocation.location_id == 3)
        ).qty == Decimal("5.000")
        assert (
            db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "SYNC_STOCK_REQUESTED"
                )
            )
            == 1
        )
