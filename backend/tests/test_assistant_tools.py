"""TASK-079 固定只读工具的权限、范围与脱敏回归。"""

import json
from decimal import Decimal

import app.models  # noqa: F401  # ensure the full metadata is registered
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.data_scope import initialize_scope_policy
from app.core.permissions import permissions_for_roles
from app.core.security import hash_password
from app.db.base import Base
from app.models.audit_log import AuditLog
from app.models.org import Company, Department, Staff
from app.models.party import Supplier
from app.models.product import Product
from app.models.purchase import PurchaseOrder, PurchaseOrderItem
from app.models.rbac import DataScope, Role, StaffRole
from app.integrations.assistant_provider import ProviderToolCall
from app.services import assistant_tools


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_identity(
    db,
    *,
    second_staff: bool = False,
    role_code: str = "PURCHASER",
) -> None:
    db.add(Company(id=1, full_name="测试公司"))
    db.add(Role(id=1, company_id=1, code=role_code, name=role_code))
    db.add_all(
        [
            Staff(
                id=1,
                company_id=1,
                name="采购员一",
                phone="13800000001",
                password_hash=hash_password("test-password"),
                is_active=True,
            ),
            *(
                [
                    Staff(
                        id=2,
                        company_id=1,
                        name="采购员二",
                        phone="13800000002",
                        password_hash=hash_password("test-password"),
                        is_active=True,
                    )
                ]
                if second_staff
                else []
            ),
        ]
    )
    db.add(StaffRole(staff_id=1, role_id=1))
    if second_staff:
        db.add(StaffRole(staff_id=2, role_id=1))
    db.commit()


def _seed_purchase(db, *, created_by: int = 1, company_id: int = 1) -> None:
    db.add(
        Supplier(
            id=1,
            company_id=company_id,
            code="SUPPLIER-PRIVATE",
            name="供应商身份秘密",
            phone="13912345678",
            address="不应发出的地址",
            is_active=True,
        )
    )
    db.add(
        Product(
            id=1,
            company_id=company_id,
            code="PRODUCT-PRIVATE",
            name="商品身份秘密",
            cost_price=Decimal("37.50"),
        )
    )
    db.add(
        PurchaseOrder(
            id=812734,
            company_id=company_id,
            order_no="PO-SENSITIVE-NUMBER",
            supplier_id=1,
            status="PENDING",
            audit_status="APPROVED",
            total_qty=Decimal("9.000"),
            arrived_qty=Decimal("4.000"),
            total_amount=Decimal("9876.54"),
            created_by=created_by,
        )
    )
    db.add(
        PurchaseOrderItem(
            id=1,
            company_id=company_id,
            order_id=812734,
            product_id=1,
            qty=Decimal("9.000"),
            arrived_qty=Decimal("4.000"),
            purchase_price=Decimal("88.88"),
            amount=Decimal("799.92"),
        )
    )
    db.commit()


def _purchase_call(arguments: str = "{}") -> ProviderToolCall:
    return ProviderToolCall(
        id="call-safe-test",
        name="purchase_order_lookup",
        arguments=arguments,
    )


def test_tool_schema_never_contains_selected_record_identifier():
    selected = assistant_tools.SelectedDocument(
        doc_type="purchase_order",
        doc_id=812734,
    )
    tools = assistant_tools.tool_definitions(
        ["module:purchase"],
        selected,
    )
    encoded = json.dumps(tools, ensure_ascii=False)

    assert [item["function"]["name"] for item in tools] == ["purchase_order_lookup"]
    assert "812734" not in encoded
    assert "doc_id" not in encoded
    assert "order_no" not in encoded
    assert tools[0]["function"]["parameters"]["properties"] == {}


def test_rejected_tool_arguments_are_not_echoed_into_model_history():
    rejected = assistant_tools.sanitized_tool_call_for_history(
        ProviderToolCall(
            id="call-invalid-args",
            name="purchase_order_lookup",
            arguments='{"doc_id":812734,"bank_account":"private"}',
        )
    )
    unknown = assistant_tools.sanitized_tool_call_for_history(
        ProviderToolCall(
            id="call-unknown-tool",
            name="query_private_database",
            arguments='{"sql":"SELECT *"}',
        )
    )

    assert rejected == {"name": "purchase_order_lookup", "arguments": "{}"}
    assert unknown == {"name": "unregistered_tool", "arguments": "{}"}


def test_missing_scope_policy_denies_before_business_lookup(session_factory, monkeypatch):
    with session_factory() as db:
        _seed_identity(db)
        business_lookup_called = False

        def forbidden_lookup(*_args, **_kwargs):
            nonlocal business_lookup_called
            business_lookup_called = True
            pytest.fail("业务查询不得在 scope policy 初始化前执行")

        monkeypatch.setattr(assistant_tools.purchase_order_service, "get_order_or_404", forbidden_lookup)
        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result["ok"] is False
        assert source is None
        assert business_lookup_called is False
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "READ_DENIED"))
        assert audit is not None
        assert "812734" not in (audit.detail or "")


def test_purchase_result_is_record_scoped_and_minimally_redacted(session_factory):
    with session_factory() as db:
        _seed_identity(db, second_staff=True)
        _seed_purchase(db)
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        outbound = json.dumps(
            {"result": result, "source": source},
            ensure_ascii=False,
            default=str,
        )
        assert result["ok"] is True
        assert result["data"]["status"] == "PENDING"
        assert result["data"]["arrived_quantity"] == Decimal("4.000")
        assert set(result["data"]) == {"status", "audit_status", "arrived_quantity"}
        assert source and source["type"] == "data"
        assert source["scope"] == "本人制单范围"
        for private_value in (
            "812734",
            "PO-SENSITIVE-NUMBER",
            "SUPPLIER-PRIVATE",
            "供应商身份秘密",
            "商品身份秘密",
            "13912345678",
            "不应发出的地址",
            "9876.54",
            "88.88",
            "799.92",
            "37.50",
        ):
            assert private_value not in outbound
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "READ"))
        assert audit is not None
        assert audit.company_id == 1
        assert audit.target_id == "purchase_order_lookup"
        assert "812734" not in (audit.detail or "")
        assert "PO-SENSITIVE-NUMBER" not in (audit.detail or "")


def test_purchase_record_outside_self_scope_is_indistinguishable_from_missing(session_factory):
    with session_factory() as db:
        _seed_identity(db, second_staff=True)
        _seed_purchase(db, created_by=2)
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result == {
            "ok": False,
            "message": "记录不存在或当前账号无权查看，或查询参数无效。",
        }
        assert source is None


def test_purchase_department_scope_allows_same_department_owner(session_factory):
    with session_factory() as db:
        _seed_identity(db, second_staff=True)
        db.add(Department(id=1, company_id=1, name="采购部"))
        db.get(Staff, 1).department_id = 1
        db.get(Staff, 2).department_id = 1
        db.add(DataScope(staff_id=1, scope_type="DEPARTMENT", department_id=1))
        _seed_purchase(db, created_by=2)
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result["ok"] is True
        assert source and source["scope"] == "本部门制单范围"


def test_purchase_company_scope_allows_same_company_owner(session_factory):
    with session_factory() as db:
        _seed_identity(db, second_staff=True)
        db.add(DataScope(staff_id=1, scope_type="COMPANY"))
        _seed_purchase(db, created_by=2)
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result["ok"] is True
        assert source and source["scope"] == "本公司制单范围"


@pytest.mark.parametrize(
    "arguments",
    [
        '{"doc_id":812734}',
        '{"order_id":812734}',
        '{"company_id":2}',
        '{"page_size":999999}',
        '{"scope":"all"}',
    ],
)
def test_untrusted_tool_arguments_are_rejected(session_factory, arguments):
    with session_factory() as db:
        _seed_identity(db)
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(arguments),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result["ok"] is False
        assert source is None


def test_current_database_permission_revocation_is_enforced(session_factory, monkeypatch):
    with session_factory() as db:
        _seed_identity(db)
        initialize_scope_policy(db, db.get(Staff, 1))
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == 1))
        db.delete(assignment)
        db.commit()

        monkeypatch.setattr(
            assistant_tools.purchase_order_service,
            "get_order_or_404",
            lambda *_args, **_kwargs: pytest.fail("撤权后不得查询业务记录"),
        )
        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result["ok"] is False
        assert source is None


def test_multiple_current_roles_are_combined_for_module_authorization(session_factory):
    with session_factory() as db:
        _seed_identity(db)
        db.add(Role(id=2, company_id=1, code="WAREHOUSE", name="仓库"))
        db.add(StaffRole(staff_id=1, role_id=2))
        db.commit()
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=ProviderToolCall(
                id="call-multi-role",
                name="inventory_lookup",
                arguments="{}",
            ),
        )

        assert result["ok"] is True
        assert result["data"]["warning_sku_count"] == 0
        assert source is not None


def test_permission_revoked_during_lookup_discards_prepared_result(
    session_factory,
    monkeypatch,
):
    with session_factory() as db:
        _seed_identity(db)
        initialize_scope_policy(db, db.get(Staff, 1))

        def revoke_while_reading(db, _order_id):
            assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == 1))
            db.delete(assignment)
            db.commit()
            return {"status": "PENDING", "audit_status": "APPROVED", "arrived_quantity": 4}

        monkeypatch.setattr(
            assistant_tools,
            "_lookup_purchase_order",
            revoke_while_reading,
        )
        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result["ok"] is False
        assert source is None
        assert "PENDING" not in json.dumps(result)


def test_unregistered_tool_is_denied_without_echoing_arguments(session_factory):
    with session_factory() as db:
        _seed_identity(db)
        call = ProviderToolCall(
            id="call-unregistered",
            name="execute_sql",
            arguments='{"query":"SELECT sensitive_data"}',
        )

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=call,
        )

        assert result["ok"] is False
        assert source is None
        assert "SELECT sensitive_data" not in json.dumps(result)
        audit = db.scalar(select(AuditLog).where(AuditLog.target_id == "unregistered"))
        assert audit is not None
        assert "SELECT sensitive_data" not in (audit.detail or "")


def test_assistant_inventory_query_runs_in_multi_company_database(session_factory):
    with session_factory() as db:
        _seed_identity(db, role_code="ADMIN")
        db.add(Company(id=2, full_name="第二家公司"))
        db.commit()
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=ProviderToolCall(
                id="call-inventory-test",
                name="inventory_lookup",
                arguments="{}",
            ),
        )

        assert result["ok"] is True, result
        assert source is not None


def test_inventory_query_returns_only_company_quantity_and_warning_aggregates(session_factory):
    with session_factory() as db:
        _seed_identity(db, role_code="ADMIN")
        initialize_scope_policy(db, db.get(Staff, 1))

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=ProviderToolCall(
                id="call-inventory-safe",
                name="inventory_lookup",
                arguments="{}",
            ),
        )

        assert result == {
            "ok": True,
            "data": {
                "normal_quantity": 0,
                "defective_quantity": 0,
                "warning_sku_count": 0,
            },
        }
        assert source and source["scope"] == "本公司共享业务数据"


@pytest.mark.parametrize(
    ("roles", "expected_tools"),
    [
        (["ADMIN"], {"purchase_order_lookup", "inventory_lookup", "my_approval_tasks", "authorized_metric"}),
        (["VP"], {"purchase_order_lookup", "my_approval_tasks", "authorized_metric"}),
        (["FINANCE"], {"purchase_order_lookup", "my_approval_tasks", "authorized_metric"}),
        (["PURCHASER"], {"purchase_order_lookup", "my_approval_tasks", "authorized_metric"}),
        (["WAREHOUSE"], {"inventory_lookup", "my_approval_tasks", "authorized_metric"}),
        (["QUALITY"], {"purchase_order_lookup", "inventory_lookup", "my_approval_tasks", "authorized_metric"}),
        (["AFTER_SALE"], {"authorized_metric"}),
        (["PURCHASER", "WAREHOUSE"], {"purchase_order_lookup", "inventory_lookup", "my_approval_tasks", "authorized_metric"}),
    ],
)
def test_registered_read_tools_match_seven_role_and_multi_role_matrix(roles, expected_tools):
    selected = assistant_tools.SelectedDocument(doc_type="purchase_order", doc_id=812734)
    tools = assistant_tools.tool_definitions(
        sorted(permissions_for_roles(roles)), selected_document=selected
    )

    assert {item["function"]["name"] for item in tools} == expected_tools
    assert all(item["function"]["parameters"].get("additionalProperties") is False for item in tools)
    assert all("create" not in item["function"]["name"] for item in tools)
    assert all("approve" not in item["function"]["name"] for item in tools)
    assert all("ship" not in item["function"]["name"] for item in tools)


@pytest.mark.parametrize(
    ("staff_id", "company_id"),
    [(2, 1), (1, 2)],
)
def test_dispatcher_rejects_policy_bound_to_another_identity_or_company(
    session_factory, monkeypatch, staff_id, company_id
):
    with session_factory() as db:
        _seed_identity(db, second_staff=True)
        initialize_scope_policy(db, db.get(Staff, 1))
        monkeypatch.setattr(
            assistant_tools.purchase_order_service,
            "get_order_or_404",
            lambda *_args, **_kwargs: pytest.fail("错身份/公司 policy 不得查询单据"),
        )

        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=staff_id,
            company_id=company_id,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order",
                doc_id=812734,
            ),
        )

        assert result["ok"] is False
        assert source is None


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("inventory_lookup", "{}"),
        ("my_approval_tasks", "{}"),
        ("authorized_metric", '{"metric":"today_order_count"}'),
    ],
)
def test_shared_assistant_queries_are_available_in_multi_company_database(
    session_factory, tool_name, arguments
):
    with session_factory() as db:
        _seed_identity(db, role_code="ADMIN")
        db.add(Company(id=2, full_name="第二家公司"))
        db.commit()
        initialize_scope_policy(db, db.get(Staff, 1))
        result, source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=ProviderToolCall(id="call-multi-company", name=tool_name, arguments=arguments),
        )

        assert result["ok"] is True, result
        assert source is not None


def test_purchase_document_owned_by_another_company_matches_missing_response(session_factory):
    with session_factory() as db:
        _seed_identity(db)
        db.add(Company(id=2, full_name="第二家公司"))
        db.add(Role(id=2, company_id=2, code="PURCHASER", name="采购员"))
        db.add(
            Staff(
                id=3,
                company_id=2,
                name="异公司员工",
                phone="13800000003",
                password_hash=hash_password("test-password"),
                is_active=True,
            )
        )
        db.add(StaffRole(staff_id=3, role_id=2))
        db.add(DataScope(staff_id=1, scope_type="COMPANY"))
        _seed_purchase(db, created_by=3, company_id=2)
        initialize_scope_policy(db, db.get(Staff, 1))

        foreign, foreign_source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order", doc_id=812734
            ),
        )
        missing, missing_source = assistant_tools.dispatch_tool_call(
            db,
            staff_id=1,
            company_id=1,
            call=_purchase_call(),
            selected_document=assistant_tools.SelectedDocument(
                doc_type="purchase_order", doc_id=999999
            ),
        )

        assert foreign == missing
        assert foreign_source is None
        assert missing_source is None
