import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.shop import router as shop_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.order import Order
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services import shop_service


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


def _make_platform_and_shop(db, staff: Staff):
    platform = shop_service.create_platform(
        db, data={"name": "淘宝", "code": "TAOBAO"}, staff=staff
    )
    shop = shop_service.create_shop(
        db,
        data={
            "platform_id": platform.id,
            "name": "淘宝旗舰店",
            "login_account": "旗舰店账号",
            "url": "https://shop.example.com",
            "tag": "直营",
            "default_address": "杭州市余杭区",
            "auto_sync_order": True,
            "download_mode": "MATCH_PRODUCT",
            "message_match": "留言包含加急时匹配顺丰",
        },
        staff=staff,
    )
    return platform, shop


def test_platform_shop_crud_filters_and_lock_switch():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        platform, shop = _make_platform_and_shop(db, staff)

        assert shop_service.platform_dict(platform)["code"] == "TAOBAO"
        assert shop.auto_sync_order is True
        assert shop.download_mode == "MATCH_PRODUCT"

        with pytest.raises(BizException) as duplicate_exc:
            shop_service.create_platform(
                db, data={"name": "淘宝重复", "code": "TAOBAO"}, staff=staff
            )
        assert "平台编码已存在" in duplicate_exc.value.message

        with pytest.raises(BizException) as delete_platform_exc:
            shop_service.delete_platform(
                db, platform_id=platform.id, staff=staff
            )
        assert "店铺" in delete_platform_exc.value.message

        result = shop_service.list_shops(
            db,
            platform_id=platform.id,
            keyword="旗舰",
            auto_sync_order=True,
        )
        assert result["total"] == 1

        updated = shop_service.update_shop(
            db,
            shop_id=shop.id,
            data={"name": "淘宝旗舰店二店", "tag": "分销"},
            staff=staff,
        )
        assert updated.name == "淘宝旗舰店二店"
        assert updated.tag == "分销"

        locked = shop_service.set_shop_locked(
            db, shop_id=shop.id, locked=True, staff=staff
        )
        assert locked.is_locked is True
        assert shop_service.list_shops(db, is_locked=True)["total"] == 1

        unlocked = shop_service.set_shop_locked(
            db, shop_id=shop.id, locked=False, staff=staff
        )
        assert unlocked.is_locked is False

        shop_service.delete_shop(db, shop_id=shop.id, staff=staff)
        shop_service.delete_platform(db, platform_id=platform.id, staff=staff)
        assert shop_service.list_shops(db)["total"] == 0
        assert shop_service.list_platforms(db) == []


def test_shop_api_secret_not_returned_and_partial_update_keeps_it():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        _, shop = _make_platform_and_shop(db, staff)

        api = shop_service.upsert_shop_api(
            db,
            shop_id=shop.id,
            data={
                "api_code": "API-001",
                "verify_code": "VERIFY-001",
                "app_key": "KEY-001",
                "app_secret": "SECRET-001",
            },
            staff=staff,
        )
        detail = shop_service.shop_api_dict(api)
        assert detail["has_app_secret"] is True
        assert "app_secret" not in detail

        updated = shop_service.upsert_shop_api(
            db,
            shop_id=shop.id,
            data={"app_key": "KEY-002"},
            staff=staff,
        )
        assert updated.app_key == "KEY-002"
        assert updated.app_secret == "SECRET-001"
        assert shop_service.shop_dict(db, shop, with_api=True)["api_configured"] is True

        shop_service.delete_shop_api(db, shop_id=shop.id, staff=staff)
        assert shop_service.get_shop_api(db, shop.id) is None


def test_shop_delete_blocked_when_referenced_by_order():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        _, shop = _make_platform_and_shop(db, staff)
        db.add(Order(order_no="ORDER-001", shop_id=shop.id))
        db.commit()

        with pytest.raises(BizException) as exc:
            shop_service.delete_shop(db, shop_id=shop.id, staff=staff)
        assert "不能删除" in exc.value.message
        assert "order" in exc.value.message


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
    app.include_router(shop_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def _seed_roles(Session):
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add_all(
            [
                Staff(id=1, company_id=1, name="采购员", phone="13900000001"),
                Staff(id=9, company_id=1, name="管理员", phone="13900000009"),
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


def test_api_shop_read_and_write_permissions():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    denied_read = client.get("/shops", headers=_auth(1))
    assert denied_read.status_code == 403

    denied_write = client.post(
        "/platforms",
        json={"name": "淘宝"},
        headers=_auth(1),
    )
    assert denied_write.status_code == 403


def test_api_admin_manages_platform_shop_and_api_config():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)
    headers = _auth(9, "管理员")

    platform = client.post(
        "/platforms",
        json={"name": "拼多多", "code": "PDD"},
        headers=headers,
    )
    assert platform.status_code == 200
    platform_id = platform.json()["data"]["id"]

    shop = client.post(
        "/shops",
        json={
            "platform_id": platform_id,
            "name": "拼多多店铺",
            "auto_sync_order": True,
        },
        headers=headers,
    )
    assert shop.status_code == 200
    shop_id = shop.json()["data"]["id"]

    api = client.put(
        f"/shops/{shop_id}/api",
        json={"app_key": "KEY-001", "app_secret": "SECRET-001"},
        headers=headers,
    )
    assert api.status_code == 200
    assert api.json()["data"]["has_app_secret"] is True
    assert "app_secret" not in api.json()["data"]

    synced = client.post(
        f"/shops/{shop_id}/sync-stock",
        json={"enabled": True},
        headers=headers,
    )
    assert synced.status_code == 200
    assert synced.json()["data"]["auto_sync_stock"] is True

    locked = client.post(f"/shops/{shop_id}/lock", headers=headers)
    assert locked.status_code == 200
    assert locked.json()["data"]["is_locked"] is True

    detail = client.get(f"/shops/{shop_id}", headers=headers)
    assert detail.json()["data"]["api_config"]["has_app_secret"] is True
    assert "app_secret" not in detail.json()["data"]["api_config"]
