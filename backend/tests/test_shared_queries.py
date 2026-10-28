"""TASK-098：隔离授权回归；不代替本机 MySQL / WorkBuddy 验收。"""

import json
import inspect
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, func, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import dashboard as dashboard_api
from app.api.approval import router as approval_router
from app.api.approval_manage import router as approval_manage_router
from app.api.purchase_order import router as purchase_router
from app.core.data_scope import clear_scope_policy, document_visible, initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.query_access import CAPABILITIES, MCP_GRANT_KEY, QueryGrant, authorize_query
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.org import Company, Department, Staff
from app.models.order import Order
from app.models.party import Supplier
from app.models.product import Product
from app.models.purchase import PurchaseOrder, PurchaseOrderItem, PurchasePlan
from app.models.rbac import DataScope, Role, StaffRole
from app.models.stock import StockIn, StockInItem, StockSetting
from app.services import approval_manage_service, metrics_service
from app.services import query_projection, shared_queries
from app.services.mcp_credentials import ACCESS_TOOL, authenticate_credential, issue_credential, revoke_credential


@pytest.fixture
def factory():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(Company(id=1, full_name="隔离测试公司"))
        db.add_all([Department(id=1, company_id=1, name="一部"), Department(id=2, company_id=1, name="二部")])
        for staff_id in (1, 2, 3):
            db.add(Staff(id=staff_id, company_id=1, department_id=1 if staff_id < 3 else 2, name=f"采购{staff_id}"))
        db.add(Role(id=1, company_id=1, code="PURCHASER", name="采购"))
        db.add_all([StaffRole(staff_id=staff_id, role_id=1) for staff_id in (1, 2, 3)])
        for staff_id, role in enumerate(("ADMIN", "VP", "FINANCE", "WAREHOUSE", "QUALITY", "AFTER_SALE"), 10):
            db.add(Staff(id=staff_id, company_id=1, name=role))
            db.add(Role(id=staff_id, company_id=1, code=role, name=role))
            db.add(StaffRole(staff_id=staff_id, role_id=staff_id))
        db.add(Supplier(id=1, company_id=1, code="S", name="测试供应商"))
        db.add(Product(id=1, company_id=1, code="P", name="测试商品"))
        for doc_id, owner in ((1, 1), (2, 2), (3, 3), (90, None), (91, 999)):
            db.add(PurchaseOrder(id=doc_id, company_id=1, order_no=f"PO-{doc_id}", supplier_id=1, created_by=owner,
                                 total_qty=Decimal("5"), total_amount=Decimal("10"), status="APPROVED", audit_status="APPROVED"))
            db.add(PurchaseOrderItem(id=doc_id, company_id=1, order_id=doc_id, product_id=1, qty=Decimal("5"),
                                     arrived_qty=Decimal("0"), purchase_price=Decimal("2"), amount=Decimal("10")))
        db.commit()
    yield factory
    engine.dispose()


def bind(db, staff_id=1):
    clear_scope_policy(db)
    staff = db.get(Staff, staff_id, populate_existing=True)
    initialize_scope_policy(db, staff)
    return staff


def add_other_company(db):
    db.add(Company(id=2, full_name="另一公司"))
    db.add(Staff(id=4, company_id=2, name="外公司"))
    db.add(PurchaseOrder(id=4, company_id=2, order_no="OTHER-COMPANY-SECRET", supplier_id=1, created_by=4, total_qty=50, total_amount=1000))
    db.commit()


@pytest.mark.parametrize("scope,expected", [("SELF", {1}), ("DEPARTMENT", {1, 2}), ("COMPANY", {1, 2, 3})])
def test_list_total_report_and_id_use_same_scope(factory, scope, expected):
    with factory() as db:
        add_other_company(db)
        db.add(DataScope(staff_id=1, scope_type=scope))
        db.commit()
        bind(db)
        result = shared_queries.purchase_orders(db, page_size=1)
        assert result["total"] == len(expected)
        assert result["items"][0]["id"] in expected
        report = metrics_service.purchase_order_report(db)
        assert report["summary"]["order_count"] == len(expected)
        assert report["summary"]["total_amount"] == Decimal(10 * len(expected))
        for doc_id in ({1, 2, 3, 4, 90, 91} - expected):
            with pytest.raises(BizException):
                shared_queries.purchase_order(db, doc_id)
        # 多公司部署下，明细与关联库存按本公司归属过滤后仍可安全查询。
        assert shared_queries.purchase_order(db, 1)["items"][0]["id"] == 1


def test_detail_children_and_related_plan_do_not_expand_scope(factory):
    with factory() as db:
        db.add(PurchasePlan(id=1, company_id=1, plan_no="HIDDEN-PLAN", supplier_id=1, created_by=3))
        db.get(PurchaseOrder, 1).plan_id = 1
        db.commit()
        bind(db)
        result = shared_queries.purchase_order(db, 1)
        assert result["plan_id"] is None and result["plan_no"] is None
        assert [row["id"] for row in result["items"]] == [1]
        # 其他采购订单的未到货数不能从商品关联统计泄漏。
        assert result["items"][0]["in_transit_stock"] == 0
        assert shared_queries.purchase_orders(db, keyword="HIDDEN-PLAN")["total"] == 0
        with pytest.raises(BizException):
            shared_queries.purchase_orders(db, plan_id=1)
        with pytest.raises(BizException):
            shared_queries.purchase_order(db, 2)
        projected = shared_queries.external_purchase_order(db, 1)
        assert projected["items"][0]["product_name"] == "测试商品"
        assert projected["total_amount"] == Decimal("10")
        assert "approval" not in projected and "attachments" not in projected


@pytest.mark.parametrize("scope,expected", [("SELF", 1), ("DEPARTMENT", 2), ("COMPANY", 3)])
def test_inbound_report_staff_join_scopes_rows_and_total(factory, scope, expected):
    with factory() as db:
        for staff_id in (1, 2, 3):
            db.get(PurchaseOrder, staff_id).purchaser_id = staff_id
            db.add(StockIn(id=staff_id, company_id=1, stock_in_no=f"IN-{staff_id}", warehouse_id=1,
                           stock_in_type="PURCHASE", purchase_order_id=staff_id,
                           created_by=staff_id, audit_status="APPROVED"))
            db.add(StockInItem(company_id=1, stock_in_id=staff_id, product_id=1, qty=Decimal("2"), amount=Decimal("4")))
        # 可见入库错误关联不可见采购订单，也不能通过 join 带出金额。
        db.add(StockIn(id=90, company_id=1, stock_in_no="MISMATCH", warehouse_id=1, stock_in_type="PURCHASE",
                       purchase_order_id=91, created_by=1, audit_status="APPROVED"))
        db.add(StockInItem(company_id=1, stock_in_id=90, product_id=1, qty=Decimal("500"), amount=Decimal("1000")))
        db.add(DataScope(staff_id=1, scope_type=scope))
        db.commit()
        bind(db)
        report = metrics_service.purchase_inbound_report(db)
        assert report["summary"]["stock_in_count"] == expected
        assert report["summary"]["amount"] == Decimal(4 * expected)


def test_missing_context_unknown_capability_and_token_intersection(factory):
    with factory() as db:
        with pytest.raises(RuntimeError):
            shared_queries.purchase_orders(db)
        bind(db)
        with pytest.raises(BizException):
            authorize_query(db, "execute_sql")
        created = issue_credential(db, db.get(Staff, 1), name="查询", permission_scopes=["module:purchase"], tool_scopes=[ACCESS_TOOL])
        authenticate_credential(db, created["token"])
        assert authorize_query(db, "access").permissions == frozenset({"module:purchase"})
        # 已接入 API 不意味着已接入 MCP；本卡不发布业务工具。
        with pytest.raises(BizException):
            shared_queries.purchase_orders(db)
        db.info[MCP_GRANT_KEY] = QueryGrant(frozenset({"module:finance"}), frozenset({ACCESS_TOOL}))
        with pytest.raises(BizException):
            authorize_query(db, "access")
        db.info[MCP_GRANT_KEY] = QueryGrant(frozenset({"module:purchase"}), frozenset())
        with pytest.raises(BizException):
            authorize_query(db, "access")
        clear_scope_policy(db)
        assert MCP_GRANT_KEY not in db.info and "data_scope_policy" not in db.info


def test_every_mcp_capability_enforces_tool_and_permission_grants(factory):
    from app.core.permissions import MODULES

    all_permissions = {f"module:{module}" for module in MODULES}
    all_permissions.update({"settings:manage", "approval:configure"})
    checked = 0
    with factory() as db:
        bind(db, 10)  # 管理员权限用于隔离测试凭据自身的权限交集逻辑。
        for capability_name, capability in CAPABILITIES.items():
            tools = (
                {capability.tool}
                if isinstance(capability.tool, str)
                else set(capability.tool or ())
            )
            if not tools or capability_name == "access":
                continue

            db.info[MCP_GRANT_KEY] = QueryGrant(
                frozenset(all_permissions), frozenset(tools),
            )
            assert authorize_query(db, capability_name).capability == capability_name

            db.info[MCP_GRANT_KEY] = QueryGrant(
                frozenset(all_permissions), frozenset({ACCESS_TOOL}),
            )
            with pytest.raises(BizException):
                authorize_query(db, capability_name)

            required = (
                {capability.permission}
                if isinstance(capability.permission, str) and capability.permission
                else set(capability.permission or ())
            )
            if required:
                normalized = {
                    {
                        "approval:configure": "module:approval",
                        "settings:manage": "module:settings",
                    }.get(permission, permission)
                    for permission in required
                }
                unrelated = next(
                    (f"module:{module}" for module in MODULES
                     if f"module:{module}" not in normalized),
                    None,
                )
                if unrelated is not None:
                    db.info[MCP_GRANT_KEY] = QueryGrant(
                        frozenset({unrelated}), frozenset(tools),
                    )
                    with pytest.raises(BizException):
                        authorize_query(db, capability_name)
            checked += 1

    assert checked >= 30


def test_every_published_capability_enforces_the_role_matrix(factory):
    from app.core.permissions import MODULES, permissions_for_roles

    all_grant_permissions = {f"module:{module}" for module in MODULES}
    all_grant_permissions.update({"settings:manage", "approval:configure"})
    with factory() as db:
        role_assignments = db.execute(
            select(Staff.id, Role.code)
            .join(StaffRole, StaffRole.staff_id == Staff.id)
            .join(Role, Role.id == StaffRole.role_id)
            .where(Staff.id.in_([1, 10, 11, 12, 13, 14, 15]))
            .order_by(Staff.id)
        ).all()
        assert {role for _staff_id, role in role_assignments} == {
            "PURCHASER", "ADMIN", "VP", "FINANCE", "WAREHOUSE", "QUALITY", "AFTER_SALE",
        }

        checked = 0
        for capability_name, capability in CAPABILITIES.items():
            tools = (
                {capability.tool}
                if isinstance(capability.tool, str)
                else set(capability.tool or ())
            )
            if not tools or capability_name == "access":
                continue
            required_permissions = (
                ({capability.permission} if capability.permission else set())
                if isinstance(capability.permission, str)
                else set(capability.permission or ())
            )
            for staff_id, role_code in role_assignments:
                bind(db, staff_id)
                db.info[MCP_GRANT_KEY] = QueryGrant(
                    frozenset(all_grant_permissions), frozenset(tools),
                )
                live_permissions = permissions_for_roles([role_code])
                permission_allowed = (
                    "*" in live_permissions
                    or not required_permissions
                    or bool(required_permissions.intersection(live_permissions))
                )
                role_allowed = (
                    not capability.roles
                    or role_code == "ADMIN"
                    or role_code in capability.roles
                )
                should_allow = permission_allowed and role_allowed
                if should_allow:
                    result = authorize_query(db, capability_name)
                    assert result.roles == frozenset({role_code})
                else:
                    with pytest.raises(BizException):
                        authorize_query(db, capability_name)
                checked += 1

        assert checked >= 200


@pytest.mark.parametrize("change", ["inactive", "role", "department", "scope", "company", "foreign_role"])
def test_live_identity_and_scope_changes_reject_old_context(factory, change):
    with factory() as db:
        bind(db)
        authorize_query(db, "purchase_orders")
        if change == "inactive":
            db.execute(update(Staff).where(Staff.id == 1).values(is_active=False).execution_options(synchronize_session=False))
        elif change == "role":
            db.execute(delete(StaffRole).where(StaffRole.staff_id == 1))
        elif change == "department":
            db.execute(update(Staff).where(Staff.id == 1).values(department_id=2).execution_options(synchronize_session=False))
        elif change == "scope":
            db.add(DataScope(staff_id=1, scope_type="COMPANY"))
        elif change == "company":
            db.execute(update(Staff).where(Staff.id == 1).values(company_id=2).execution_options(synchronize_session=False))
        else:
            db.execute(update(Role).where(Role.id == 1).values(company_id=2))
        db.commit()
        with pytest.raises(BizException):
            shared_queries.purchase_orders(db)


def test_revoked_credential_not_reused_in_same_session(factory):
    with factory() as db:
        created = issue_credential(db, db.get(Staff, 1), name="查询", permission_scopes=["module:purchase"], tool_scopes=[ACCESS_TOOL])
        authenticate_credential(db, created["token"])
        revoke_credential(db, db.get(Staff, 1), created["id"])
        with pytest.raises(BizException):
            authenticate_credential(db, created["token"])
        assert MCP_GRANT_KEY not in db.info


def test_approval_exception_is_detail_only_and_excludes_orphans_and_other_company(factory):
    with factory() as db:
        add_other_company(db)
        for doc_id in (2, 4, 90, 91):
            db.add(ApprovalRecord(company_id=1, doc_type="purchase_order", doc_id=doc_id, doc_no=f"record-{doc_id}",
                                  approver_id=1, action="APPROVE", status="APPROVED", comment="secret comment"))
        db.commit()
        staff = bind(db)
        assert document_visible(db, 2, "purchase_order", 2)
        assert not document_visible(db, 4, "purchase_order", 4)
        assert not document_visible(db, None, "purchase_order", 90)
        assert not document_visible(db, 999, "purchase_order", 91)
        assert shared_queries.purchase_orders(db)["total"] == 1
        result = approval_manage_service.list_my_approvals(db, staff=staff, scope="processed")
        assert result["total"] == 1 and result["items"][0]["doc_id"] == 2
        assert "record-4" not in json.dumps(result)


def test_document_visibility_uses_bounded_approval_history_lookups(factory):
    with factory() as db:
        db.add(ApprovalFlow(
            id=1, company_id=1, name="有界检查审批流", doc_type="purchase_order",
            approvers=json.dumps([{
                "step": 1, "name": "主管", "approver_ids": [1],
            }]),
        ))
        db.add_all([
            ApprovalRecord(
                id=record_id, company_id=1, doc_type="purchase_order", doc_id=777,
                approver_id=1, action="APPROVE", status="APPROVED", step=1,
            )
            for record_id in range(1, 1001)
        ])
        db.add(ApprovalRecord(
            id=1001, company_id=1, flow_id=1, doc_type="purchase_order", doc_id=778,
            step=1, action="APPROVE", status="PENDING",
        ))
        db.commit()
        staff = bind(db, 1)
        statements = []

        def capture_approval_sql(_conn, _cursor, statement, *_args):
            if "from approval_record" in statement.lower():
                statements.append(statement.lower())

        engine = factory.kw["bind"]
        event.listen(engine, "before_cursor_execute", capture_approval_sql)
        try:
            assert document_visible(db, 2, "purchase_order", 777)
            assert document_visible(db, 2, "purchase_order", 778)
        finally:
            event.remove(engine, "before_cursor_execute", capture_approval_sql)

        assert len(statements) == 3
        assert all("limit" in statement for statement in statements)
        clear_scope_policy(db)


def test_current_pending_approver_and_revoked_step(factory):
    with factory() as db:
        flow = ApprovalFlow(id=1, company_id=1, name="审批", doc_type="purchase_order", approvers="[1]", created_by=10)
        db.add(flow)
        db.add(ApprovalRecord(company_id=1, doc_type="purchase_order", doc_id=2, flow_id=1, step=1, status="PENDING"))
        db.commit()
        bind(db)
        assert document_visible(db, 2, "purchase_order", 2)
        db.execute(update(ApprovalFlow).where(ApprovalFlow.id == 1).values(approvers="[3]").execution_options(synchronize_session=False))
        db.commit()
        assert not document_visible(db, 2, "purchase_order", 2)


def test_dashboard_cache_reauth_scope_and_company_filter(factory, monkeypatch):
    class Cache:
        values = {}
        reads = 0

        def get(self, key):
            self.reads += 1
            return self.values.get(key)

        def set(self, key, value, ex):
            assert ex == 15
            self.values[key] = value

    cache = Cache()
    monkeypatch.setattr(dashboard_api, "get_redis", lambda: cache)
    monkeypatch.setattr(metrics_service, "dashboard_overview", lambda db, **kw: {"staff": authorize_query(db, "dashboard").staff.id})
    with factory() as db:
        staff = bind(db)
        assert dashboard_api.get_dashboard_overview("day", staff, db).data["staff"] == 1
        assert dashboard_api.get_dashboard_overview("day", staff, db).data["staff"] == 1
        staff = bind(db, 2)
        assert dashboard_api.get_dashboard_overview("day", staff, db).data["staff"] == 2
        assert len(cache.values) == 2
        db.add(DataScope(staff_id=2, scope_type="COMPANY"))
        db.commit()
        staff = bind(db, 2)
        dashboard_api.get_dashboard_overview("day", staff, db)
        assert len(cache.values) == 3
        old_reads = cache.reads
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.commit()
        with pytest.raises(BizException):
            dashboard_api.get_dashboard_overview("day", staff, db)
        assert cache.reads == old_reads
        staff = bind(db, 1)
        add_other_company(db)
        assert dashboard_api.get_dashboard_overview("day", staff, db).data["staff"] == 1
        assert cache.reads == old_reads + 1


def test_stock_setting_get_does_not_write(factory):
    with factory() as db:
        bind(db, 13)
        statements = []
        def track(_conn, _cursor, statement, *_):
            statements.append(statement.lstrip().split()[0].upper())
        event.listen(db.bind, "before_cursor_execute", track)
        try:
            value = shared_queries.stock_settings(db)
            assert value["id"] is None and value["shop_stock_enabled"] is False
            assert not db.new and not db.dirty
            assert db.scalar(select(func.count(StockSetting.id))) == 0
            assert not set(statements) & {"INSERT", "UPDATE", "DELETE"}
        finally:
            event.remove(db.bind, "before_cursor_execute", track)


@pytest.mark.parametrize("staff_id,capability,allowed", [
    (1, "purchase_orders", True), (10, "purchase_orders", True), (12, "purchase_orders", True),
    (11, "purchase_orders", False), (14, "purchase_orders", False),
    (14, "assistant_inventory", False), (13, "assistant_inventory", True),
    (15, "print_batch", False), (12, "print_batch", True), (13, "print_batch", True),
    (1, "purchase_invoice_report", False), (12, "purchase_invoice_report", True),
])
def test_subfunction_matrix(factory, staff_id, capability, allowed):
    with factory() as db:
        bind(db, staff_id)
        if allowed:
            assert authorize_query(db, capability).staff.id == staff_id
        else:
            with pytest.raises(BizException):
                authorize_query(db, capability)


def test_projection_is_recursive_bounded_and_amount_authorized(factory):
    with factory() as db:
        bind(db)
        access = authorize_query(db, "purchase_orders")
        secret = "DO-NOT-EXPOSE"
        data = {"order_no": "PO-1", "total_amount": Decimal("20"), "bank_account": secret, "remark": secret,
                "items": [{"product_name": "商品", "qty": Decimal("2"), "amount": Decimal("20"), "extra": {"api_key": secret}}] * 101}
        result = query_projection.purchase_order(data, access)
        assert len(result["items"]) == 100 and result["items_truncated"]
        assert secret not in str(result)
        restricted = replace(access, roles=frozenset({"WAREHOUSE"}))
        result = query_projection.purchase_order(data, restricted)
        assert "total_amount" not in result and "amount" not in result["items"][0]
        with pytest.raises(BizException):
            query_projection.purchase_order({"order_no": {"secret": secret}}, access)
        bind(db, 13)
        print_access = authorize_query(db, "print_batch")
        result = query_projection.print_batch({"batch_no": "B", "orders": [{"order_no": "O", "receiver_phone": "13812345678", "receiver_address": secret, "password": secret}], "pick_list": {"address": secret}}, print_access)
        assert result["orders"][0]["receiver_phone"] == "****5678"
        assert result["orders"][0]["receiver_address"] == "已脱敏"
        assert secret not in str(result)
        assert query_projection.mask_phone("1234") == "已脱敏"


def test_every_public_projection_rejects_a_mismatched_capability(factory):
    with factory() as db:
        bind(db, 10)
        valid_access = authorize_query(db, "product_catalog")

    projectors = {
        name: function
        for name, function in vars(query_projection).items()
        if not name.startswith("_")
        and inspect.isfunction(function)
        and "access" in inspect.signature(function).parameters
    }
    assert len(projectors) >= 60
    invalid_access = replace(valid_access, capability="not_a_registered_capability")
    for name, projector in projectors.items():
        with pytest.raises(BizException, match="字段策略不匹配"):
            projector({}, invalid_access)


def test_projection_rejects_malformed_or_oversized_query_summaries(factory):
    with factory() as db:
        bind(db, 10)
        access_by_capability = {
            capability: authorize_query(db, capability)
            for capability in (
                "product_catalog", "purchase_orders", "purchase_plans",
                "approval_business", "approval_configuration", "sales_orders",
                "print_batch",
            )
        }

    invalid_payloads = [
        (query_projection.purchase_order, "purchase_orders", {"items": {}}),
        (query_projection.purchase_plan, "purchase_plans", {"items": {}}),
        (query_projection.product, "product_catalog", {"skus": {}}),
        (query_projection.approval_records, "approval_business", {"records": {}}),
        (query_projection.approval_records, "approval_business", {"records": [{}] * 101}),
        (query_projection.approval_flow_config, "approval_configuration", {"steps": {}}),
        (query_projection.approval_flow_config, "approval_configuration", {"steps": [{}] * 101}),
        (query_projection.approval_flow_config, "approval_configuration", {"steps": [None]}),
        (query_projection.my_approval_detail, "approval_business", {"doc_type": "unknown"}),
        (query_projection.my_approval_detail, "approval_business", {
            "doc_type": "purchase_order", "detail": [], "approval": {},
        }),
        (query_projection.my_approval_detail, "approval_business", {
            "doc_type": "purchase_order", "detail": {}, "approval": [],
        }),
        (query_projection.my_approval_detail, "approval_business", {
            "doc_type": "purchase_order", "detail": {}, "approval": {}, "flow": [],
        }),
        (query_projection.my_approval_detail, "approval_business", {
            "doc_type": "purchase_order", "detail": {}, "approval": {}, "records": {},
        }),
        (query_projection.sales_order, "sales_orders", {"flags": "not-an-array"}),
        (query_projection.sales_order_log, "sales_orders", {"source": "UNKNOWN"}),
        (query_projection.print_batch, "print_batch", {"orders": {}}),
    ]
    for projector, capability, payload in invalid_payloads:
        with pytest.raises(BizException):
            projector(payload, access_by_capability[capability])

    with pytest.raises(BizException):
        query_projection._pick("not-an-object", frozenset({"field"}))


def test_rest_business_and_configuration_views_and_subfunction_denial(factory):
    app = FastAPI()
    register_exception_handlers(app)
    for router in (approval_router, approval_manage_router, purchase_router):
        app.include_router(router)
    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)
    app.dependency_overrides[get_db] = sessions
    with factory() as db:
        db.add_all([ApprovalFlow(company_id=1, name="采购流", doc_type="purchase_order", approvers="[12]", created_by=10),
                    ApprovalFlow(company_id=1, name="付款流", doc_type="purchase_prepay", approvers="[11,12]", created_by=10)])
        db.commit()
    client = TestClient(app)
    def headers(staff_id):
        return {"Authorization": "Bearer " + create_access_token(staff_id, "test", [])}
    rows = client.get("/approval/flows", headers=headers(11)).json()["data"]
    assert len(rows) == 1 and rows[0]["doc_type"] == "purchase_prepay"
    assert set(rows[0]) == {"id", "name", "doc_type"}
    assert client.get("/approval-manage/flows", headers=headers(1)).status_code == 403
    assert client.get("/approval-manage/flows", headers=headers(10)).status_code == 200
    assert client.get("/purchase-orders", headers=headers(11)).status_code == 403
    assert client.get("/purchase-orders/1", headers=headers(14)).status_code == 403
    assert client.get("/approval/records/purchase_order/2", headers=headers(1)).status_code == 404
    assert client.get("/approval/records/purchase_order/1", headers=headers(1)).status_code == 200


def test_order_approval_history_requires_business_access_or_participation(factory):
    with factory() as db:
        db.add(Order(id=1, company_id=1, order_no="O1", shop_id=1))
        db.commit()
        bind(db, 1)
        with pytest.raises(BizException):
            shared_queries.approval_state(db, doc_type="order", doc_id=1)
        db.add(ApprovalRecord(company_id=1, doc_type="order", doc_id=1, approver_id=1, action="APPROVE", status="APPROVED"))
        db.commit()
        assert shared_queries.approval_state(db, doc_type="order", doc_id=1)["doc_id"] == 1
        add_other_company(db)
        # 第二家公司存在不再触发全局拒绝；审批行仍按当前凭据公司过滤。
        state = shared_queries.approval_state(db, doc_type="order", doc_id=1)
        assert state["doc_id"] == 1
        assert len(state["records"]) == 1


def test_print_rechecks_orders_and_rejects_inconsistent_snapshot(factory, monkeypatch):
    from app.services import print_service

    data = {"batch_no": "B1", "order_count": 99, "orders": [{"id": 1, "order_no": "O1", "receiver_phone": "13912345678"}],
            "waybills": [{"order_id": 1}], "delivery_lists": [{"order_id": 1}], "pick_list": {"rows": [{"order_id": 1}]}}
    monkeypatch.setattr(print_service, "get_print_batch", lambda *_args, **_kw: dict(data))
    with factory() as db:
        db.add(Order(id=1, company_id=1, order_no="O1", shop_id=1))
        db.commit()
        bind(db, 13)
        result = shared_queries.external_print_batch(db, batch_no="B1")
        assert result["order_count"] == 1
        assert result["orders"][0]["receiver_phone"] == "****5678"
        data["pick_list"]["rows"].append({"order_id": 999})
        with pytest.raises(BizException):
            shared_queries.external_print_batch(db, batch_no="B1")
        data["pick_list"]["rows"].pop()
        db.execute(delete(Order).where(Order.id == 1))
        db.commit()
        with pytest.raises(BizException):
            shared_queries.print_batch(db, batch_no="B1")


def test_mysql_smoke_script_rejects_any_write_statement():
    from scripts.check_task098_readonly import _no_flush, _only_select

    for sql in ("INSERT INTO anything VALUES(1)", "UPDATE anything SET value=1", "DELETE FROM anything", "DROP TABLE anything", "SET TRANSACTION READ WRITE"):
        with pytest.raises(RuntimeError):
            _only_select(None, None, sql, None, None, None)
    _only_select(None, None, "SELECT 1", None, None, None)
    _only_select(None, None, "SET TRANSACTION READ ONLY", None, None, None)
    with pytest.raises(RuntimeError):
        _no_flush()
