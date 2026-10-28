"""TASK-085：结构化解析、权限隔离、草稿 CAS 与无业务写入回归。"""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import assistant as assistant_api
from app.core.config import Settings
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.base import Base, CompanyOwnedBase
from app.db.redis import get_redis
from app.db.session import get_db
from app.integrations.assistant_provider import ProviderToolCall, ProviderTurn, ProviderUsage
from app.models.audit_log import AuditLog
from app.models.basic import Unit, UnitGroup, UnitGroupItem
from app.models.assistant_governance import AssistantUsage
from app.models.org import Company, Staff
from app.models.party import Supplier, SupplierPrice
from app.models.product import Product, ProductSku
from app.models.purchase import PurchasePlan, PurchasePlanItem
from app.models.rbac import Role, StaffRole
from app.services import assistant_drafts


class DraftRedis:
    """内存 Redis 替身：执行配额租约和草稿 CAS 使用的有限 Lua 契约。"""

    def __init__(self):
        self.values: dict[str, str | int] = {}
        self.active: dict[str, dict[str, float]] = {}

    def get(self, key):
        value = self.values.get(key)
        return str(value) if value is not None else None

    def eval(self, script, numkeys, *items):
        keys, args = list(items[:numkeys]), list(items[numkeys:])
        if "ZREMRANGEBYSCORE" in script:
            active_key, minute_key, day_key, request_key = keys
            token, now, expires, _ttl, concurrency, rpm, daily_limit, _day_ttl = args
            if request_key in self.values:
                return -3
            current = {
                key: expiry
                for key, expiry in self.active.setdefault(active_key, {}).items()
                if expiry > float(now)
            }
            self.active[active_key] = current
            if len(current) >= int(concurrency):
                return -1
            if int(self.values.get(minute_key, 0)) >= int(rpm):
                return -2
            if int(daily_limit) > 0 and int(self.values.get(day_key, 0)) >= int(daily_limit):
                return -4
            self.values[minute_key] = int(self.values.get(minute_key, 0)) + 1
            if int(daily_limit) > 0:
                self.values[day_key] = int(self.values.get(day_key, 0)) + 1
            current[str(token)] = float(expires)
            self.values[request_key] = str(token)
            return 1
        if "ZREM" in script:
            return int(self.active.get(keys[0], {}).pop(args[0], None) is not None)
        if "EXISTS" in script:
            if keys[0] in self.values:
                return 0
            self.values[keys[0]] = args[0]
            return 1
        if "GET" in script and "DEL" in script:
            if self.get(keys[0]) != args[0]:
                return 0
            self.values.pop(keys[0], None)
            return 1
        if "GET" in script:
            if self.get(keys[0]) != args[0]:
                return 0
            self.values[keys[0]] = args[1]
            return 1
        raise AssertionError(f"未覆盖的 Redis 脚本：{script[:40]}")


class DraftProvider:
    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.calls = []

    async def start_with_tools(self, messages, tools):
        self.calls.append((messages, tools))
        return ProviderTurn(
            "",
            (ProviderToolCall("provider-call", "extract_purchase_plan", json.dumps(self.arguments, ensure_ascii=False)),),
            ProviderUsage(120, 35, 155),
        )


def _fixture(role_code: str = "PURCHASER", provider_arguments: dict | None = None):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def backfill_legacy_fixture_rows(db, _flush_context, _instances):
        for row in db.new:
            if isinstance(row, CompanyOwnedBase) and row.company_id is None:
                row.company_id = 1

    event.listen(Session.class_, "before_flush", backfill_legacy_fixture_rows)
    with Session() as db:
        db.add(Company(id=1, full_name="草稿测试公司"))
        db.add(Role(id=1, company_id=1, code=role_code, name=role_code))
        db.add(Staff(
            id=1,
            company_id=1,
            name="测试采购员",
            phone="13800001001",
            password_hash=hash_password("test-password"),
            is_active=True,
        ))
        db.add(StaffRole(staff_id=1, role_id=1))
        db.add(Unit(id=1, name="件", code="EA"))
        db.add(Unit(id=2, name="箱", code="BOX"))
        db.add(UnitGroup(id=1, name="包装单位", main_unit_id=2))
        db.add(UnitGroupItem(id=1, unit_group_id=1, unit_id=1, conversion_rate=Decimal("12")))
        db.add(Supplier(id=1, code="SUP-01", name="示例供应商", is_active=True))
        db.add(Product(
            id=1,
            name="蓝色收纳箱",
            code="P-001",
            default_supplier_id=1,
            unit_group_id=1,
            main_unit_id=2,
            is_disabled=False,
        ))
        db.add(ProductSku(
            id=1,
            product_id=1,
            spec_name="大号",
            spec_code="L",
            is_active=True,
        ))
        db.add(SupplierPrice(
            id=1,
            supplier_id=1,
            product_id=1,
            sku_id=1,
            price=Decimal("36.50"),
            status="ACTIVE",
        ))
        db.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(assistant_api.router)

    def override_db():
        with Session() as db:
            yield db

    provider = DraftProvider(provider_arguments or {
        "document_type": "purchase_plan",
        "supplier_name": "示例供应商",
        "supplier_code": None,
        "plan_date": "2026-10-15",
        "remark": None,
        "items": [{
            "product_name": "蓝色收纳箱",
            "product_code": "P-001",
            "spec_name": "大号",
            "spec_code": "L",
            "unit_name": "箱",
            "quantity": "2",
            "purchase_price": "36.50",
        }],
    })
    redis = DraftRedis()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[assistant_api.get_assistant_provider] = lambda: provider
    return TestClient(app), Session, redis, provider


def _settings(**overrides) -> Settings:
    values = {
        "assistant_enabled": True,
        "assistant_provider": "deepseek",
        "assistant_api_key": "test-key",
        "assistant_model": "deepseek-flash",
        "jwt_secret": "draft-test-secret",
        "assistant_total_timeout_seconds": 3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _headers(role: str = "PURCHASER") -> dict[str, str]:
    token = create_access_token(1, "测试采购员", [role])
    return {"Authorization": f"Bearer {token}"}


def _create_session(client: TestClient, headers: dict[str, str]) -> dict:
    result = client.post("/assistant/sessions", headers=headers)
    assert result.status_code == 200
    return result.json()["data"]


def _table(qty: str = "24") -> str:
    return (
        "供应商\t计划日期\t商品编码\t商品名称\t规格编码\t单位\t数量\t采购价\n"
        f"示例供应商\t2026-10-15\tP-001\t蓝色收纳箱\tL\t件\t{qty}\t36.50"
    )


def _provider_arguments() -> dict:
    return {
        "document_type": "purchase_plan",
        "supplier_name": "示例供应商",
        "supplier_code": None,
        "plan_date": "2026-10-15",
        "remark": None,
        "items": [{
            "product_name": "蓝色收纳箱",
            "product_code": "P-001",
            "spec_name": "大号",
            "spec_code": "L",
            "unit_name": "箱",
            "quantity": "2",
            "purchase_price": "36.50",
        }],
    }


def test_table_parse_maps_headers_converts_unit_and_never_writes_purchase_document(monkeypatch):
    client, Session, _redis, provider = _fixture()
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": _table(),
        },
    )

    assert response.status_code == 200, response.text
    draft = response.json()["data"]
    assert draft["ready"] is True, f"missing={draft['missing']}; items={draft['items']}"
    assert draft["table_mapping"]["row_count"] == 1
    assert draft["items"][0]["quantity"]["value"] == "2.000"
    assert draft["items"][0]["unit"]["value"]["name"] == "件"
    assert draft["items"][0]["calculation"]["amount"] == "73.00"
    assert draft["estimated_total"] == "73.00"
    assert draft["payload"]["items"][0] == {
        "product_id": 1,
        "sku_id": 1,
        "planned_qty": "2.000",
        "purchase_price": "36.50",
    }
    assert provider.calls == []
    with Session() as db:
        assert db.scalar(select(func.count(PurchasePlan.id))) == 0
        assert db.scalar(select(func.count(PurchasePlanItem.id))) == 0
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "ai.draft.create"))
        assert audit is not None
        assert _table() not in (audit.detail or "")
        usage = db.scalar(select(AssistantUsage).where(AssistantUsage.tool_names.is_not(None)))
        assert usage is not None
        assert usage.status == "completed"


def test_pasted_table_creates_one_draft_with_multiple_mapped_lines(monkeypatch):
    client, Session, _redis, provider = _fixture()
    with Session() as db:
        db.add(Product(
            id=2,
            name="红色收纳箱",
            code="P-002",
            default_supplier_id=1,
            unit_group_id=1,
            main_unit_id=2,
            is_disabled=False,
        ))
        db.add(ProductSku(
            id=2,
            product_id=2,
            spec_name="中号",
            spec_code="M",
            is_active=True,
        ))
        db.add(SupplierPrice(
            id=2,
            supplier_id=1,
            product_id=2,
            sku_id=2,
            price=Decimal("20.00"),
            status="ACTIVE",
        ))
        db.commit()
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    table = (
        "供应商\t计划日期\t商品编码\t规格编码\t单位\t数量\t采购价\n"
        "示例供应商\t2026-10-15\tP-001\tL\t件\t12\t36.50\n"
        "示例供应商\t2026-10-15\tP-002\tM\t件\t24\t20.00"
    )
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": table,
        },
    )
    assert response.status_code == 200, response.text
    draft = response.json()["data"]
    assert draft["ready"] is True
    assert draft["table_mapping"]["row_count"] == 2
    assert [line["quantity"]["value"] for line in draft["items"]] == ["1.000", "2.000"]
    assert [line["product"]["value"]["code"] for line in draft["items"]] == ["P-001", "P-002"]
    assert draft["estimated_total"] == "76.50"
    assert len(draft["payload"]["items"]) == 2
    assert provider.calls == []
    with Session() as db:
        assert db.scalar(select(func.count(PurchasePlan.id))) == 0
        assert db.scalar(select(func.count(PurchasePlanItem.id))) == 0


def test_natural_parse_uses_only_fixed_schema_and_rejects_model_ids(monkeypatch):
    args = {
        "document_type": "purchase_plan",
        "supplier_name": "示例供应商",
        "supplier_code": None,
        "plan_date": "2026-10-15",
        "remark": "加急",
        "items": [{
            "product_name": "蓝色收纳箱",
            "product_code": "P-001",
            "spec_name": "大号",
            "spec_code": "L",
            "unit_name": "箱",
            "quantity": "5",
            "purchase_price": None,
        }],
    }
    client, Session, _redis, provider = _fixture(provider_arguments=args)
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "natural",
            "input_text": "采购计划：示例供应商，2026-10-15，蓝色收纳箱大号 5 件。",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["ready"] is True
    assert response.json()["data"]["payload"]["items"][0]["planned_qty"] == "5.000"
    assert "purchase_price" not in response.json()["data"]["payload"]["items"][0]
    assert response.json()["data"]["items"][0]["purchase_price"]["preview_value"] == "36.50"
    assert response.json()["data"]["items"][0]["calculation"]["amount"] == "182.50"
    messages, tools = provider.calls[0]
    assert "不可信" in messages[0]["content"]
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "extract_purchase_plan"
    assert "id" not in tools[0]["function"]["parameters"]["properties"]
    with Session() as db:
        assert db.scalar(select(func.count(PurchasePlan.id))) == 0



def test_reparse_preserves_user_quantity_edits_and_cancel_is_versioned(monkeypatch):
    client, _Session, _redis, _provider = _fixture()
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    created = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": _table(),
        },
    ).json()["data"]
    edits = client.put(
        f"/assistant/drafts/{created['draft_id']}",
        headers=headers,
        json={
            "session_id": session["session_id"],
            "session_version": session["version"],
            "draft_version": created["version"],
            "edits": {
                "supplier_id": None,
                "plan_date": None,
                "items": [{"planned_qty": "30"}],
            },
        },
    )
    assert edits.status_code == 200, edits.text
    edited = edits.json()["data"]
    assert edited["items"][0]["quantity"]["value"] == "30.000"
    assert edited["supplier"]["value"] is None
    assert edited["plan_date"]["value"] is None

    reparsed = client.post(
        f"/assistant/drafts/{created['draft_id']}/reparse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "draft_version": edited["version"],
            "mode": "table",
            "input_text": _table("3"),
        },
    )
    assert reparsed.status_code == 200, reparsed.text
    after = reparsed.json()["data"]
    assert after["items"][0]["quantity"]["value"] == "30.000"
    assert "items.0.quantity" in after["edited_fields"]
    assert any(item["field"] == "items.0.quantity" and item.get("user_edit_preserved") for item in after["diff"])
    assert after["supplier"]["value"] is None
    assert after["plan_date"]["value"] is None
    assert {"supplier", "plan_date"}.issubset(after["edited_fields"])
    assert any(item["field"] == "supplier" and item.get("user_edit_preserved") for item in after["diff"])
    assert any(item["field"] == "plan_date" and item.get("user_edit_preserved") for item in after["diff"])
    assert after["version"] == edited["version"] + 1

    cancelled = client.request(
        "DELETE",
        f"/assistant/drafts/{created['draft_id']}",
        headers=headers,
        json={
            "session_id": session["session_id"],
            "session_version": session["version"],
            "draft_version": after["version"],
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    current = client.get(
        "/assistant/drafts/current",
        headers=headers,
        params={"session_id": session["session_id"], "session_version": session["version"]},
    )
    assert current.status_code == 200
    assert current.json()["data"] is None


def test_clearing_assistant_session_version_hides_previous_draft(monkeypatch):
    client, _Session, redis, _provider = _fixture()
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    parsed = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": _table(),
        },
    )
    assert parsed.status_code == 200
    old_draft = parsed.json()["data"]
    cleared = client.post(
        f"/assistant/sessions/{session['session_id']}/clear",
        headers=headers,
        json={"version": session["version"]},
    )
    assert cleared.status_code == 200
    new_session = cleared.json()["data"]
    assert new_session["session_id"] == session["session_id"]
    assert new_session["version"] == session["version"] + 1
    current = client.get(
        "/assistant/drafts/current",
        headers=headers,
        params={"session_id": new_session["session_id"], "session_version": new_session["version"]},
    )
    assert current.status_code == 200
    assert current.json()["data"] is None
    assert any(old_draft["draft_id"] in str(value) for value in redis.values.values())


def test_purchase_create_permission_and_login_session_are_enforced(monkeypatch):
    client, _Session, _redis, _provider = _fixture(role_code="AFTER_SALE")
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers("AFTER_SALE")
    session = _create_session(client, headers)
    denied = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": _table(),
        },
    )
    assert denied.status_code == 403

    purchaser_client, _Session2, _redis2, _provider2 = _fixture()
    session2 = _create_session(purchaser_client, _headers())
    another_login = {"Authorization": f"Bearer {create_access_token(1, '另一登录', ['PURCHASER'])}"}
    denied_session = purchaser_client.get(
        "/assistant/drafts/current",
        headers=another_login,
        params={"session_id": session2["session_id"], "session_version": session2["version"]},
    )
    assert denied_session.status_code == 403


def test_ambiguous_product_and_missing_required_fields_remain_unresolved(monkeypatch):
    client, Session, _redis, _provider = _fixture()
    with Session() as db:
        db.add(Product(
            id=2,
            name="蓝色收纳箱",
            code="P-002",
            default_supplier_id=1,
            unit_group_id=1,
            main_unit_id=2,
            is_disabled=False,
        ))
        db.commit()
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": "商品名称\t数量\n蓝色收纳箱\t",
        },
    )
    assert response.status_code == 200, response.text
    draft = response.json()["data"]
    assert draft["ready"] is False
    assert draft["payload"] is None
    assert draft["items"][0]["product"]["status"] == "ambiguous"
    assert len(draft["items"][0]["product"]["candidates"]) == 2
    assert "计划日期待补" in draft["missing"]
    assert "第 1 行数量待补" in draft["missing"]


def test_multiple_skus_and_unmatched_unit_require_user_selection(monkeypatch):
    client, Session, _redis, _provider = _fixture()
    with Session() as db:
        db.add(ProductSku(
            id=2,
            product_id=1,
            spec_name="小号",
            spec_code="S",
            is_active=True,
        ))
        db.commit()
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": (
                "供应商\t计划日期\t商品编码\t单位\t数量\n"
                "示例供应商\t2026-10-15\tP-001\t未知单位\t5"
            ),
        },
    )
    assert response.status_code == 200, response.text
    draft = response.json()["data"]
    assert draft["ready"] is False
    assert draft["items"][0]["sku"]["status"] == "ambiguous"
    assert len(draft["items"][0]["sku"]["candidates"]) == 2
    assert draft["items"][0]["unit"]["status"] == "missing"
    assert len(draft["items"][0]["unit"]["candidates"]) == 2

    edited = client.put(
        f"/assistant/drafts/{draft['draft_id']}",
        headers=headers,
        json={
            "session_id": session["session_id"],
            "session_version": session["version"],
            "draft_version": draft["version"],
            "edits": {"items": [{"sku_id": 1, "unit_id": 2}]},
        },
    )
    assert edited.status_code == 200, edited.text
    resolved = edited.json()["data"]
    assert resolved["ready"] is True
    assert resolved["payload"]["items"][0]["sku_id"] == 1
    assert resolved["payload"]["items"][0]["planned_qty"] == "5.000"


def test_table_parser_reports_mapping_errors_and_rejects_extra_model_fields():
    extracted, mapping = assistant_drafts.extract_pasted_table(
        "商品名称\t数量\n蓝色收纳箱\t2\t多余列"
    )
    assert extracted.items == []
    assert mapping["row_count"] == 1
    assert mapping["errors"][0]["row"] == 2
    invalid = {
        "document_type": "purchase_plan",
        "supplier_name": None,
        "supplier_code": None,
        "plan_date": None,
        "remark": None,
        "company_id": 1,
        "items": [],
    }
    with pytest.raises(ValidationError):
        assistant_drafts._ExtractedPlan.model_validate(invalid)


@pytest.mark.parametrize(
    "system_header",
    ["company_id", "公司 ID", "staff_id", "制单人", "plan_no", "采购计划编号", "status", "审核状态"],
)
def test_table_parser_rejects_system_owned_headers(system_header):
    text = f"商品编码\t数量\t{system_header}\nP-001\t2\t伪造值"
    with pytest.raises(BizException, match="系统管理字段"):
        assistant_drafts.extract_pasted_table(text)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_type", "purchase_order"),
        ("document_type", "uncertain"),
        ("company_id", 2),
        ("staff_id", 9),
        ("plan_no", "CGJH-FORGED"),
        ("status", "APPROVED"),
        ("supplier_id", 999),
        ("items.product_id", 999),
    ],
)
def test_fake_provider_system_field_and_type_forgery_is_rejected_without_business_write(
    monkeypatch, field, value
):
    arguments = _provider_arguments()
    if field.startswith("items."):
        arguments["items"][0][field.split(".", 1)[1]] = value
    else:
        arguments[field] = value

    client, Session, redis, provider = _fixture(provider_arguments=arguments)
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "natural",
            "input_text": "录入采购计划：示例供应商，蓝色收纳箱大号 2 箱。",
        },
    )

    assert response.status_code >= 400, response.text
    assert response.json()["code"] != "0"
    assert len(provider.calls) == 1
    with Session() as db:
        assert db.scalar(select(func.count(PurchasePlan.id))) == 0
        assert db.scalar(select(func.count(PurchasePlanItem.id))) == 0
        assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "ai.draft.create")) == 0
    assert not any(key.startswith("erp:assistant:draft:{") for key in redis.values)


@pytest.mark.parametrize(
    ("role_code", "may_create"),
    [
        ("ADMIN", True),
        ("VP", False),
        ("FINANCE", False),
        ("PURCHASER", True),
        ("WAREHOUSE", False),
        ("QUALITY", False),
        ("AFTER_SALE", False),
    ],
)
def test_draft_entry_permission_matches_seven_role_purchase_create_matrix(
    monkeypatch, role_code, may_create
):
    client, Session, _redis, _provider = _fixture(role_code=role_code)
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers(role_code)
    session = _create_session(client, headers)
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": _table(),
        },
    )

    assert response.status_code == (200 if may_create else 403), response.text
    with Session() as db:
        assert db.scalar(select(func.count(PurchasePlan.id))) == 0
        assert db.scalar(select(func.count(PurchasePlanItem.id))) == 0


def test_multi_company_catalog_fails_closed_before_creating_assistant_draft(monkeypatch):
    client, Session, redis, _provider = _fixture()
    with Session() as db:
        db.add(Company(id=2, full_name="另一家公司"))
        db.commit()
    monkeypatch.setattr(assistant_api, "get_settings", lambda: _settings())
    headers = _headers()
    session = _create_session(client, headers)
    response = client.post(
        "/assistant/drafts/parse",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "session_id": session["session_id"],
            "session_version": session["version"],
            "mode": "table",
            "input_text": _table(),
        },
    )

    assert response.status_code == 403, response.text
    with Session() as db:
        assert db.scalar(select(func.count(PurchasePlan.id))) == 0
        assert db.scalar(select(func.count(PurchasePlanItem.id))) == 0
    assert not any(key.startswith("erp:assistant:draft:{") for key in redis.values)
