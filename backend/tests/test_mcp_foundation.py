"""TASK-097：隔离单元/协议验证；不作为真实MySQL或WorkBuddy验收。"""

import json
import threading
import time
from datetime import timedelta
from typing import Any, Literal, get_args, get_origin

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.mcp_credentials import router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base, CompanyOwnedBase
from app.db.session import get_db
from app.mcp_server import create_server
from app.models.audit_log import AuditLog
from app.models.mcp_credential import McpCredential
from app.models.org import Company, Staff
from app.models.product import ComboProduct, ComboProductItem, Product, ProductSku
from app.models.rbac import Role, StaffRole
from app.services.mcp_credentials import (
    ACCESS_TOOL, authenticate_credential, issue_credential, list_credentials,
    revoke_credential, utc_now,
)
from app.services.mcp_limits import QueryLimitExceeded
from app.services.mcp_runtime import McpRuntime, QueryPage


class MemoryLimits:
    """运行时单测替身；生产跨进程治理使用Redis原子Lua。"""
    def __init__(self, rpm=60, concurrent=2):
        self.rpm, self.concurrent = rpm, concurrent
        self.history, self.active = {}, {}
        self.lock = threading.Lock()

    def acquire(self, company_id, staff_id):
        key = (company_id, staff_id)
        with self.lock:
            if self.history.get(key, 0) >= self.rpm or self.active.get(key, 0) >= self.concurrent:
                raise QueryLimitExceeded
            self.history[key] = self.history.get(key, 0) + 1
            self.active[key] = self.active.get(key, 0) + 1
        return key

    def release(self, lease):
        with self.lock:
            self.active[lease] -= 1


_NONEMPTY_SUCCESSFUL_TOOL_CALLS: set[str] = set()


def _has_nonempty_query_data(data: Any) -> bool:
    if isinstance(data, list):
        return bool(data)
    if isinstance(data, dict):
        rows = data.get("items")
        if isinstance(rows, list):
            return bool(rows)
        return bool(data)
    return False


@pytest.fixture(scope="module", autouse=True)
def every_registered_tool_has_runtime_success_evidence(request):
    """Full foundation runs must exercise every tool with valid non-empty data."""
    args = [str(arg).replace("\\", "/") for arg in request.config.args]
    option = request.config.option
    is_partial_selection = (
        any("::" in arg for arg in args)
        or any(bool(getattr(option, name, None)) for name in (
            "keyword", "markexpr", "lf", "ff", "stepwise", "sw",
            "ignore", "ignore_glob", "deselect",
        ))
    )
    yield
    if is_partial_selection or option.collectonly:
        return

    from app.services.mcp_tool_registry import MCP_TOOL_SPECS

    missing = set(MCP_TOOL_SPECS) - _NONEMPTY_SUCCESSFUL_TOOL_CALLS
    assert not missing, (
        "完整MCP基础回归中没有观察到非空成功结果的工具："
        + ", ".join(sorted(missing))
    )


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp-unit.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def backfill_legacy_fixture_rows(session, _flush_context, _instances):
        # Historical fixtures model migration 0030's company-1 fallback unless
        # a test explicitly supplies another company's owner.
        for row in session.new:
            if isinstance(row, CompanyOwnedBase) and row.company_id is None:
                row.company_id = 1

    event.listen(factory.class_, "before_flush", backfill_legacy_fixture_rows)
    with factory() as db:
        db.add_all([Company(id=1, full_name="测试一"), Company(id=2, full_name="测试二")])
        db.flush()
        db.add_all([Staff(id=1, company_id=1, name="甲"), Staff(id=2, company_id=2, name="乙")])
        db.add_all([Role(id=1, company_id=1, code="PURCHASER", name="采购"),
                    Role(id=2, company_id=2, code="WAREHOUSE", name="仓库")])
        db.flush()
        db.add_all([StaffRole(staff_id=1, role_id=1), StaffRole(staff_id=2, role_id=2)])
        db.commit()
    yield factory
    event.remove(factory.class_, "before_flush", backfill_legacy_fixture_rows)
    engine.dispose()


def issue(factory, staff_id=1, *, tool_scopes=None, permission_scopes=None):
    with factory() as db:
        return issue_credential(db, db.get(Staff, staff_id), name="测试",
                                permission_scopes=permission_scopes or ["module:purchase" if staff_id == 1 else "module:stock"],
                                tool_scopes=tool_scopes or [ACCESS_TOOL])


def runtime(factory, token, **kwargs):
    return McpRuntime(read_factory=factory, audit_factory=factory, token=token,
                      limits=kwargs.pop("limits", MemoryLimits()), **kwargs)


def _schema_mismatch(value: Any, schema: dict[str, Any], path: str = "data") -> str | None:
    branches = schema.get("anyOf")
    if branches is not None:
        if any(_schema_mismatch(value, branch, path) is None for branch in branches):
            return None
        return f"{path} 不匹配 anyOf"

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            return f"{path} 应为 object，实际为 {type(value).__name__}"
        properties = schema.get("properties", {})
        missing = set(schema.get("required", ())) - set(value)
        if missing:
            return f"{path} 缺少必需字段：{sorted(missing)}"
        unexpected = set(value) - set(properties)
        if unexpected and schema.get("additionalProperties") is False:
            return f"{path} 存在未声明字段：{sorted(unexpected)}"
        for name, child in value.items():
            if name in properties:
                mismatch = _schema_mismatch(value[name], properties[name], f"{path}.{name}")
                if mismatch:
                    return mismatch
        return None
    if expected == "array":
        if not isinstance(value, list):
            return f"{path} 应为 array，实际为 {type(value).__name__}"
        for index, child in enumerate(value):
            mismatch = _schema_mismatch(child, schema["items"], f"{path}[{index}]")
            if mismatch:
                return mismatch
        return None
    valid = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }.get(expected)
    if valid is not None and not valid(value):
        return f"{path} 应为 {expected}，实际为 {type(value).__name__}"
    return None


def _schema_objects_are_closed(schema: dict[str, Any]) -> bool:
    if "anyOf" in schema:
        return all(_schema_objects_are_closed(branch) for branch in schema["anyOf"])
    if schema.get("type") == "object":
        return schema.get("additionalProperties") is False and all(
            _schema_objects_are_closed(child)
            for child in schema.get("properties", {}).values()
        )
    if schema.get("type") == "array":
        return _schema_objects_are_closed(schema["items"])
    return True


def call(rt, name=ACCESS_TOOL, arguments=None):
    result = anyio.run(lambda: rt.execute(name, arguments or {}))
    if not result.isError and name != ACCESS_TOOL:
        from app.services.mcp_tool_output_schemas import get_tool_data_schema

        mismatch = _schema_mismatch(
            result.structuredContent["data"], get_tool_data_schema(name),
        )
        assert mismatch is None, f"{name}: {mismatch}"
        if _has_nonempty_query_data(result.structuredContent["data"]):
            _NONEMPTY_SUCCESSFUL_TOOL_CALLS.add(name)
    return result


def test_output_schema_catalog_covers_every_registered_tool():
    from app.services.mcp_tool_output_schemas import (
        _OPTIONAL_OUTPUT_PATHS_BY_TOOL, _TOOL_DATA_SCHEMAS,
        get_tool_data_schema,
    )
    from app.services.mcp_tool_registry import MCP_TOOL_SPECS
    from app.services.query_projection import (
        _APPROVAL_DETAIL_FIELDS, _APPROVAL_DETAIL_MONEY_FIELDS,
    )

    assert set(_TOOL_DATA_SCHEMAS) == set(MCP_TOOL_SPECS)
    assert set(_OPTIONAL_OUTPUT_PATHS_BY_TOOL) <= set(MCP_TOOL_SPECS)
    for name in MCP_TOOL_SPECS:
        schema = get_tool_data_schema(name)
        assert schema.get("type") in {"object", "array"} or "anyOf" in schema, name
        assert _schema_objects_are_closed(schema), name

    def branches(schema):
        if "anyOf" in schema:
            return [branch for item in schema["anyOf"] for branch in branches(item)]
        return [schema]

    for tool_name, paths in _OPTIONAL_OUTPUT_PATHS_BY_TOOL.items():
        for path in paths:
            nodes = [get_tool_data_schema(tool_name)]
            owners = []
            for part in path.split("."):
                if part == "[]":
                    arrays = [
                        node for candidate in nodes for node in branches(candidate)
                        if node.get("type") == "array"
                    ]
                    assert arrays, f"{tool_name}: optional path {path} is not an array path"
                    nodes = [array["items"] for array in arrays]
                    continue
                is_array = part.endswith("[]")
                field = part[:-2] if is_array else part
                owners = [
                    node for candidate in nodes for node in branches(candidate)
                    if node.get("type") == "object"
                    and field in node.get("properties", {})
                ]
                assert owners, f"{tool_name}: optional path {path} is undeclared"
                nodes = [owner["properties"][field] for owner in owners]
                if is_array:
                    arrays = [
                        node for candidate in nodes for node in branches(candidate)
                        if node.get("type") == "array"
                    ]
                    assert arrays, f"{tool_name}: optional path {path} is not an array path"
                    nodes = [array["items"] for array in arrays]
            assert all(field not in owner.get("required", []) for owner in owners), (
                f"{tool_name}: optional path {path} is marked required"
            )

    freight = get_tool_data_schema("erp_logistics_list_freight_templates")
    freight_item = freight["items"]
    assert {"id", "name"} <= set(freight_item["required"])
    assert "base_fee" not in freight_item["required"]
    assert _OPTIONAL_OUTPUT_PATHS_BY_TOOL["erp_logistics_list_freight_templates"]
    assert _schema_mismatch(
        {"read_state": "UNREAD"},
        get_tool_data_schema("erp_get_my_notification"),
    ) is not None

    approval_detail_schema = get_tool_data_schema(
        "erp_get_my_approval_detail",
    )["properties"]["detail"]
    approval_variants = approval_detail_schema["anyOf"]
    assert len(approval_variants) == len(_APPROVAL_DETAIL_FIELDS) == 13
    variant_contracts = {
        (frozenset(variant["properties"]), frozenset(variant["required"]))
        for variant in approval_variants
    }
    for doc_type, fields in _APPROVAL_DETAIL_FIELDS.items():
        money_fields = _APPROVAL_DETAIL_MONEY_FIELDS.get(doc_type, frozenset())
        optional_fields = set(money_fields)
        if doc_type == "stock_in":
            optional_fields.add("supplier_name")
        if doc_type == "stock_check":
            optional_fields.add("total_qty")
        properties = frozenset(fields | money_fields)
        assert (
            properties,
            frozenset(properties - optional_fields),
        ) in variant_contracts, doc_type

    def sample_value(schema):
        if "anyOf" in schema:
            for branch in schema["anyOf"]:
                if branch.get("type") != "null":
                    return sample_value(branch)
            return None
        if schema.get("type") == "object":
            return {
                name: sample_value(schema["properties"][name])
                for name in schema.get("required", ())
            }
        if schema.get("type") == "array":
            return []
        return {
            "string": "sample", "integer": 1, "boolean": False,
            "null": None,
        }.get(schema.get("type"))

    for variant in approval_variants:
        detail = sample_value(variant)
        assert _schema_mismatch(detail, variant) is None
        assert _schema_mismatch({**detail, "remark": "private"}, variant) is not None


def test_approval_detail_fixed_output_schema_covers_all_document_types(
    factory, monkeypatch,
):
    from sqlalchemy import delete
    from app.services import approval_manage_service
    from app.services.mcp_tool_output_schemas import get_tool_data_schema
    from app.services.query_projection import (
        _APPROVAL_DETAIL_FIELDS, _APPROVAL_DETAIL_MONEY_FIELDS,
    )

    def sample_value(schema):
        if "anyOf" in schema:
            for branch in schema["anyOf"]:
                if branch.get("type") != "null":
                    return sample_value(branch)
            return None
        return {
            "string": "sample", "integer": 1, "boolean": False,
            "null": None,
        }.get(schema.get("type"))

    root_schema = get_tool_data_schema("erp_get_my_approval_detail")
    detail_schema = root_schema["properties"]["detail"]
    detail_variants = detail_schema["anyOf"]
    detail_inputs = {}
    for doc_type, base_fields in _APPROVAL_DETAIL_FIELDS.items():
        expected_fields = base_fields | _APPROVAL_DETAIL_MONEY_FIELDS.get(
            doc_type, frozenset(),
        )
        variant = next(
            candidate for candidate in detail_variants
            if set(candidate["properties"]) == set(expected_fields)
        )
        detail_inputs[doc_type] = {
            field: sample_value(field_schema)
            for field, field_schema in variant["properties"].items()
        }

    current = {}

    def get_detail(_db, *, doc_type, doc_id, staff, compact):
        assert compact is True
        return current["payload"]

    monkeypatch.setattr(
        approval_manage_service, "get_my_approval_detail", get_detail,
    )
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()
    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_get_my_approval_detail"],
        permission_scopes=["module:approval", "module:purchase"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    approval_only_token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_get_my_approval_detail"],
        permission_scopes=["module:approval"],
    )["token"]
    approval_only_rt = runtime(
        factory, approval_only_token, limits=MemoryLimits(rpm=100),
    )

    for doc_id, (doc_type, detail) in enumerate(detail_inputs.items(), start=1):
        doc_type_label = approval_manage_service.DOC_TYPE_LABELS[doc_type]
        current["payload"] = {
            "doc_type": doc_type,
            "doc_type_label": doc_type_label,
            "doc_id": doc_id,
            "doc_no": f"MCP-APPROVAL-{doc_id}",
            "approval": {
                "flow_name": "隔离审批流",
                "state": {
                    "status": "PENDING", "current_step": 1,
                    "total_steps": 1, "approved_steps": [],
                    "approved_steps_truncated": False,
                },
            },
            "flow": {
                "id": 1, "name": "隔离审批流", "doc_type": doc_type,
                "doc_type_label": doc_type_label,
            },
            "records": [{
                "id": doc_id, "step": 1, "step_name": None,
                "action": "SUBMIT", "status": "DONE",
                "audit_time": None, "created_at": "2026-10-13T10:00:00",
            }],
            "can_approve": False, "can_reject": False,
            "can_unaudit": False,
            "detail": {
                **detail, "remark": "PRIVATE-REMARK",
                "account_no": "PRIVATE-BANK-ACCOUNT",
            },
        }
        response = call(rt, "erp_get_my_approval_detail", {
            "doc_type": doc_type, "doc_id": doc_id,
        })
        assert not response.isError, f"{doc_type}: {response.model_dump_json()}"
        result = response.structuredContent["data"]
        assert result["detail"] == detail, doc_type
        assert not {"remark", "account_no"} & set(result["detail"])

        approval_only_result = call(
            approval_only_rt, "erp_get_my_approval_detail", {
                "doc_type": doc_type, "doc_id": doc_id,
            },
        ).structuredContent["data"]
        money_fields = _APPROVAL_DETAIL_MONEY_FIELDS.get(doc_type, frozenset())
        assert approval_only_result["detail"] == {
            field: value for field, value in detail.items()
            if field not in money_fields
        }, doc_type

    current["payload"]["flow"] = None
    current["payload"]["doc_no"] = None
    current["payload"]["approval"]["flow_name"] = None
    current["payload"]["approval"]["state"]["current_step"] = None
    no_flow_result = call(approval_only_rt, "erp_get_my_approval_detail", {
        "doc_type": "order", "doc_id": len(detail_inputs),
    }).structuredContent["data"]
    assert "flow" not in no_flow_result


def test_current_access_tool_matches_rest_identity_and_only_reports_credential_permissions(factory):
    from app.api.auth import router as auth_router
    from app.core.data_scope import clear_scope_policy
    from app.core.permissions import visible_modules

    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_search_purchase_plans"],
        permission_scopes=["module:purchase"],
    )["token"]
    mcp_data = call(runtime(factory, token), ACCESS_TOOL, {}).structuredContent["data"]

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(auth_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get(
            "/auth/me/permissions",
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
    assert rest_response.status_code == 200
    rest_data = rest_response.json()["data"]

    assert mcp_data["staff_id"] == rest_data["staff_id"] == 1
    assert set(mcp_data["permissions"]) == {"module:purchase"}
    assert set(mcp_data["permissions"]) <= set(rest_data["permissions"])
    assert mcp_data["modules"] == visible_modules(set(mcp_data["permissions"]))
    assert set(mcp_data["modules"]) <= set(rest_data["modules"])
    assert mcp_data["company_id"] == 1
    assert mcp_data["data_scope"] == "SELF"
    assert set(mcp_data["tools"]) == {ACCESS_TOOL, "erp_search_purchase_plans"}
    assert set(mcp_data) == {
        "staff_id", "company_id", "permissions", "modules", "data_scope", "tools",
    }


def _valid_required_tool_arguments(spec):
    arguments = {}
    for field_name, field in spec.arguments.model_fields.items():
        if not field.is_required():
            continue
        annotation = field.annotation
        if get_origin(annotation) is Literal:
            value = get_args(annotation)[0]
        elif annotation is int:
            value = 999999
        elif annotation is str:
            value = (
                "00000000-0000-0000-0000-000000000099"
                if field_name == "request_id" else "TASK099-NOT-FOUND"
            )
        else:
            pytest.fail(f"未登记的必填参数类型：{spec.name}.{field_name} ({annotation!r})")
        arguments[field_name] = value
    return arguments


def test_every_registered_tool_rejects_unknown_arguments_in_mcp_runtime(factory):
    from app.core.permissions import MODULES
    from app.services.mcp_tool_registry import MCP_TOOL_SPECS

    with factory() as db:
        db.add(Role(id=3, company_id=1, code="ADMIN", name="管理员"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()

    permissions = [f"module:{module}" for module in MODULES]
    tools = [ACCESS_TOOL, *MCP_TOOL_SPECS]
    token = issue(
        factory, staff_id=1, permission_scopes=permissions, tool_scopes=tools,
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=500))
    for tool_name in MCP_TOOL_SPECS:
        result = call(rt, tool_name, {"unregistered_argument": True})
        assert result.isError, tool_name
        assert result.structuredContent["code"] == "INVALID_ARGUMENT", tool_name

        spec = MCP_TOOL_SPECS[tool_name]
        if any(field.is_required() for field in spec.arguments.model_fields.values()):
            missing_required = call(rt, tool_name, {})
            assert missing_required.isError, tool_name
            assert missing_required.structuredContent["code"] == "INVALID_ARGUMENT", tool_name


def test_every_registered_tool_is_denied_without_matching_credential_scope(factory):
    from app.services.mcp_tool_registry import MCP_TOOL_SPECS

    token = issue(
        factory, staff_id=1, permission_scopes=["module:purchase"],
        tool_scopes=[ACCESS_TOOL],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=500))
    for tool_name in MCP_TOOL_SPECS:
        result = call(rt, tool_name)
        assert result.isError, tool_name
        assert result.structuredContent["code"] == "FORBIDDEN", tool_name


def test_every_registered_tool_requires_its_current_module_permission(factory):
    from app.core.query_access import CAPABILITIES
    from app.services.mcp_tool_registry import MCP_TOOL_SPECS

    with factory() as db:
        db.add(Role(id=3, company_id=1, code="ADMIN", name="管理员"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()

    tools = [ACCESS_TOOL, *MCP_TOOL_SPECS]
    token = issue(
        factory, staff_id=1, permission_scopes=["module:dashboard"],
        tool_scopes=tools,
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=500, concurrent=1))
    for name, spec in MCP_TOOL_SPECS.items():
        result = call(rt, name, _valid_required_tool_arguments(spec))
        capability = CAPABILITIES[spec.capability]
        required = (
            {capability.permission}
            if isinstance(capability.permission, str) else set(capability.permission)
        )
        required = {
            {"approval:configure": "module:approval", "settings:manage": "module:settings"}.get(p, p)
            for p in required if p
        }
        if not required or "module:dashboard" in required:
            assert not result.isError or result.structuredContent["code"] == "FORBIDDEN", name
        else:
            assert result.isError and result.structuredContent["code"] == "FORBIDDEN", name


def test_every_registered_tool_rechecks_live_role_permissions_after_credential_issue(factory):
    from app.core.permissions import MODULES, permissions_for_roles
    from app.core.query_access import CAPABILITIES
    from app.services.mcp_tool_registry import MCP_TOOL_SPECS

    tool_scopes = [ACCESS_TOOL, *MCP_TOOL_SPECS]
    permission_scopes = [f"module:{module}" for module in MODULES]
    with factory() as db:
        for assignment in db.scalars(
            select(StaffRole).where(StaffRole.staff_id == 2)
        ).all():
            db.delete(assignment)
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        admin = Role(id=3, company_id=1, code="ADMIN", name="管理员")
        db.add(admin)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=admin.id))
        db.commit()
        created = issue_credential(
            db, db.get(Staff, 1), name="TASK-099实时权限回归",
            permission_scopes=permission_scopes, tool_scopes=tool_scopes,
        )

    rt = runtime(
        factory, created["token"],
        limits=MemoryLimits(rpm=2 * len(MCP_TOOL_SPECS) + 20),
    )
    assert not call(rt, ACCESS_TOOL, {}).isError

    # 令牌仍保留全部工具/模块scope；实时角色降级后必须只留下仓储能力。
    with factory() as db:
        purchaser_assignment = db.scalar(select(StaffRole).where(
            StaffRole.staff_id == 1, StaffRole.role_id == 1,
        ))
        if purchaser_assignment is not None:
            db.delete(purchaser_assignment)
        warehouse_role = db.get(Role, 3)
        assert warehouse_role is not None
        warehouse_role.code = "WAREHOUSE"
        db.flush()
        db.commit()

    live_permissions = permissions_for_roles(["WAREHOUSE"])
    warehouse_results = {}
    for tool_name, spec in MCP_TOOL_SPECS.items():
        result = call(rt, tool_name, _valid_required_tool_arguments(spec))
        warehouse_results[tool_name] = result
        capability = CAPABILITIES[spec.capability]
        required = (
            {capability.permission}
            if isinstance(capability.permission, str)
            else set(capability.permission or ())
        )
        required = {
            {"approval:configure": "module:approval", "settings:manage": "module:settings"}.get(p, p)
            for p in required if p
        }
        if required and not required.intersection(live_permissions):
            assert result.isError, tool_name
            assert result.structuredContent["code"] == "FORBIDDEN", tool_name

    assert not warehouse_results["erp_stock_query_list_stock_summary"].isError
    assert warehouse_results["erp_search_products"].structuredContent["code"] == "FORBIDDEN"

    # 再撤销剩余角色，旧令牌必须对所有工具立即失效。
    with factory() as db:
        for assignment in db.scalars(
            select(StaffRole).where(StaffRole.staff_id == 1)
        ).all():
            db.delete(assignment)
        db.commit()

    for tool_name, spec in MCP_TOOL_SPECS.items():
        result = call(rt, tool_name, _valid_required_tool_arguments(spec))
        assert result.isError, tool_name
        assert result.structuredContent["code"] == "FORBIDDEN", tool_name


def test_every_registered_tool_runs_against_empty_company_without_business_writes(factory):
    from app.core.permissions import MODULES
    from app.models.audit_log import AuditLog
    from app.services.mcp_tool_registry import MCP_TOOL_SPECS

    # 保留第二家公司：逐工具验证授权后的公司隔离查询在多公司实例也可执行。
    with factory() as db:
        admin = Role(id=3, company_id=1, code="ADMIN", name="管理员")
        db.add(admin)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=admin.id))
        db.commit()

    tools = [ACCESS_TOOL, *MCP_TOOL_SPECS]
    token = issue(
        factory, staff_id=1, permission_scopes=[f"module:{m}" for m in MODULES],
        tool_scopes=tools,
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=500, concurrent=1))

    def business_rows_snapshot():
        with factory() as db:
            return {
                table.name: sorted(repr(tuple(row)) for row in db.execute(select(table)).all())
                for table in Base.metadata.sorted_tables
                if table.name != AuditLog.__tablename__
            }

    before = business_rows_snapshot()
    successes = 0
    expected_missing = 0
    paginated_probes = 0
    sql_bounded_page_tools = set()
    unbounded_page_tools = []
    for name, spec in MCP_TOOL_SPECS.items():
        result = call(rt, name, _valid_required_tool_arguments(spec))
        if result.isError:
            code = result.structuredContent["code"]
            assert code == "FORBIDDEN", f"{name}: {code}"
            assert any(field.is_required() for field in spec.arguments.model_fields.values()), (
                f"无必填实体参数的列表/搜索工具必须在空业务库中成功：{name}"
            )
            expected_missing += 1
            continue

        payload = result.structuredContent
        assert payload["schema_version"] == "1.0", name
        assert isinstance(payload["data"], (list, dict)), name
        assert "warnings" in payload, name
        page = payload.get("page")
        if isinstance(payload["data"], list) and not payload["data"] and page is not None:
            assert page["total"] == 0, name
        successes += 1

        if {"page", "page_size"} <= set(spec.arguments.model_fields):
            paginated_probes += 1
            paged_arguments = _valid_required_tool_arguments(spec)
            paged_arguments.update(page=2, page_size=1)
            page_statements = []

            def capture_page_select(_conn, _cursor, statement, _parameters, _context, _executemany):
                if statement.lstrip().upper().startswith("SELECT"):
                    page_statements.append(statement)

            engine = factory.kw["bind"]
            event.listen(engine, "before_cursor_execute", capture_page_select)
            try:
                paged_result = call(rt, name, paged_arguments)
            finally:
                event.remove(engine, "before_cursor_execute", capture_page_select)
            if paged_result.isError:
                assert paged_result.structuredContent["code"] == "FORBIDDEN", name
                continue
            paged_payload = paged_result.structuredContent
            assert paged_payload["page"]["page"] == 2, name
            assert paged_payload["page"]["page_size"] == 1, name
            assert isinstance(paged_payload["page"]["has_more"], bool), name
            if isinstance(paged_payload["data"], list):
                assert len(paged_payload["data"]) <= 1, name
            has_sql_page_bound = any(
                "LIMIT" in statement.upper() or "ROW_NUMBER()" in statement.upper()
                for statement in page_statements
            )
            if has_sql_page_bound:
                sql_bounded_page_tools.add(name)
            else:
                unbounded_page_tools.append(name)
            if paged_payload["page"]["total"] == 0:
                assert paged_payload["page"]["has_more"] is False, name
                if isinstance(paged_payload["data"], list):
                    assert paged_payload["data"] == [], name
                elif isinstance(paged_payload["data"], dict):
                    assert not paged_payload["data"].get("items", []), name

    assert successes + expected_missing == len(MCP_TOOL_SPECS)
    assert successes > 0
    assert paginated_probes > 0
    assert not unbounded_page_tools, (
        "成功返回分页响应的工具必须执行含SQL边界的SELECT："
        + ", ".join(unbounded_page_tools)
    )
    assert sql_bounded_page_tools, "没有捕获到成功分页工具的SQL边界查询"
    assert business_rows_snapshot() == before


def test_token_digest_once_and_owner_revoke(factory):
    created = issue(factory)
    with factory() as db:
        row = db.get(McpCredential, created["id"])
        assert row.token_digest != created["token"] and len(row.token_digest) == 64
        assert "token" not in list_credentials(db, db.get(Staff, 1))[0]
        with pytest.raises(BizException):
            revoke_credential(db, db.get(Staff, 2), row.id)
        revoke_credential(db, db.get(Staff, 1), row.id)
    assert call(runtime(factory, created["token"])).structuredContent["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("change", ["expired", "inactive", "role_removed", "company_changed", "missing", "malformed"])
def test_current_identity_is_checked(factory, change):
    token = issue(factory)["token"]
    with factory() as db:
        if change == "expired":
            db.scalar(select(McpCredential)).expires_at = utc_now() - timedelta(seconds=1)
        elif change == "inactive":
            db.get(Staff, 1).is_active = False
        elif change == "role_removed":
            db.delete(db.scalar(select(StaffRole).where(StaffRole.staff_id == 1)))
        elif change == "company_changed":
            db.get(Staff, 1).company_id = 2
        elif change == "missing":
            token = ""
        elif change == "malformed":
            token = "erp_mcp_" + "x" * 200
        db.commit()
    result = call(runtime(factory, token))
    assert result.isError and result.structuredContent["code"] in {"UNAUTHENTICATED", "FORBIDDEN"}


def test_scope_cannot_be_escalated(factory):
    with factory() as db:
        for scopes, tools in [(["module:finance"], [ACCESS_TOOL]), (["*"], [ACCESS_TOOL]),
                              (["module:purchase"], ["execute_sql"])]:
            with pytest.raises(BizException):
                issue_credential(db, db.get(Staff, 1), name="bad", permission_scopes=scopes, tool_scopes=tools)


def test_arguments_unknown_tool_paging_and_secret_redaction(factory, caplog):
    token = issue(factory)["token"]
    rt = runtime(factory, token)
    result = call(rt, arguments={"staff_id": 2, "secret": token})
    assert result.structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "execute_sql").structuredContent["code"] == "UNKNOWN_TOOL"
    assert token not in result.model_dump_json() + caplog.text
    with pytest.raises(ValidationError):
        QueryPage(page_size=101)
    with pytest.raises(ValidationError):
        QueryPage(page=True)
    with factory() as db:
        assert token not in json.dumps([r.detail for r in db.scalars(select(AuditLog))])


def test_result_size_rate_and_audit_failure(factory, monkeypatch, caplog):
    token = issue(factory)["token"]
    rt = runtime(factory, token, max_result_bytes=1)
    assert call(rt).structuredContent["code"] == "RESULT_TOO_LARGE"
    rt = runtime(factory, token, limits=MemoryLimits(rpm=1))
    assert not call(rt).isError
    assert call(rt).structuredContent["code"] == "RATE_LIMITED"
    def broken(**kwargs):
        raise RuntimeError(token)
    monkeypatch.setattr(rt, "_audit", broken)
    assert call(rt).structuredContent["code"] == "DEPENDENCY_UNAVAILABLE"
    assert token not in caplog.text


def test_timeout_and_cancel_keep_lease_until_worker_finishes(factory, monkeypatch):
    token = issue(factory)["token"]
    limits = MemoryLimits()
    rt = runtime(factory, token, limits=limits, timeout_seconds=0.05)
    original = rt._identity_data
    started = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    finished = [threading.Event(), threading.Event()]
    call_lock = threading.Lock()
    call_count = 0

    def slow(identity):
        nonlocal call_count
        with call_lock:
            call_index = call_count
            call_count += 1
        started[call_index].set()
        try:
            release[call_index].wait(timeout=10)
            return original(identity)
        finally:
            finished[call_index].set()

    monkeypatch.setattr(rt, "_identity_data", slow)

    async def wait_for_release(lease):
        for _ in range(100):
            if limits.active.get(lease, 0) == 0:
                return
            await anyio.sleep(0.01)
        assert limits.active.get(lease, 0) == 0

    async def run():
        result = await rt.execute(ACCESS_TOOL, {})
        assert result.structuredContent["code"] == "TIMEOUT"
        assert await anyio.to_thread.run_sync(started[0].wait, 5)
        assert limits.active[(1, 1)] == 1
        release[0].set()
        assert await anyio.to_thread.run_sync(finished[0].wait, 5)
        await wait_for_release((1, 1))
        cancellation_observed = anyio.Event()

        async def cancel_after_worker_starts():
            try:
                await rt.execute(ACCESS_TOOL, {})
            except BaseException:
                cancellation_observed.set()
                raise

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancel_after_worker_starts)
            assert await anyio.to_thread.run_sync(started[1].wait, 5)
            task_group.cancel_scope.cancel()
            with anyio.CancelScope(shield=True):
                await cancellation_observed.wait()
        assert limits.active[(1, 1)] == 1
        release[1].set()
        assert await anyio.to_thread.run_sync(finished[1].wait, 5)
        await wait_for_release((1, 1))

    anyio.run(run)
    with factory() as db:
        codes = [json.loads(r.detail)["code"] for r in db.scalars(select(AuditLog).where(AuditLog.target_type == "mcp_tool"))]
        assert "CANCELLED" in codes


def test_concurrent_identities_and_global_limit(factory, monkeypatch):
    tokens = [issue(factory, sid)["token"] for sid in (1, 2)]
    limits = MemoryLimits(concurrent=1)
    runtimes = [runtime(factory, token, limits=limits) for token in tokens]
    original = runtimes[0]._identity_data
    def slow(identity):
        time.sleep(0.1)
        return original(identity)
    monkeypatch.setattr(runtimes[0], "_identity_data", slow)
    async def run():
        results = []
        async def one(rt):
            results.append(await rt.execute(ACCESS_TOOL, {}))
        async with anyio.create_task_group() as tg:
            tg.start_soon(one, runtimes[0])
            await anyio.sleep(0.03)
            tg.start_soon(one, runtimes[0])
            tg.start_soon(one, runtimes[1])
        data = [r.structuredContent["data"] for r in results if not r.isError]
        assert {(r["staff_id"], r["company_id"]) for r in data} == {(1, 1), (2, 2)}
        assert any(r.isError and r.structuredContent["code"] == "RATE_LIMITED" for r in results)
    anyio.run(run)


def test_read_session_rejects_flush(factory):
    rt = runtime(factory, issue(factory)["token"])
    with pytest.raises(Exception):
        with rt._read_session() as db:
            db.add(Company(full_name="不得写入"))
            db.flush()
    with factory() as db:
        assert db.scalar(select(Company).where(Company.full_name == "不得写入")) is None


def test_redis_lua_shared_sliding_window_and_lease_expiry():
    import fakeredis
    from app.services.mcp_limits import McpLimits
    server = fakeredis.FakeServer()
    first, second = McpLimits("redis://unused", rpm=2, concurrent=1), McpLimits("redis://unused", rpm=2, concurrent=1)
    first.redis = fakeredis.FakeRedis(server=server)
    second.redis = fakeredis.FakeRedis(server=server)
    lease = first.acquire(1, 1)
    with pytest.raises(QueryLimitExceeded):
        second.acquire(1, 1)
    other = second.acquire(2, 1)
    second.release(other)
    first.release(lease)
    next_lease = second.acquire(1, 1)
    second.release(next_lease)
    with pytest.raises(QueryLimitExceeded):
        first.acquire(1, 1)
    # 时间窗过期：修改本测试Redis数据中的分数，不等待一分钟。
    key = "erp:mcp:{1:1}:minute"
    for member in first.redis.zrange(key, 0, -1):
        first.redis.zadd(key, {member: 0})
    expired_lease = first.acquire(1, 1)
    first.redis.zadd(expired_lease[0], {expired_lease[1]: 0})
    acquired = second.acquire(1, 1)
    second.release(acquired)


def test_dependency_failure_never_echoes_exception(factory, caplog):
    token = issue(factory)["token"]
    class BrokenLimits(MemoryLimits):
        def release(self, lease):
            raise RuntimeError(token)
    result = call(runtime(factory, token, limits=BrokenLimits()))
    assert result.structuredContent["code"] == "DEPENDENCY_UNAVAILABLE"
    assert token not in result.model_dump_json() + caplog.text


def test_migration_0026_round_trip(tmp_path):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert "mcp_credential" in inspector.get_table_names()
    assert {c["name"] for c in inspector.get_columns("mcp_credential")} >= {"token_digest", "staff_id", "tool_scopes"}
    engine.dispose()
    command.downgrade(cfg, "0025")
    engine = create_engine(url)
    assert "mcp_credential" not in inspect(engine).get_table_names()
    engine.dispose()


def test_real_sdk_protocol_and_revocation(factory):
    created = issue(factory)
    rt = runtime(factory, created["token"])
    async def run():
        async with create_connected_server_and_client_session(create_server(rt)) as client:
            initialized = await client.initialize()
            assert initialized.serverInfo.name == "erp-readonly"
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == [ACCESS_TOOL]
            assert tools.tools[0].inputSchema["additionalProperties"] is False
            result = await client.call_tool(ACCESS_TOOL, {})
            assert result.structuredContent["data"]["staff_id"] == 1
            assert (await client.call_tool(ACCESS_TOOL, {"company_id": 2})).isError
            assert (await client.call_tool("execute_sql", {})).isError
            with factory() as db:
                revoke_credential(db, db.get(Staff, 1), created["id"])
            assert (await client.call_tool(ACCESS_TOOL, {})).isError
            with pytest.raises(Exception):
                await client.list_tools()
    anyio.run(run)


def test_actual_stdio_transport(factory):
    from decimal import Decimal
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from sqlalchemy import delete
    from app.models.product import Product
    from app.models.rbac import StaffRole

    # Product rows carry company ownership; the MCP handler enforces that scope.
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.execute(delete(Staff).where(Staff.id == 2))
        db.execute(delete(Role).where(Role.company_id == 2))
        db.execute(delete(Company).where(Company.id == 2))
        db.add(Product(
            id=1, company_id=1, name="STDIO只读商品", code="STDIO-MCP-PRODUCT",
            cost_price=Decimal("12.34"), description="不得经MCP外发",
            image="/private/stdio-product.png",
        ))
        db.commit()
    credential = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_search_products"],
        permission_scopes=["module:product"],
    )
    token = credential["token"]
    url = str(factory.kw["bind"].url)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tests.mcp_stdio_fixture"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={"ERP_MCP_TEST_DB": url, "ERP_MCP_TOKEN": token,
             "DATABASE_URL": url, "PYTHONIOENCODING": "utf-8"},
    )
    async def run():
        with anyio.fail_after(20):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    assert (await client.initialize()).serverInfo.name == "erp-readonly"
                    listed = await client.list_tools()
                    assert {tool.name for tool in listed.tools} == {
                        ACCESS_TOOL, "erp_search_products",
                    }
                    for tool in listed.tools:
                        assert tool.inputSchema["additionalProperties"] is False
                        assert tool.outputSchema
                        assert tool.annotations.readOnlyHint is True
                        assert tool.annotations.destructiveHint is False
                        assert tool.annotations.openWorldHint is False
                    result = await client.call_tool(ACCESS_TOOL, {})
                    assert result.structuredContent["data"]["staff_id"] == 1
                    products = await client.call_tool("erp_search_products", {
                        "keyword": "STDIO-MCP-PRODUCT", "page_size": 20,
                    })
                    assert not products.isError, products.model_dump()
                    envelope = products.structuredContent
                    assert envelope["page"]["total"] == 1
                    product = envelope["data"][0]
                    assert product["code"] == "STDIO-MCP-PRODUCT"
                    assert not {
                        "cost_price", "description", "image",
                    } & set(product)
                    assert token not in products.model_dump_json()
                    invalid = await client.call_tool("erp_search_products", {
                        "keyword": "STDIO-MCP-PRODUCT", "company_id": 1,
                    })
                    assert invalid.isError
                    assert invalid.structuredContent["code"] == "INVALID_ARGUMENT"
                    assert token not in invalid.model_dump_json()
                    with factory() as db:
                        revoke_credential(
                            db, db.get(Staff, 1), credential["id"],
                        )
                    revoked = await client.call_tool(ACCESS_TOOL, {})
                    assert revoked.isError
                    assert revoked.structuredContent["code"] == "UNAUTHENTICATED"
                    assert token not in revoked.model_dump_json()
    anyio.run(run)


def test_registered_search_tools_are_scoped_paged_and_projected(factory):
    from decimal import Decimal
    from sqlalchemy import func
    from app.models.rbac import StaffRole
    from app.models.party import Account, Customer, Supplier
    from app.models.purchase import PurchaseOrder, PurchasePlan
    from app.models.stock import StockSetting
    from app.models.warehouse import Location, Warehouse, Zone
    from app.services.mcp_tool_registry import MCP_TOOL_SPECS

    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.add(Role(id=4, company_id=1, code="FINANCE", name="财务"))
        db.add(Role(id=5, company_id=1, code="AFTER_SALE", name="售后"))
        db.flush()
        db.add_all([StaffRole(staff_id=1, role_id=3), StaffRole(staff_id=1, role_id=4),
                    StaffRole(staff_id=1, role_id=5)])
        db.commit()
    names = [ACCESS_TOOL, *MCP_TOOL_SPECS]
    permissions = ["module:purchase", "module:product", "module:approval", "module:stock", "module:finance", "module:order"]
    token = issue(factory, tool_scopes=names, permission_scopes=permissions)["token"]
    with factory() as db:
        # 保留第二家公司，验证共享查询在多公司数据库中仍按凭据公司隔离。
        db.add(Staff(id=3, company_id=1, name="同部门外员工"))
        db.flush()
        db.add(StaffRole(staff_id=3, role_id=1))
        db.add_all([
            Supplier(id=1, code="MCP-SUP", name="MCP测试供应商"),
            Supplier(id=2, code="MCP-SUP-2", name="第二测试供应商", contact="联系人姓名",
                     phone="13812345678", address="测试省测试市测试路123号",
                     email="private@example.test", remark="不外发备注"),
            Supplier(id=3, code="MCP-INACTIVE", name="停用测试供应商", is_active=False),
            Account(id=1, supplier_id=2, bank="测试银行", account_no="6222000012345678"),
        ])
        db.add_all([
            Customer(id=1, code="MCP-CUS-1", name="测试客户一", phone="13987654321",
                     address="客户详细地址", email="customer@example.test", remark="客户备注"),
            Customer(id=2, code="MCP-CUS-2", name="测试客户二"),
        ])
        db.add_all([
            PurchasePlan(id=1, plan_no="MCP-PLAN-OWN", supplier_id=1, created_by=1),
            PurchasePlan(id=2, plan_no="MCP-PLAN-OTHER", supplier_id=1, created_by=3),
            PurchaseOrder(id=1, order_no="MCP-ORDER-OWN", supplier_id=1, purchaser_id=1, created_by=1),
            PurchaseOrder(id=2, order_no="MCP-ORDER-OTHER", supplier_id=1, purchaser_id=3, created_by=3),
        ])
        db.add(Product(id=1, company_id=1, name="只读测试商品", code="MCP-TEST", barcode="690000000001",
                       cost_price=Decimal("88.88"), online_price=Decimal("99.99"),
                       description="不得外发描述", image="/private/product.png"))
        db.add(Product(id=2, company_id=1, name="第二测试商品", code="MCP-TEST-2", cost_price=Decimal("77.77")))
        db.add_all([
            ProductSku(id=1, company_id=1, product_id=1, spec_name="规格一", spec_code="MCP-SKU-1",
                       barcode="690000000011", sale_price=Decimal("12.50"),
                       cost_price=Decimal("6.25"), image="/private/sku.png"),
            ProductSku(id=2, company_id=1, product_id=1, spec_name="规格二", spec_code="MCP-SKU-2",
                       barcode="690000000012", sale_price=Decimal("13.50"),
                       cost_price=Decimal("7.25")),
        ])
        db.add(ComboProduct(id=1, company_id=1, combo_no="MCP-COMBO-1", name="测试组合商品",
                            main_product_id=1, main_sku_id=1, is_enabled=True,
                            remark="组合备注不得外发"))
        db.add_all([
            ComboProductItem(id=1, company_id=1, combo_id=1, product_id=1, sku_id=1,
                             qty=Decimal("2.000"), sort_order=1),
            ComboProductItem(id=2, company_id=1, combo_id=1, product_id=2, sku_id=None,
                             qty=Decimal("1.500"), sort_order=2),
        ])
        db.add(ComboProduct(id=2, company_id=1, combo_no="MCP-COMBO-DISABLED",
                            name="停用组合商品", main_product_id=2,
                            main_sku_id=None, is_enabled=False))
        db.add_all([
            Warehouse(id=1, code="MCP-WH-1", name="测试仓一", region="测试市",
                      address="测试街道123号", warehouse_type="FINISHED", is_enabled=True,
                      is_default=True, remark="不外发备注", ship_regions="内部路由"),
            Warehouse(id=2, code="MCP-WH-2", name="测试仓二", warehouse_type="DEFECTIVE",
                      is_enabled=False),
        ])
        db.add_all([
            Zone(id=1, warehouse_id=1, code="MCP-Z-1", name="一区", sort_order=1),
            Zone(id=2, warehouse_id=1, code="MCP-Z-2", name="二区", sort_order=2),
        ])
        db.add_all([
            Location(id=1, warehouse_id=1, zone_id=1, code="MCP-LOC-1", is_virtual=False,
                     is_enabled=True, capacity=500, row_no=8),
            Location(id=2, warehouse_id=1, zone_id=1, code="MCP-LOC-2", is_virtual=True,
                     is_enabled=False, capacity=900, row_no=9),
        ])
        db.commit()

    # 本用例逐个探测注册工具，会超过生产默认的单用户RPM；专门放宽夹具，限流语义由独立用例验证。
    # 此用例会逐工具发现并验证参数，阈值仅用于避免模拟限流干扰协议断言。
    rt = runtime(factory, token, limits=MemoryLimits(rpm=1000))
    exposed = anyio.run(lambda: rt.execute(ACCESS_TOOL, {}, discovery=True))
    exposed_names = set(names) - {
        "erp_list_approval_flow_configs", "erp_company_get_company",
        "erp_company_list_departments", "erp_company_get_department",
        "erp_company_list_positions", "erp_company_get_position",
            "erp_company_list_staff", "erp_company_get_staff",
            "erp_company_get_staff_data_scope",
        "erp_company_list_ip_rules", "erp_company_get_ip_rule",
        "erp_assistant_get_admin_config", "erp_assistant_get_admin_usage",
        "erp_dashboard_get_dashboard_overview",
    }
    assert set(exposed) == exposed_names
    with factory() as db:
        before_counts = {
            "plans": db.scalar(select(func.count(PurchasePlan.id))),
            "orders": db.scalar(select(func.count(PurchaseOrder.id))),
            "products": db.scalar(select(func.count(Product.id))),
            "product_skus": db.scalar(select(func.count(ProductSku.id))),
            "combos": db.scalar(select(func.count(ComboProduct.id))),
            "combo_items": db.scalar(select(func.count(ComboProductItem.id))),
            "suppliers": db.scalar(select(func.count(Supplier.id))),
            "accounts": db.scalar(select(func.count(Account.id))),
            "customers": db.scalar(select(func.count(Customer.id))),
            "warehouses": db.scalar(select(func.count(Warehouse.id))),
            "zones": db.scalar(select(func.count(Zone.id))),
            "locations": db.scalar(select(func.count(Location.id))),
        }

    products = call(rt, "erp_search_products", {"keyword": "MCP-TEST", "page_size": 1})
    assert products.structuredContent["page"]["total"] == 2
    assert products.structuredContent["page"]["has_more"] is True
    product = products.structuredContent["data"][0]
    assert product["code"] == "MCP-TEST-2"
    assert "cost_price" not in product and "online_price" not in product
    assert call(rt, "erp_search_products", {
        "keyword": "MCP-TEST", "page": 2, "page_size": 1,
    }).structuredContent["page"]["has_more"] is False
    product_detail = call(rt, "erp_get_product", {"product_id": 1})
    assert product_detail.structuredContent["data"]["code"] == "MCP-TEST"
    assert product_detail.structuredContent["data"]["barcode"] == "690000000001"
    assert not {"online_price", "cost_price", "tax_rate", "description", "image", "skus"} & set(
        product_detail.structuredContent["data"]
    )
    assert call(rt, "erp_get_product", {}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_product", {"product_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_product", {"product_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    skus = call(rt, "erp_list_product_skus", {"product_id": 1, "page_size": 1})
    assert skus.structuredContent["page"]["total"] == 2
    assert skus.structuredContent["page"]["has_more"] is True
    sku = skus.structuredContent["data"][0]
    assert sku["spec_code"] == "MCP-SKU-1"
    assert not {"sale_price", "cost_price", "min_price", "image"} & set(sku)
    assert call(rt, "erp_list_product_skus", {
        "product_id": 1, "page": 2, "page_size": 1,
    }).structuredContent["page"]["has_more"] is False
    assert call(rt, "erp_list_product_skus", {"product_id": 2}).structuredContent["data"] == []
    assert call(rt, "erp_list_product_skus", {"product_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    combos = call(rt, "erp_search_combos", {"keyword": "MCP-COMBO-1", "page_size": 1})
    assert combos.structuredContent["page"]["total"] == 1
    assert combos.structuredContent["data"][0]["combo_no"] == "MCP-COMBO-1"
    assert "remark" not in combos.structuredContent["data"][0]
    assert call(rt, "erp_search_combos", {"keyword": "NO-SUCH-COMBO"}).structuredContent["data"] == []
    combo_detail = call(rt, "erp_get_combo", {"combo_id": 1})
    assert combo_detail.structuredContent["data"]["main_sku_spec_code"] == "MCP-SKU-1"
    assert "remark" not in combo_detail.structuredContent["data"]
    assert "items" not in combo_detail.structuredContent["data"]
    assert call(rt, "erp_get_combo", {}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_combo", {"combo_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    combo_items = call(rt, "erp_list_combo_items", {"combo_id": 1, "page_size": 1})
    assert combo_items.structuredContent["page"]["total"] == 2
    assert combo_items.structuredContent["page"]["has_more"] is True
    assert combo_items.structuredContent["data"][0]["product_name"] == "只读测试商品"
    assert combo_items.structuredContent["data"][0]["sku_spec_code"] == "MCP-SKU-1"
    assert not {"cost_price", "sale_price", "image", "remark"} & set(
        combo_items.structuredContent["data"][0]
    )
    assert call(rt, "erp_list_combo_items", {
        "combo_id": 1, "page": 2, "page_size": 1,
    }).structuredContent["page"]["has_more"] is False
    assert call(rt, "erp_list_combo_items", {"combo_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    expansion = call(rt, "erp_product_expand_combo", {
        "combo_id": 1, "qty": 2, "page_size": 2,
    })
    assert expansion.structuredContent["page"] == {
        "page": 1, "page_size": 2, "total": 3, "has_more": True,
    }
    assert [row["kind"] for row in expansion.structuredContent["data"]] == [
        "MAIN", "ACCESSORY",
    ]
    assert Decimal(expansion.structuredContent["data"][1]["qty"]) == Decimal("4.000")
    assert not {"cost_price", "sale_price", "remark", "image"} & set(
        expansion.structuredContent["data"][1]
    )
    expansion_page_2 = call(rt, "erp_product_expand_combo", {
        "combo_id": 1, "qty": 2, "page": 2, "page_size": 2,
    })
    assert expansion_page_2.structuredContent["page"]["has_more"] is False
    assert Decimal(expansion_page_2.structuredContent["data"][0]["qty"]) == Decimal("3.000")
    assert call(rt, "erp_product_expand_combo", {
        "combo_id": 2,
    }).structuredContent["data"][0]["kind"] == "SINGLE"
    assert call(rt, "erp_product_expand_combo", {
        "combo_id": 1, "qty": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_product_expand_combo", {
        "combo_id": 1, "qty": 1.2345,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_product_expand_combo", {}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_product_expand_combo", {
        "combo_id": 99999,
    }).structuredContent["code"] == "FORBIDDEN"

    suppliers = call(rt, "erp_search_suppliers", {"keyword": "MCP-SUP", "page_size": 1})
    assert suppliers.structuredContent["page"]["total"] == 2
    assert suppliers.structuredContent["page"]["has_more"] is True
    supplier = suppliers.structuredContent["data"][0]
    assert supplier["code"] == "MCP-SUP-2"
    assert supplier["phone"] == "****5678"
    assert supplier["address"] == "已脱敏"
    assert not {"contact", "email", "remark", "accounts", "account_no", "bank"} & set(supplier)
    supplier_detail = call(rt, "erp_get_supplier", {"supplier_id": 2})
    assert supplier_detail.structuredContent["data"]["code"] == "MCP-SUP-2"
    assert supplier_detail.structuredContent["data"]["phone"] == "****5678"
    assert "account_no" not in supplier_detail.structuredContent["data"]
    assert call(rt, "erp_get_supplier", {"supplier_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    missing_supplier = call(rt, "erp_get_supplier", {"supplier_id": 99999})
    assert missing_supplier.structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_search_suppliers", {
        "keyword": "MCP-SUP", "page": 2, "page_size": 1,
    }).structuredContent["page"]["has_more"] is False
    active_suppliers = call(rt, "erp_search_suppliers", {
        "keyword": "MCP-SUP", "is_active": True,
    })
    assert active_suppliers.structuredContent["page"]["total"] == 2
    assert all(item["is_active"] is True for item in active_suppliers.structuredContent["data"])
    inactive_suppliers = call(rt, "erp_search_suppliers", {"is_active": False})
    assert inactive_suppliers.structuredContent["page"]["total"] == 1
    assert inactive_suppliers.structuredContent["data"][0]["code"] == "MCP-INACTIVE"
    assert inactive_suppliers.structuredContent["data"][0]["is_active"] is False
    assert call(rt, "erp_search_suppliers", {"keyword": "NO-SUCH-SUPPLIER"}).structuredContent["data"] == []
    assert call(rt, "erp_search_suppliers", {"page_size": 101}).structuredContent["code"] == "INVALID_ARGUMENT"

    customers = call(rt, "erp_search_customers", {"keyword": "MCP-CUS", "page_size": 1})
    assert customers.structuredContent["page"]["total"] == 2
    assert customers.structuredContent["page"]["has_more"] is True
    customer = customers.structuredContent["data"][0]
    assert customer["code"] == "MCP-CUS-2"
    customer_detail = call(rt, "erp_get_customer", {"customer_id": 1})
    assert customer_detail.structuredContent["data"]["phone"] == "****4321"
    assert customer_detail.structuredContent["data"]["address"] == "已脱敏"
    assert not {"contact", "email", "remark"} & set(customer_detail.structuredContent["data"])
    assert call(rt, "erp_search_customers", {"keyword": "NO-SUCH-CUSTOMER"}).structuredContent["data"] == []
    assert call(rt, "erp_get_customer", {"customer_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_customer", {"customer_id": 99999}).structuredContent["code"] == "FORBIDDEN"

    warehouses = call(rt, "erp_search_warehouses", {"keyword": "MCP-WH", "page_size": 1})
    assert warehouses.structuredContent["page"]["total"] == 2
    assert warehouses.structuredContent["page"]["has_more"] is True
    warehouse = warehouses.structuredContent["data"][0]
    assert warehouse["code"] == "MCP-WH-1"
    assert warehouse["address"] == "已脱敏"
    assert not {"remark", "ship_regions", "sync_stock", "lock_type"} & set(warehouse)
    warehouse_detail = call(rt, "erp_get_warehouse", {"warehouse_id": 1})
    assert warehouse_detail.structuredContent["data"]["code"] == "MCP-WH-1"
    assert warehouse_detail.structuredContent["data"]["address"] == "已脱敏"
    assert "ship_regions" not in warehouse_detail.structuredContent["data"]
    assert call(rt, "erp_get_warehouse", {"warehouse_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_warehouse", {"warehouse_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_search_warehouses", {"warehouse_type": "DEFECTIVE"}).structuredContent["data"][0]["code"] == "MCP-WH-2"
    assert call(rt, "erp_search_warehouses", {"warehouse_type": "INVALID"}).structuredContent["code"] == "INVALID_ARGUMENT"
    zones = call(rt, "erp_list_warehouse_zones", {"warehouse_id": 1, "page_size": 1})
    assert zones.structuredContent["page"]["total"] == 2
    assert zones.structuredContent["page"]["has_more"] is True
    assert zones.structuredContent["data"][0]["code"] == "MCP-Z-1"
    assert call(rt, "erp_list_warehouse_zones", {"warehouse_id": 1, "page": 2, "page_size": 1}).structuredContent["page"]["has_more"] is False
    assert call(rt, "erp_list_warehouse_zones", {"warehouse_id": 2}).structuredContent["data"] == []
    assert call(rt, "erp_list_warehouse_zones", {"warehouse_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    zone_detail = call(rt, "erp_get_warehouse_zone", {"zone_id": 1})
    assert zone_detail.structuredContent["data"] == {
        "id": 1, "warehouse_id": 1, "code": "MCP-Z-1", "name": "一区", "sort_order": 1,
    }
    assert call(rt, "erp_get_warehouse_zone", {}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_warehouse_zone", {"zone_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_warehouse_zone", {"zone_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    locations = call(rt, "erp_search_locations", {"warehouse_id": 1, "page_size": 1})
    assert locations.structuredContent["page"]["total"] == 2
    assert locations.structuredContent["page"]["has_more"] is True
    location = locations.structuredContent["data"][0]
    assert location["code"] == "MCP-LOC-1"
    assert "capacity" not in location and "row_no" not in location
    assert call(rt, "erp_search_locations", {"warehouse_id": 1, "zone_id": 1, "is_enabled": True}).structuredContent["page"]["total"] == 1
    assert call(rt, "erp_search_locations", {"warehouse_id": 1, "zone_id": 99999}).structuredContent["data"] == []
    assert call(rt, "erp_search_locations", {"warehouse_id": 99999}).structuredContent["code"] == "FORBIDDEN"
    location_detail = call(rt, "erp_get_location", {"location_id": 1})
    assert location_detail.structuredContent["data"] == {
        "id": 1, "warehouse_id": 1, "zone_id": 1, "code": "MCP-LOC-1",
        "is_virtual": False, "is_enabled": True,
    }
    assert call(rt, "erp_get_location", {}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_location", {"location_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_location", {"location_id": 99999}).structuredContent["code"] == "FORBIDDEN"

    plans = call(rt, "erp_search_purchase_plans", {"keyword": "MCP-PLAN"})
    assert plans.structuredContent["page"]["total"] == 1
    assert plans.structuredContent["data"][0]["plan_no"] == "MCP-PLAN-OWN"
    orders = call(rt, "erp_search_purchase_orders", {"keyword": "MCP-ORDER"})
    assert orders.structuredContent["page"]["total"] == 1
    assert orders.structuredContent["data"][0]["order_no"] == "MCP-ORDER-OWN"

    empty = call(rt, "erp_search_purchase_plans", {"keyword": "NO-SUCH-PLAN"})
    assert empty.structuredContent["data"] == []
    assert empty.structuredContent["page"]["total"] == 0
    assert empty.structuredContent["page"]["has_more"] is False

    stock_flows = call(rt, "erp_search_stock_flows", {
        "start_time": "2026-10-01T00:00:00+08:00",
        "end_time": "2026-10-31T23:59:59+08:00",
    })
    assert stock_flows.structuredContent["page"]["total"] == 0
    assert call(rt, "erp_search_stock_flows", {
        "start_time": "2026-10-31T00:00:00+08:00",
        "end_time": "2026-10-01T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_list_approval_flows", {}).structuredContent["data"] == []
    assert call(rt, "erp_list_approval_flows", {"doc_type": "expense"}).structuredContent["code"] == "FORBIDDEN"
    notifications = call(rt, "erp_list_my_notifications", {"category": "approval"})
    assert notifications.structuredContent["data"]["items"] == []
    assert notifications.structuredContent["page"]["has_more"] is False
    assert call(rt, "erp_list_my_notifications", {"cursor": "invalid"}).structuredContent["code"] == "INVALID_ARGUMENT"
    unread = call(rt, "erp_notification_count_unread", {})
    assert unread.structuredContent["data"] == {"count": 0}
    settings_result = call(rt, "erp_get_stock_settings", {})
    assert settings_result.structuredContent["data"]["id"] is None
    assert settings_result.structuredContent["data"]["shop_stock_enabled"] is False
    with factory() as db:
        assert db.scalar(select(func.count(StockSetting.id))) == 0
    for finance_tool in (
        "erp_search_purchase_prepays", "erp_search_purchase_invoices",
        "erp_search_expenses", "erp_search_incomes", "erp_search_payments",
        "erp_search_receipts", "erp_search_settlements",
    ):
        finance_result = call(rt, finance_tool, {})
        assert finance_result.structuredContent["data"] == []
        assert finance_result.structuredContent["page"]["total"] == 0
    for tool_name in MCP_TOOL_SPECS:
        invalid = call(rt, tool_name, {"company_id": 1})
        expected_code = (
            "FORBIDDEN" if tool_name == "erp_list_approval_flow_configs"
            or tool_name.startswith("erp_company_")
                or tool_name in {
                    "erp_assistant_get_admin_config",
                    "erp_assistant_get_admin_usage",
                    "erp_dashboard_get_dashboard_overview",
                }
            else "INVALID_ARGUMENT"
        )
        assert invalid.structuredContent["code"] == expected_code
    with factory() as db:
        after_counts = {
            "plans": db.scalar(select(func.count(PurchasePlan.id))),
            "orders": db.scalar(select(func.count(PurchaseOrder.id))),
            "products": db.scalar(select(func.count(Product.id))),
            "product_skus": db.scalar(select(func.count(ProductSku.id))),
            "combos": db.scalar(select(func.count(ComboProduct.id))),
            "combo_items": db.scalar(select(func.count(ComboProductItem.id))),
            "suppliers": db.scalar(select(func.count(Supplier.id))),
            "accounts": db.scalar(select(func.count(Account.id))),
            "customers": db.scalar(select(func.count(Customer.id))),
            "warehouses": db.scalar(select(func.count(Warehouse.id))),
            "zones": db.scalar(select(func.count(Zone.id))),
            "locations": db.scalar(select(func.count(Location.id))),
        }
    assert after_counts == before_counts

    assert call(rt, "erp_search_products", {"page_size": 101}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_search_products", {"company_id": 2}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_search_stock", {}).structuredContent["code"] == "UNKNOWN_TOOL"

    async def protocol_check():
        async with create_connected_server_and_client_session(create_server(rt)) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == exposed_names
            from app.services.mcp_tool_output_schemas import get_tool_data_schema

            business_tools = {
                tool.name: tool for tool in tools.tools if tool.name != ACCESS_TOOL
            }
            assert set(business_tools) == exposed_names - {ACCESS_TOOL}
            for name, tool in business_tools.items():
                assert len(tool.outputSchema["anyOf"]) == 2
                success_schema, error_schema = tool.outputSchema["anyOf"]
                data_schema = success_schema["properties"]["data"]
                assert data_schema == get_tool_data_schema(name), name
                assert _schema_objects_are_closed(data_schema), name
                assert set(error_schema["required"]) == {
                    "code", "message", "request_id",
                }
            product_tool = next(tool for tool in tools.tools if tool.name == "erp_search_products")
            assert product_tool.inputSchema["additionalProperties"] is False
            assert all(tool.inputSchema["additionalProperties"] is False for tool in tools.tools)
            response = await client.call_tool("erp_search_products", {"keyword": "MCP-TEST", "page_size": 1})
            assert response.structuredContent["page"]["has_more"] is True
            customer_response = await client.call_tool(
                "erp_search_customers", {"keyword": "MCP-CUS", "page_size": 1},
            )
            assert customer_response.structuredContent["page"]["has_more"] is True
            warehouse_response = await client.call_tool(
                "erp_search_warehouses", {"keyword": "MCP-WH", "page_size": 1},
            )
            assert warehouse_response.structuredContent["data"][0]["address"] == "已脱敏"
    anyio.run(protocol_check)


def test_registered_search_tool_requires_credential_scope_and_role(factory):
    names = [ACCESS_TOOL, "erp_search_purchase_plans"]
    # WAREHOUSE 有效采购数据权限为空；即便令牌范围列出采购搜索，也不能越过当前角色权限。
    warehouse_token = issue(factory, staff_id=2, tool_scopes=names)["token"]
    result = call(runtime(factory, warehouse_token), "erp_search_purchase_plans", {})
    assert result.isError and result.structuredContent["code"] == "FORBIDDEN"
    from app.models.rbac import StaffRole
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="ADMIN", name="管理员"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    permission_mismatch = issue(
        factory, staff_id=1, tool_scopes=names,
        permission_scopes=["module:order"],
    )["token"]
    assert call(
        runtime(factory, permission_mismatch), "erp_search_purchase_plans", {},
    ).structuredContent["code"] == "FORBIDDEN"
    from app.core.query_access import authorize_query
    from app.services.mcp_credentials import authenticate_credential
    with factory() as db:
        authenticate_credential(db, permission_mismatch)
        with pytest.raises(BizException, match="功能不在凭据范围内"):
            authorize_query(db, "purchase_plans")


def test_supplier_search_is_scoped_to_credential_company(factory):
    from app.models.party import Supplier

    with factory() as db:
        db.add_all([
            Supplier(id=1, company_id=1, code="CO1-SUP", name="本公司供应商"),
            Supplier(id=2, company_id=2, code="CO2-SUP", name="其他公司供应商"),
        ])
        db.commit()
    token = issue(factory, tool_scopes=[ACCESS_TOOL, "erp_search_suppliers"])["token"]
    result = call(runtime(factory, token), "erp_search_suppliers", {})
    assert not result.isError
    assert result.structuredContent["page"]["total"] == 1
    assert [row["name"] for row in result.structuredContent["data"]] == ["本公司供应商"]


def test_supplier_search_matches_rest_filters_and_applies_safe_projection(factory):
    from app.api.party import router as party_router
    from app.core.data_scope import clear_scope_policy
    from app.models.party import Supplier

    with factory() as db:
        db.delete(db.get(StaffRole, 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Supplier(
                id=1, code="MCP-PARITY-1", name="匹配的停用供应商",
                phone="13812345678", address="详细地址不可外发", is_active=False,
                contact="联系人不可外发", email="private@example.test",
                remark="备注不可外发",
            ),
            Supplier(
                id=2, code="MCP-PARITY-2", name="匹配的启用供应商",
                is_active=True,
            ),
        ])
        db.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(party_router)

    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app.dependency_overrides[get_db] = sessions
    client = TestClient(app)
    rest = client.get(
        "/suppliers",
        params={"keyword": "MCP-PARITY", "is_active": "false", "page": 1, "page_size": 20},
        headers={"Authorization": "Bearer " + create_access_token(1, "test", [])},
    )
    assert rest.status_code == 200
    rest_data = rest.json()["data"]

    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_search_suppliers", "erp_get_supplier"],
        permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)
    mcp = call(rt, "erp_search_suppliers", {
        "keyword": "MCP-PARITY", "is_active": False, "page": 1, "page_size": 20,
    }).structuredContent
    assert mcp["page"]["total"] == rest_data["total"] == 1
    assert [
        {key: row[key] for key in ("id", "code", "name", "is_active")}
        for row in mcp["data"]
    ] == [
        {key: row[key] for key in ("id", "code", "name", "is_active")}
        for row in rest_data["items"]
    ]
    supplier = mcp["data"][0]
    assert supplier["phone"] == "****5678"
    assert supplier["address"] == "已脱敏"
    assert not {"contact", "email", "remark", "accounts"} & set(supplier)

    for supplier_id in (1, 2):
        rest_detail_response = client.get(
            f"/suppliers/{supplier_id}",
            headers={"Authorization": "Bearer " + create_access_token(1, "test", [])},
        )
        assert rest_detail_response.status_code == 200
        rest_detail = rest_detail_response.json()["data"]
        mcp_detail = call(rt, "erp_get_supplier", {
            "supplier_id": supplier_id,
        }).structuredContent["data"]
        for field in ("id", "code", "name", "is_active"):
            assert mcp_detail[field] == rest_detail[field], field
        assert mcp_detail["phone"] == (
            "****" + rest_detail["phone"][-4:] if rest_detail["phone"] else None
        )
        assert mcp_detail["address"] == (
            "已脱敏" if rest_detail["address"] else rest_detail["address"]
        )
        assert not {"contact", "fax", "email", "remark", "accounts"} & set(mcp_detail)


def test_product_query_tools_match_rest_rows_for_same_identity_and_filters(factory):
    from decimal import Decimal
    from sqlalchemy import delete
    from app.api.product import router as product_router
    from app.core.data_scope import clear_scope_policy

    with factory() as db:
        db.add_all([
            Product(id=1, company_id=1, code="MCP-PARITY-P1", name="对照商品一",
                    cost_price=Decimal("7.25"), online_price=Decimal("9.50"),
                    description="REST可见但MCP不外发"),
            Product(id=2, company_id=1, code="MCP-PARITY-P2", name="对照商品二",
                    cost_price=Decimal("8.25"), online_price=Decimal("10.50")),
            ProductSku(id=1, company_id=1, product_id=1, spec_name="标准规格", spec_code="MCP-PARITY-SKU",
                       barcode="690000000099", cost_price=Decimal("7.25"),
                       sale_price=Decimal("9.50")),
            ComboProduct(id=1, company_id=1, combo_no="MCP-PARITY-COMBO", name="对照组合",
                         main_product_id=1, main_sku_id=1, is_enabled=True,
                         remark="MCP不外发备注"),
            ComboProductItem(id=1, company_id=1, combo_id=1, product_id=2, sku_id=None,
                             qty=Decimal("1.500"), sort_order=1),
        ])
        db.commit()

    tools = [
        ACCESS_TOOL, "erp_search_products", "erp_get_product", "erp_list_product_skus",
        "erp_search_combos", "erp_get_combo", "erp_list_combo_items",
        "erp_product_expand_combo",
    ]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:product"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(product_router)

    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app.dependency_overrides[get_db] = sessions
    headers = {"Authorization": "Bearer " + create_access_token(1, "采购员", [])}

    def assert_common_fields(mcp_row, rest_row):
        for field, value in mcp_row.items():
            if field in rest_row:
                if field == "qty":
                    assert Decimal(str(value)) == Decimal(str(rest_row[field]))
                else:
                    assert value == rest_row[field], field

    with TestClient(app) as client:
        rest = client.get(
            "/products", params={"keyword": "MCP-PARITY", "page": 1, "page_size": 100},
            headers=headers,
        )
        assert rest.status_code == 200
        rest_data = rest.json()["data"]
        mcp = call(rt, "erp_search_products", {
            "keyword": "MCP-PARITY", "page": 1, "page_size": 100,
        }).structuredContent
        assert mcp["page"]["total"] == rest_data["total"] == 2
        assert [row["id"] for row in mcp["data"]] == [
            row["id"] for row in rest_data["items"]
        ]
        for mcp_row, rest_row in zip(mcp["data"], rest_data["items"]):
            assert_common_fields(mcp_row, rest_row)

        rest_detail = client.get("/products/1", headers=headers)
        assert rest_detail.status_code == 200
        detail = call(rt, "erp_get_product", {"product_id": 1}).structuredContent["data"]
        assert_common_fields(detail, rest_detail.json()["data"])
        assert "description" not in detail and "cost_price" not in detail

        rest_skus = client.get("/products/1/skus", headers=headers)
        assert rest_skus.status_code == 200
        skus = call(rt, "erp_list_product_skus", {
            "product_id": 1, "page_size": 100,
        }).structuredContent
        assert skus["page"]["total"] == len(rest_skus.json()["data"]) == 1
        assert_common_fields(skus["data"][0], rest_skus.json()["data"][0])
        assert "sale_price" not in skus["data"][0] and "cost_price" not in skus["data"][0]

        rest_combos = client.get(
            "/combo-products", params={"keyword": "MCP-PARITY", "page": 1, "page_size": 100},
            headers=headers,
        )
        assert rest_combos.status_code == 200
        combo_list = call(rt, "erp_search_combos", {
            "keyword": "MCP-PARITY", "page": 1, "page_size": 100,
        }).structuredContent
        assert combo_list["page"]["total"] == rest_combos.json()["data"]["total"] == 1
        assert_common_fields(combo_list["data"][0], rest_combos.json()["data"]["items"][0])

        rest_combo = client.get("/combo-products/1", headers=headers)
        assert rest_combo.status_code == 200
        combo_detail = call(rt, "erp_get_combo", {"combo_id": 1}).structuredContent["data"]
        rest_combo_data = rest_combo.json()["data"]
        assert_common_fields(combo_detail, rest_combo_data)
        assert "remark" not in combo_detail and "items" not in combo_detail

        combo_items = call(rt, "erp_list_combo_items", {
            "combo_id": 1, "page_size": 100,
        }).structuredContent
        assert combo_items["page"]["total"] == len(rest_combo_data["items"]) == 1
        assert_common_fields(combo_items["data"][0], rest_combo_data["items"][0])

        rest_expansion = client.get(
            "/combo-products/1/expand", params={"qty": "2"}, headers=headers,
        )
        assert rest_expansion.status_code == 200
        expansion = call(rt, "erp_product_expand_combo", {
            "combo_id": 1, "qty": 2, "page_size": 100,
        }).structuredContent
        assert expansion["page"]["total"] == len(rest_expansion.json()["data"]) == 2
        for mcp_row, rest_row in zip(expansion["data"], rest_expansion.json()["data"]):
            assert_common_fields(mcp_row, rest_row)


def test_warehouse_search_requires_role_and_scopes_by_company(factory):
    with factory() as db:
        warehouse_role = Role(id=3, company_id=1, code="WAREHOUSE", name="仓库")
        db.add(warehouse_role)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=warehouse_role.id))
        db.commit()
    token = issue(factory, tool_scopes=[ACCESS_TOOL, "erp_search_warehouses"],
                  permission_scopes=["module:stock"])["token"]
    result = call(runtime(factory, token), "erp_search_warehouses", {})
    assert not result.isError
    assert result.structuredContent["page"]["total"] == 0


def test_warehouse_metadata_details_require_role_and_scope_by_company(factory):
    from app.models.warehouse import Location, Warehouse, Zone
    with factory() as db:
        quality_role = Role(id=3, company_id=1, code="QUALITY", name="质检")
        db.add(quality_role)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=quality_role.id))
        db.commit()
    tool_names = [
        ACCESS_TOOL, "erp_get_warehouse_zone", "erp_get_location",
        "erp_warehouse_get_location_capacity",
    ]
    calls = (
        ("erp_get_warehouse_zone", {"zone_id": 1}),
        ("erp_get_location", {"location_id": 1}),
        ("erp_warehouse_get_location_capacity", {"location_id": 1}),
    )
    # QUALITY 有库存模块权限，但没有 WAREHOUSE 子功能角色，不能读仓库元数据。
    quality_token = issue(factory, tool_scopes=tool_names,
                          permission_scopes=["module:stock"])["token"]
    for tool_name, arguments in calls:
        result = call(runtime(factory, quality_token), tool_name, arguments)
        assert result.isError and result.structuredContent["code"] == "FORBIDDEN"

    # 正确角色可查本公司数据；同一实例中的其他公司记录不可见。
    with factory() as db:
        warehouse_role = Role(id=4, company_id=1, code="WAREHOUSE", name="仓库")
        db.add(warehouse_role)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=warehouse_role.id))
        db.add_all([
            Warehouse(id=1, company_id=1, code="OWN-WH", name="本公司仓库"),
            Warehouse(id=2, company_id=2, code="OTHER-WH", name="外公司仓库"),
            Zone(id=1, company_id=1, warehouse_id=1, code="OWN-Z", name="本公司库区"),
            Zone(id=2, company_id=2, warehouse_id=2, code="OTHER-Z", name="外公司库区"),
            Location(id=1, company_id=1, warehouse_id=1, zone_id=1, code="OWN-L"),
            Location(id=2, company_id=2, warehouse_id=2, zone_id=2, code="OTHER-L"),
        ])
        db.commit()
    warehouse_token = issue(factory, tool_scopes=tool_names,
                            permission_scopes=["module:stock"])["token"]
    for tool_name, arguments in calls:
        result = call(runtime(factory, warehouse_token), tool_name, arguments)
        assert not result.isError, (tool_name, result.structuredContent)
        foreign_arguments = {
            "erp_get_warehouse_zone": {"zone_id": 2},
            "erp_get_location": {"location_id": 2},
            "erp_warehouse_get_location_capacity": {"location_id": 2},
        }[tool_name]
        foreign = call(runtime(factory, warehouse_token), tool_name, foreign_arguments)
        assert foreign.isError and foreign.structuredContent["code"] == "FORBIDDEN"


def test_warehouse_catalog_search_filters_are_paged_and_read_only(factory):
    from sqlalchemy import delete, func
    from app.models.warehouse import Location, Warehouse, Zone

    tools = [
        ACCESS_TOOL, "erp_search_warehouses", "erp_list_warehouse_zones",
        "erp_search_locations", "erp_get_warehouse", "erp_get_warehouse_zone",
        "erp_get_location",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Warehouse(
                id=1, code="MCP-WH-ENABLED", name="启用仓库",
                warehouse_type="FINISHED", is_enabled=True,
                address="PRIVATE-WAREHOUSE-ADDRESS",
            ),
            Warehouse(
                id=2, code="MCP-WH-DISABLED", name="停用仓库",
                warehouse_type="DEFECTIVE", is_enabled=False,
            ),
        ])
        db.add_all([
            Zone(id=1, warehouse_id=1, code="MCP-ZONE-A", name="库区甲"),
            Zone(id=2, warehouse_id=1, code="MCP-ZONE-B", name="库区乙"),
        ])
        db.add_all([
            Location(
                id=1, warehouse_id=1, zone_id=1, code="MCP-LOC-A",
                is_enabled=True, capacity=99,
            ),
            Location(
                id=2, warehouse_id=1, zone_id=1, code="MCP-LOC-B",
                is_enabled=False, capacity=100,
            ),
        ])
        db.commit()
        before = tuple(db.scalar(select(func.count(model.id))) for model in (
            Warehouse, Zone, Location,
        ))

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=500))
    warehouses = call(rt, tools[1], {
        "keyword": "MCP-WH-ENABLED", "warehouse_type": "FINISHED",
        "is_enabled": True,
    }).structuredContent
    assert warehouses["page"]["total"] == 1
    assert warehouses["data"][0]["name"] == "启用仓库"
    zones = call(rt, tools[2], {
        "warehouse_id": 1, "keyword": "MCP-ZONE-B",
    }).structuredContent
    assert zones["page"]["total"] == 1
    assert zones["data"][0]["name"] == "库区乙"
    locations = call(rt, tools[3], {
        "warehouse_id": 1, "zone_id": 1, "keyword": "MCP-LOC-A",
        "is_enabled": True,
    }).structuredContent
    assert locations["page"]["total"] == 1
    assert locations["data"][0]["code"] == "MCP-LOC-A"
    assert "capacity" not in locations["data"][0]

    from app.api.warehouse import router as warehouse_router
    from app.core.data_scope import clear_scope_policy

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(warehouse_router)

    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app.dependency_overrides[get_db] = sessions
    headers = {"Authorization": "Bearer " + create_access_token(1, "仓库员", [])}
    with TestClient(app) as client:
        rest_warehouses = client.get("/warehouses", params={
            "keyword": "MCP-WH-ENABLED", "warehouse_type": "FINISHED",
            "is_enabled": "true",
        }, headers=headers)
        assert rest_warehouses.status_code == 200
        rest_wh_rows = rest_warehouses.json()["data"]
        assert [row["code"] for row in rest_wh_rows] == [row["code"] for row in warehouses["data"]]
        assert warehouses["page"]["total"] == len(rest_wh_rows) == 1

        rest_zones = client.get("/warehouses/1/zones", headers=headers)
        assert rest_zones.status_code == 200
        all_zones = call(rt, "erp_list_warehouse_zones", {
            "warehouse_id": 1, "page_size": 100,
        }).structuredContent
        assert all_zones["page"]["total"] == len(rest_zones.json()["data"]) == 2
        assert [row["id"] for row in all_zones["data"]] == [
            row["id"] for row in rest_zones.json()["data"]
        ]

        rest_locations = client.get("/locations", params={
            "warehouse_id": 1, "zone_id": 1, "keyword": "MCP-LOC-A",
            "is_enabled": "true",
        }, headers=headers)
        assert rest_locations.status_code == 200
        rest_location_rows = rest_locations.json()["data"]
        assert [row["code"] for row in rest_location_rows] == [row["code"] for row in locations["data"]]
        assert locations["page"]["total"] == len(rest_location_rows) == 1

        for rest_path, tool_name, arguments, keys in (
            ("/warehouses/1", "erp_get_warehouse", {"warehouse_id": 1},
             ("id", "code", "name", "warehouse_type", "is_enabled")),
            ("/zones/1", "erp_get_warehouse_zone", {"zone_id": 1},
             ("id", "warehouse_id", "code", "name")),
            ("/locations/1", "erp_get_location", {"location_id": 1},
             ("id", "warehouse_id", "zone_id", "code", "is_enabled")),
        ):
            rest_detail = client.get(rest_path, headers=headers)
            assert rest_detail.status_code == 200
            detail = call(rt, tool_name, arguments).structuredContent["data"]
            for key in keys:
                assert detail[key] == rest_detail.json()["data"][key], (tool_name, key)
            if tool_name == "erp_get_warehouse":
                assert rest_detail.json()["data"]["address"] == "PRIVATE-WAREHOUSE-ADDRESS"
                assert detail["address"] == "已脱敏"

        assert "capacity" not in locations["data"][0]

    with factory() as db:
        after = tuple(db.scalar(select(func.count(model.id))) for model in (
            Warehouse, Zone, Location,
        ))
    assert after == before


def test_warehouse_reference_queries_and_capacity_match_rest_for_same_identity_and_pages(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.warehouse import router as warehouse_router
    from app.core.data_scope import clear_scope_policy
    from app.models.warehouse import Location, StockLocation, Warehouse, Zone

    tools = [
        ACCESS_TOOL, "erp_search_warehouses", "erp_get_warehouse",
        "erp_list_warehouse_zones", "erp_get_warehouse_zone",
        "erp_search_locations", "erp_get_location",
        "erp_warehouse_get_location_capacity",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Warehouse(
                id=1, code="MCP-WH-REST-A", name="仓库甲",
                warehouse_type="FINISHED", is_enabled=True,
                address="PRIVATE-WAREHOUSE-ADDRESS",
            ),
            Warehouse(
                id=2, code="MCP-WH-REST-B", name="仓库乙",
                warehouse_type="DEFECTIVE", is_enabled=False,
            ),
            Zone(id=1, warehouse_id=1, code="MCP-ZONE-REST-A", name="常温区", sort_order=1),
            Zone(id=2, warehouse_id=1, code="MCP-ZONE-REST-B", name=None, sort_order=2),
            Zone(id=3, warehouse_id=1, code="MCP-ZONE-REST-C", name="冷藏区", sort_order=3),
            Location(
                id=1, warehouse_id=1, zone_id=1, code="MCP-LOC-REST-A",
                is_enabled=True, capacity=99,
            ),
            Location(
                id=2, warehouse_id=1, zone_id=1, code="MCP-LOC-REST-B",
                is_enabled=False, capacity=100,
            ),
            Location(
                id=3, warehouse_id=1, zone_id=None, code="MCP-LOC-REST-C",
                is_enabled=True, capacity=0,
            ),
            Product(id=1, name="容量测试商品", code="MCP-CAP-PRODUCT"),
        ])
        db.flush()
        db.add(ProductSku(id=1, product_id=1, spec_code="MCP-CAP-SKU"))
        db.flush()
        db.add_all([
            StockLocation(
                id=1, location_id=1, product_sku_id=1,
                qty=Decimal("17.250"),
            ),
            StockLocation(
                id=2, location_id=3, product_sku_id=1,
                qty=Decimal("2.500"),
            ),
        ])
        db.commit()
        models = (Warehouse, Zone, Location, Product, ProductSku, StockLocation)
        before = tuple(db.scalar(select(func.count(model.id))) for model in models)

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(warehouse_router)

    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app.dependency_overrides[get_db] = sessions

    def rest_data(client, path, params=None):
        response = client.get(
            path, params=params or {},
            headers={"Authorization": "Bearer " + create_access_token(1, "仓库员", [])},
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def mcp_pages(tool_name, filters=None):
        filters = filters or {}
        result = call(rt, tool_name, {**filters, "page": 1, "page_size": 1})
        assert not result.isError, result.structuredContent
        rows = list(result.structuredContent["data"])
        page = result.structuredContent["page"]
        while page["has_more"]:
            result = call(rt, tool_name, {
                **filters, "page": page["page"] + 1, "page_size": 1,
            })
            assert not result.isError, result.structuredContent
            rows.extend(result.structuredContent["data"])
            page = result.structuredContent["page"]
        return rows

    with TestClient(app) as client:
        rest_warehouses = rest_data(client, "/warehouses")
        mcp_warehouses = mcp_pages("erp_search_warehouses")
        assert {row["id"] for row in mcp_warehouses} == {
            row["id"] for row in rest_warehouses
        }
        for rest_row in rest_warehouses:
            mcp_row = next(row for row in mcp_warehouses if row["id"] == rest_row["id"])
            for field in ("id", "code", "name", "region", "warehouse_type", "is_enabled", "is_default"):
                assert mcp_row[field] == rest_row[field], field
            assert mcp_row["address"] == (
                "已脱敏" if rest_row["address"] else rest_row["address"]
            )
            assert "remark" not in mcp_row and "ship_regions" not in mcp_row

        rest_zones = rest_data(client, "/warehouses/1/zones")
        mcp_zones = mcp_pages("erp_list_warehouse_zones", {"warehouse_id": 1})
        assert {row["id"] for row in mcp_zones} == {row["id"] for row in rest_zones}
        rest_zone = rest_data(client, "/zones/2")
        mcp_zone = call(rt, "erp_get_warehouse_zone", {"zone_id": 2}).structuredContent["data"]
        assert {key: mcp_zone[key] for key in ("id", "warehouse_id", "code", "name")} == {
            key: rest_zone[key] for key in ("id", "warehouse_id", "code", "name")
        }
        assert mcp_zone["name"] is None

        rest_locations = rest_data(client, "/locations", {"warehouse_id": 1})
        mcp_locations = mcp_pages("erp_search_locations", {"warehouse_id": 1})
        assert {row["id"] for row in mcp_locations} == {row["id"] for row in rest_locations}
        for row in mcp_locations:
            assert "capacity" not in row
        rest_location = rest_data(client, "/locations/3")
        mcp_location = call(rt, "erp_get_location", {
            "location_id": 3,
        }).structuredContent["data"]
        for field in ("id", "warehouse_id", "zone_id", "code", "is_enabled"):
            assert mcp_location[field] == rest_location[field], field
        assert mcp_location["zone_id"] is None

        for location_id in (1, 3):
            rest_capacity = rest_data(client, f"/locations/{location_id}/capacity")
            mcp_capacity = call(rt, "erp_warehouse_get_location_capacity", {
                "location_id": location_id,
            }).structuredContent["data"]
            assert mcp_capacity["location_id"] == rest_capacity["location_id"]
            assert mcp_capacity["capacity"] == rest_capacity["capacity"]
            assert Decimal(mcp_capacity["used_qty"]) == Decimal(str(rest_capacity["used_qty"]))
            if rest_capacity["remaining_qty"] is None:
                assert mcp_capacity["remaining_qty"] is None
            else:
                assert Decimal(mcp_capacity["remaining_qty"]) == Decimal(str(rest_capacity["remaining_qty"]))
            assert mcp_capacity["unlimited"] == rest_capacity["unlimited"]

    with factory() as db:
        after = tuple(db.scalar(select(func.count(model.id))) for model in models)
    assert after == before


def test_product_queries_are_company_scoped_and_require_product_permission(factory):
    from decimal import Decimal
    from app.models.product import ComboProduct, ComboProductItem, Product, ProductSku

    names = [
        ACCESS_TOOL, "erp_search_products", "erp_get_product", "erp_list_product_skus",
        "erp_search_combos", "erp_get_combo", "erp_list_combo_items",
        "erp_product_expand_combo",
    ]
    with factory() as db:
        db.add_all([
            Product(id=1, company_id=1, code="TENANT-P1", name="本公司商品"),
            Product(id=2, company_id=2, code="TENANT-P2", name="他公司商品"),
            ProductSku(
                id=1, company_id=1, product_id=1,
                spec_name="本公司规格", spec_code="TENANT-SKU-1",
                barcode="TENANT-BARCODE-1",
            ),
            ProductSku(
                id=2, company_id=2, product_id=2,
                spec_name="他公司规格", spec_code="TENANT-SKU-2",
                barcode="TENANT-BARCODE-2",
            ),
            ComboProduct(
                id=1, company_id=1, combo_no="TENANT-C1", name="本公司组合",
                main_product_id=1, main_sku_id=1,
            ),
            ComboProduct(
                id=2, company_id=2, combo_no="TENANT-C2", name="他公司组合",
                main_product_id=2, main_sku_id=2,
            ),
            ComboProductItem(
                id=1, company_id=1, combo_id=1, product_id=1, sku_id=1,
                qty=Decimal("1.000"),
            ),
            ComboProductItem(
                id=2, company_id=2, combo_id=2, product_id=2, sku_id=2,
                qty=Decimal("2.000"),
            ),
        ])
        db.commit()

    stock_only_token = issue(factory, staff_id=2, tool_scopes=names,
                             permission_scopes=["module:stock"])["token"]
    product_token = issue(factory, staff_id=1, tool_scopes=names,
                          permission_scopes=["module:product"])["token"]
    calls = (
        ("erp_get_product", {"product_id": 1}),
        ("erp_list_product_skus", {"product_id": 1}),
        ("erp_search_combos", {}),
        ("erp_get_combo", {"combo_id": 1}),
        ("erp_list_combo_items", {"combo_id": 1}),
        ("erp_product_expand_combo", {"combo_id": 1}),
    )
    # 库存权限不能转成商品资料权限。
    for tool_name, arguments in calls:
        result = call(runtime(factory, stock_only_token), tool_name, arguments)
        assert result.isError and result.structuredContent["code"] == "FORBIDDEN"
    rt = runtime(factory, product_token)
    assert [row["id"] for row in call(rt, "erp_search_products", {}).structuredContent["data"]] == [1]
    assert call(rt, "erp_get_product", {"product_id": 1}).structuredContent["data"]["name"] == "本公司商品"
    assert call(rt, "erp_list_product_skus", {"product_id": 1}).structuredContent["page"]["total"] == 1
    assert call(rt, "erp_search_combos", {}).structuredContent["page"]["total"] == 1
    assert call(rt, "erp_get_combo", {"combo_id": 1}).structuredContent["data"]["combo_no"] == "TENANT-C1"
    assert call(rt, "erp_list_combo_items", {"combo_id": 1}).structuredContent["page"]["total"] == 1
    assert not call(rt, "erp_product_expand_combo", {"combo_id": 1}).isError

    assert call(rt, "erp_search_products", {"keyword": "他公司商品"}).structuredContent["page"]["total"] == 0
    assert call(rt, "erp_search_combos", {"keyword": "TENANT-C2"}).structuredContent["page"]["total"] == 0
    for tool_name, arguments in (
        ("erp_get_product", {"product_id": 2}),
        ("erp_list_product_skus", {"product_id": 2}),
        ("erp_get_combo", {"combo_id": 2}),
        ("erp_list_combo_items", {"combo_id": 2}),
        ("erp_product_expand_combo", {"combo_id": 2}),
    ):
        result = call(rt, tool_name, arguments)
        assert result.isError and result.structuredContent["code"] == "FORBIDDEN"


def test_company_directory_tools_are_company_scoped_paged_and_fixed(factory):
    from sqlalchemy import func
    from app.api.company import router as company_router
    from app.core.data_scope import clear_scope_policy
    from app.models.org import Department, Position
    from app.models.rbac import DataScope

    tool_names = [
        ACCESS_TOOL, "erp_company_get_company", "erp_company_list_departments",
        "erp_company_get_department", "erp_company_list_positions",
        "erp_company_get_position", "erp_company_list_staff",
        "erp_company_get_staff", "erp_company_get_staff_data_scope",
    ]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="ADMIN", name="管理员一"),
            Role(id=4, company_id=2, code="ADMIN", name="管理员二"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            StaffRole(staff_id=2, role_id=4),
            Department(id=1, company_id=1, name="采购部", sort_order=1),
            Department(id=2, company_id=1, name="销售部", sort_order=2),
            Department(id=3, company_id=2, name="他公司部门", sort_order=1),
            Position(id=1, company_id=1, name="采购员"),
            Position(id=2, company_id=2, name="他公司职务"),
            Staff(id=3, company_id=1, name="测试员工甲", staff_no="EMP-001",
                  login_account="private-login", phone="13800001111",
                  password_hash="private-hash", department_id=1, position_id=1,
                  remark="员工备注不得外发"),
            Staff(id=5, company_id=1, name="测试员工乙", staff_no="EMP-003",
                  department_id=1, position_id=1),
            Staff(id=4, company_id=2, name="他公司员工", staff_no="EMP-002",
                  login_account="other-private-login", phone="13900002222"),
            DataScope(id=1, staff_id=3, scope_type="DEPARTMENT", department_id=1),
        ])
        company = db.get(Company, 1)
        company.short_name = "测试简称"
        company.legal_person = "不应外发"
        company.phone = "13800001111"
        company.tax_no = "TAX-SECRET"
        db.commit()

    token = issue(
        factory, staff_id=1, tool_scopes=tool_names,
        permission_scopes=["module:settings"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    with factory() as db:
        before = {
            "companies": db.scalar(select(func.count(Company.id))),
            "departments": db.scalar(select(func.count(Department.id))),
            "positions": db.scalar(select(func.count(Position.id))),
            "staff": db.scalar(select(func.count(Staff.id))),
            "data_scopes": db.scalar(select(func.count(DataScope.id))),
        }

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(company_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "管理员一", [])}
    with TestClient(rest_app) as rest_client:
        rest_company_response = rest_client.get("/company", headers=rest_headers)
        assert rest_company_response.status_code == 200
        rest_company = rest_company_response.json()["data"]
        mcp_company = call(rt, "erp_company_get_company", {}).structuredContent["data"]
        for field in mcp_company:
            assert mcp_company[field] == rest_company[field]

        rest_departments_response = rest_client.get("/departments", headers=rest_headers)
        assert rest_departments_response.status_code == 200
        rest_departments = {
            row["id"]: row for row in rest_departments_response.json()["data"]
        }
        mcp_departments = {}
        for page_number in (1, 2):
            department_page = call(rt, "erp_company_list_departments", {
                "page": page_number, "page_size": 1,
            }).structuredContent
            assert department_page["page"]["total"] == len(rest_departments) == 2
            mcp_departments.update({row["id"]: row for row in department_page["data"]})
        assert set(mcp_departments) == set(rest_departments)
        for department_id, mcp_department in mcp_departments.items():
            rest_department = rest_departments[department_id]
            for field in ("id", "name", "parent_id", "sort_order"):
                assert mcp_department[field] == rest_department[field]
        rest_department_detail = rest_client.get(
            "/departments/1", headers=rest_headers,
        )
        mcp_department_detail = call(rt, "erp_company_get_department", {
            "department_id": 1,
        }).structuredContent["data"]
        for field in ("id", "name", "parent_id", "sort_order"):
            assert mcp_department_detail[field] == rest_department_detail.json()["data"][field]

        rest_positions_response = rest_client.get("/positions", headers=rest_headers)
        assert rest_positions_response.status_code == 200
        rest_positions = {
            row["id"]: row for row in rest_positions_response.json()["data"]
        }
        mcp_positions = call(rt, "erp_company_list_positions", {
            "page": 1, "page_size": 1,
        }).structuredContent
        assert mcp_positions["page"]["total"] == len(rest_positions) == 1
        assert mcp_positions["data"][0] == rest_positions[1]
        rest_position_detail = rest_client.get("/positions/1", headers=rest_headers)
        mcp_position_detail = call(rt, "erp_company_get_position", {
            "position_id": 1,
        }).structuredContent["data"]
        assert mcp_position_detail == rest_position_detail.json()["data"]

        staff_params = {
            "keyword": "EMP-00", "department_id": 1, "position_id": 1,
            "is_active": True, "page_size": 1,
        }
        rest_staff_pages = {}
        for page_number in (1, 2):
            rest_staff_response = rest_client.get(
                "/staff", params={**staff_params, "page": page_number},
                headers=rest_headers,
            )
            assert rest_staff_response.status_code == 200
            rest_staff_data = rest_staff_response.json()["data"]
            assert rest_staff_data["total"] == 2
            rest_staff_pages.update({row["id"]: row for row in rest_staff_data["items"]})
            mcp_staff_data = call(rt, "erp_company_list_staff", {
                **staff_params, "page": page_number,
            }).structuredContent
            assert mcp_staff_data["page"]["total"] == rest_staff_data["total"]
            assert [row["id"] for row in mcp_staff_data["data"]] == [
                row["id"] for row in rest_staff_data["items"]
            ]
            for mcp_employee in mcp_staff_data["data"]:
                rest_employee = next(
                    row for row in rest_staff_data["items"]
                    if row["id"] == mcp_employee["id"]
                )
                for field in ("id", "name", "staff_no", "department_name", "position_name"):
                    assert mcp_employee[field] == rest_employee[field]
        assert set(rest_staff_pages) == {3, 5}

        rest_staff_detail_response = rest_client.get("/staff/3", headers=rest_headers)
        assert rest_staff_detail_response.status_code == 200
        rest_staff_detail = rest_staff_detail_response.json()["data"]
        mcp_staff_detail = call(rt, "erp_company_get_staff", {
            "staff_id": 3,
        }).structuredContent["data"]
        for field in ("id", "name", "staff_no", "department_name", "position_name"):
            assert mcp_staff_detail[field] == rest_staff_detail[field]

        rest_scope_response = rest_client.get(
            "/staff/3/data-scope", headers=rest_headers,
        )
        assert rest_scope_response.status_code == 200
        rest_scope = rest_scope_response.json()["data"]
        mcp_scope = call(rt, "erp_company_get_staff_data_scope", {
            "staff_id": 3,
        }).structuredContent["data"]
        assert {key: mcp_scope[key] for key in ("staff_id", "scope_type")} == {
            key: rest_scope[key] for key in ("staff_id", "scope_type")
        }

    company = call(rt, "erp_company_get_company", {})
    assert company.structuredContent["data"] == {
        "id": 1, "full_name": "测试一", "short_name": "测试简称",
    }
    assert call(rt, "erp_company_get_company", {"company_id": 1}).structuredContent[
        "code"
    ] == "INVALID_ARGUMENT"

    departments = call(rt, "erp_company_list_departments", {"page_size": 1})
    assert departments.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert departments.structuredContent["data"][0]["name"] == "采购部"
    assert call(rt, "erp_company_list_departments", {
        "page": 2, "page_size": 1,
    }).structuredContent["data"][0]["name"] == "销售部"
    assert call(rt, "erp_company_list_departments", {
        "page": 100, "page_size": 1,
    }).structuredContent["data"] == []
    assert call(rt, "erp_company_get_department", {
        "department_id": 1,
    }).structuredContent["data"]["parent_id"] is None
    assert call(rt, "erp_company_get_department", {
        "department_id": 3,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_company_get_department", {
        "department_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    positions = call(rt, "erp_company_list_positions", {"page_size": 1})
    assert positions.structuredContent["page"]["total"] == 1
    assert positions.structuredContent["data"] == [{"id": 1, "name": "采购员"}]
    assert call(rt, "erp_company_list_positions", {
        "page": 2, "page_size": 1,
    }).structuredContent["data"] == []
    assert call(rt, "erp_company_get_position", {
        "position_id": 1,
    }).structuredContent["data"]["name"] == "采购员"
    assert call(rt, "erp_company_get_position", {
        "position_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"

    employees = call(rt, "erp_company_list_staff", {
        "keyword": "EMP-001", "department_id": 1, "position_id": 1,
        "is_active": True, "page_size": 1,
    })
    assert employees.structuredContent["page"]["total"] == 1
    employee = employees.structuredContent["data"][0]
    assert employee["name"] == "测试员工甲"
    assert employee["staff_no"] == "EMP-001"
    assert employee["department_name"] == "采购部"
    assert not {
        "company_id", "login_account", "phone", "gender", "region",
        "remark", "password_hash", "has_password",
    } & set(employee)
    assert call(rt, "erp_company_list_staff", {
        "keyword": "13800001111",
    }).structuredContent["page"]["total"] == 0
    assert call(rt, "erp_company_list_staff", {
        "page": 100, "page_size": 1,
    }).structuredContent["data"] == []
    assert call(rt, "erp_company_get_staff", {
        "staff_id": 3,
    }).structuredContent["data"]["position_name"] == "采购员"
    assert call(rt, "erp_company_get_staff", {
        "staff_id": 4,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_company_get_staff", {
        "staff_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_company_get_staff_data_scope", {
        "staff_id": 1,
    }).structuredContent["data"] == {"staff_id": 1, "scope_type": "SELF"}
    staff_scope = call(rt, "erp_company_get_staff_data_scope", {
        "staff_id": 3,
    }).structuredContent["data"]
    assert staff_scope == {"staff_id": 3, "scope_type": "DEPARTMENT"}
    assert call(rt, "erp_company_get_staff_data_scope", {
        "staff_id": 4,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_company_get_staff_data_scope", {
        "staff_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_company_get_staff_data_scope", {
        "staff_id": 3, "company_id": 1,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    unauthorized = issue(
        factory, staff_id=2, tool_scopes=tool_names,
        permission_scopes=["module:stock"],
    )["token"]
    denied = call(runtime(factory, unauthorized), "erp_company_get_company", {})
    assert denied.isError and denied.structuredContent["code"] == "FORBIDDEN"
    denied_scope = call(runtime(factory, unauthorized), "erp_company_get_staff_data_scope", {
        "staff_id": 3,
    })
    assert denied_scope.isError and denied_scope.structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after = {
            "companies": db.scalar(select(func.count(Company.id))),
            "departments": db.scalar(select(func.count(Department.id))),
            "positions": db.scalar(select(func.count(Position.id))),
            "staff": db.scalar(select(func.count(Staff.id))),
            "data_scopes": db.scalar(select(func.count(DataScope.id))),
        }
    assert before == after


def test_assistant_capabilities_tool_exposes_only_current_safe_status(factory):
    from types import SimpleNamespace
    from sqlalchemy import func
    from app.api.assistant import router as assistant_router
    from app.core.data_scope import clear_scope_policy
    from app.models.assistant_governance import AssistantConfig, AssistantUsage
    from app.services import query_projection

    tool_name = "erp_assistant_capabilities"
    with factory() as db:
        db.add(AssistantConfig(
            id=1, company_id=1, provider="deepseek",
            model="PRIVATE-MODEL-NAME",
            api_key_ciphertext="PRIVATE-CIPHER-TEXT",
            api_key_overrides_environment=False,
        ))
        db.commit()
    token = issue(
        factory, staff_id=1, tool_scopes=[ACCESS_TOOL, tool_name],
        permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)
    with factory() as db:
        before = {
            "config": db.scalar(select(func.count(AssistantConfig.id))),
            "usage": db.scalar(select(func.count(AssistantUsage.id))),
        }

    result = call(rt, tool_name, {})
    data = result.structuredContent["data"]
    assert set(data) == {"service_status", "provider_mode", "modules", "features"}
    assert "purchase" in data["modules"]
    assert data["service_status"] in {"ready", "unconfigured", "unavailable"}
    assert data["provider_mode"] in {"deepseek", "mock", "unavailable"}
    assert {row["code"] for row in data["features"]} == {
        "guide", "read_query", "draft_entry",
    }
    serialized = result.model_dump_json()
    assert "PRIVATE-MODEL-NAME" not in serialized
    assert "PRIVATE-CIPHER-TEXT" not in serialized
    assert "api_key" not in serialized
    assert "staff_id" not in data and "permissions" not in data
    assert call(rt, tool_name, {"staff_id": 2}).structuredContent["code"] == "INVALID_ARGUMENT"

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(assistant_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "采购员", [])}
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get("/assistant/capabilities", headers=rest_headers)
        assert rest_response.status_code == 200
        rest_data = rest_response.json()["data"]
        for field in ("service_status", "provider_mode", "modules", "features"):
            assert data[field] == rest_data[field]
        rest_serialized = rest_response.text
        assert "PRIVATE-MODEL-NAME" not in rest_serialized
        assert "PRIVATE-CIPHER-TEXT" not in rest_serialized
        assert "api_key" not in rest_serialized

    projected = query_projection.assistant_capabilities({
        "service_status": "PRIVATE-STATUS", "provider_mode": "PRIVATE-PROVIDER",
        "modules": ["purchase", "PRIVATE-MODULE"],
        "features": [
            None,
            {"code": "delete_all", "enabled": True},
            {"code": "guide", "enabled": "yes"},
            {"code": "guide", "enabled": True, "reason": "PRIVATE-REASON"},
            {"code": "read_query", "enabled": False,
             "reason": "module_not_authorized", "secret": "PRIVATE"},
            {"code": "draft_entry", "enabled": True,
             "document_types": ["purchase_plan", "expense"]},
        ],
    }, SimpleNamespace(
        capability="assistant_capabilities", permissions=frozenset(),
    ))
    assert projected == {
        "service_status": "unavailable", "provider_mode": "unavailable",
        "modules": ["purchase"],
        "features": [
            {"code": "guide", "enabled": True},
            {"code": "read_query", "enabled": False,
             "reason": "module_not_authorized"},
            {"code": "draft_entry", "enabled": True,
             "document_types": ["purchase_plan"]},
        ],
    }
    assert "PRIVATE" not in json.dumps(projected)

    with factory() as db:
        after = {
            "config": db.scalar(select(func.count(AssistantConfig.id))),
            "usage": db.scalar(select(func.count(AssistantUsage.id))),
        }
    assert before == after


def test_finance_document_details_recheck_owner_scope_and_hide_text(factory):
    from decimal import Decimal
    from sqlalchemy import func
    from app.models.finance import Expense, Income, Settlement

    tool_names = [
        ACCESS_TOOL, "erp_finance_doc_get_expense",
        "erp_finance_doc_get_income", "erp_finance_doc_get_settlement",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add_all([
            Expense(id=1, expense_no="MCP-EXP-OWN", amount=Decimal("80.50"),
                    description="不得外发费用描述", voucher_no="PRIVATE-VOUCHER",
                    created_by=1),
            Expense(id=2, expense_no="MCP-EXP-OTHER", amount=Decimal("90.00"),
                    created_by=2),
            Income(id=1, income_no="MCP-INCOME-OWN", amount=Decimal("120.00"),
                   description="不得外发收入描述", voucher_no="PRIVATE-VOUCHER",
                   created_by=1),
            Income(id=2, income_no="MCP-INCOME-OTHER", amount=Decimal("130.00"),
                   created_by=2),
            Settlement(id=1, settlement_no="MCP-SETTLE-OWN",
                       platform_no="PLATFORM-001", amount=Decimal("250.00"),
                       source_file="private.xlsx", remark="不得外发备注",
                       created_by=1),
            Settlement(id=2, settlement_no="MCP-SETTLE-OTHER",
                       amount=Decimal("260.00"), created_by=2),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, token)
    with factory() as db:
        before = {
            "expenses": db.scalar(select(func.count(Expense.id))),
            "incomes": db.scalar(select(func.count(Income.id))),
            "settlements": db.scalar(select(func.count(Settlement.id))),
        }

    expense = call(rt, "erp_finance_doc_get_expense", {"expense_id": 1})
    assert expense.structuredContent["data"]["expense_no"] == "MCP-EXP-OWN"
    assert Decimal(expense.structuredContent["data"]["amount"]) == Decimal("80.50")
    assert not {"description", "voucher_no", "created_by", "created_by_name"} & set(
        expense.structuredContent["data"]
    )
    income = call(rt, "erp_finance_doc_get_income", {"income_id": 1})
    assert income.structuredContent["data"]["income_no"] == "MCP-INCOME-OWN"
    assert not {"description", "voucher_no", "created_by"} & set(
        income.structuredContent["data"]
    )
    settlement = call(rt, "erp_finance_doc_get_settlement", {"settlement_id": 1})
    assert settlement.structuredContent["data"]["settlement_no"] == "MCP-SETTLE-OWN"
    assert not {"source_file", "remark", "created_by", "voucher"} & set(
        settlement.structuredContent["data"]
    )

    for name, key in (
        ("erp_finance_doc_get_expense", "expense_id"),
        ("erp_finance_doc_get_income", "income_id"),
        ("erp_finance_doc_get_settlement", "settlement_id"),
    ):
        assert call(rt, name, {key: 2}).structuredContent["code"] == "FORBIDDEN"
        assert call(rt, name, {key: 99999}).structuredContent["code"] == "FORBIDDEN"
        assert call(rt, name, {key: 0}).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        after = {
            "expenses": db.scalar(select(func.count(Expense.id))),
            "incomes": db.scalar(select(func.count(Income.id))),
            "settlements": db.scalar(select(func.count(Settlement.id))),
        }
    assert before == after


def test_finance_search_lists_page_owner_scope_and_project_fixed_fields(factory):
    from datetime import date
    from decimal import Decimal
    from sqlalchemy import func
    from app.models.finance import Expense, Income, Payment, Receipt, Settlement
    from app.models.org import Department
    from app.models.party import Customer, Supplier
    from app.models.purchase import PurchaseInvoice, PurchasePrepay
    from app.models.rbac import DataScope, StaffRole

    tools = [
        ACCESS_TOOL, "erp_search_expenses", "erp_search_incomes",
        "erp_search_payments", "erp_search_receipts", "erp_search_settlements",
        "erp_search_purchase_prepays", "erp_search_purchase_invoices",
        "erp_finance_doc_get_expense",
        "erp_finance_doc_get_income", "erp_finance_doc_get_settlement",
        "erp_finance_doc_get_payment", "erp_finance_doc_get_receipt",
        "erp_get_purchase_prepay", "erp_get_purchase_invoice",
    ]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="FINANCE", name="财务"),
            Department(id=1, company_id=1, name="财务范围一部"),
            Department(id=2, company_id=1, name="财务范围二部"),
            Staff(id=3, company_id=1, department_id=2, name="同公司跨部门制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门制单人"),
            Supplier(id=1, code="MCP-FIN-SUP", name="查询测试供应商"),
            Customer(id=1, code="MCP-FIN-CUS", name="查询测试客户"),
        ])
        db.flush()
        db.get(Staff, 1).department_id = 1
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add(DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1))
        db.add_all([
            Expense(id=1, expense_no="MCP-EXP-OWN-1", customer_id=1,
                    expense_type="TRAVEL", amount=Decimal("10.00"),
                    description="不得外发费用描述", voucher_no="PRIVATE-EXP-VOUCHER",
                    created_by=1),
            Expense(id=2, expense_no="MCP-EXP-OWN-2", customer_id=1,
                    expense_type="TRAVEL", amount=Decimal("20.00"),
                    description="另一条私密描述", voucher_no="PRIVATE-EXP-VOUCHER-2",
                    created_by=1),
            Expense(id=3, expense_no="MCP-EXP-OTHER", amount=Decimal("99.00"),
                    created_by=3),
            Expense(id=4, expense_no="MCP-EXP-DEPARTMENT", amount=Decimal("40.00"),
                    created_by=4),
            Income(id=1, income_no="MCP-INCOME-OWN-1", supplier_id=1,
                   income_type="OTHER", amount=Decimal("30.00"),
                   description="不得外发收入描述", voucher_no="PRIVATE-INCOME-VOUCHER",
                   created_by=1),
            Income(id=2, income_no="MCP-INCOME-OWN-2", supplier_id=1,
                   income_type="OTHER", amount=Decimal("40.00"), created_by=1),
            Income(id=3, income_no="MCP-INCOME-OTHER", amount=Decimal("99.00"),
                   created_by=3),
            Income(id=4, income_no="MCP-INCOME-DEPARTMENT", amount=Decimal("44.00"),
                   created_by=4),
            Payment(id=1, payment_no="MCP-PAY-OWN-1", supplier_id=1,
                    payment_type="PURCHASE", amount=Decimal("50.00"),
                    created_by=1),
            Payment(id=2, payment_no="MCP-PAY-OWN-2", supplier_id=1,
                    payment_type="PURCHASE", amount=Decimal("60.00"),
                    created_by=1),
            Payment(id=3, payment_no="MCP-PAY-OTHER", supplier_id=1,
                    amount=Decimal("99.00"), created_by=3),
            Payment(id=4, payment_no="MCP-PAY-DEPARTMENT", supplier_id=1,
                    amount=Decimal("64.00"), created_by=4),
            Receipt(id=1, receipt_no="MCP-REC-OWN-1", customer_id=1,
                    amount=Decimal("70.00"), created_by=1),
            Receipt(id=2, receipt_no="MCP-REC-OWN-2", customer_id=1,
                    amount=Decimal("80.00"), created_by=1),
            Receipt(id=3, receipt_no="MCP-REC-OTHER", customer_id=1,
                    amount=Decimal("99.00"), created_by=3),
            Receipt(id=4, receipt_no="MCP-REC-DEPARTMENT", customer_id=1,
                    amount=Decimal("84.00"), created_by=4),
            Settlement(id=1, settlement_no="MCP-SET-OWN-1",
                       platform_no="PLAT-OWN-1", settlement_date=date(2026, 1, 1),
                       amount=Decimal("90.00"), source_file="PRIVATE.xlsx",
                       remark="不得外发结算备注", created_by=1),
            Settlement(id=2, settlement_no="MCP-SET-OWN-2",
                       platform_no="PLAT-OWN-2", settlement_date=date(2026, 1, 2),
                       amount=Decimal("100.00"), created_by=1),
            Settlement(id=3, settlement_no="MCP-SET-OTHER",
                       amount=Decimal("999.00"), created_by=3),
            Settlement(id=4, settlement_no="MCP-SET-DEPARTMENT",
                       amount=Decimal("104.00"), created_by=4),
            PurchasePrepay(id=1, prepay_no="MCP-PRE-OWN-1", supplier_id=1,
                           prepay_amount=Decimal("110.00"), bank="PRIVATE-BANK",
                           account_no="PRIVATE-ACCOUNT", pay_note="不得外发付款备注",
                           created_by=1),
            PurchasePrepay(id=2, prepay_no="MCP-PRE-OWN-2", supplier_id=1,
                           prepay_amount=Decimal("120.00"), created_by=1),
            PurchasePrepay(id=3, prepay_no="MCP-PRE-OTHER", supplier_id=1,
                           prepay_amount=Decimal("999.00"), created_by=3),
            PurchasePrepay(id=4, prepay_no="MCP-PRE-DEPARTMENT", supplier_id=1,
                           prepay_amount=Decimal("124.00"), created_by=4),
            PurchaseInvoice(id=1, invoice_no="MCP-INV-OWN-1", supplier_id=1,
                            invoice_type="NORMAL", amount=Decimal("130.00"),
                            tax_amount=Decimal("0.00"), description="不得外发发票描述",
                            voucher_no="PRIVATE-INVOICE-VOUCHER", created_by=1),
            PurchaseInvoice(id=2, invoice_no="MCP-INV-OWN-2", supplier_id=1,
                            invoice_type="NORMAL", amount=Decimal("140.00"),
                            tax_amount=Decimal("0.00"), created_by=1),
            PurchaseInvoice(id=3, invoice_no="MCP-INV-OTHER", supplier_id=1,
                            invoice_type="NORMAL", amount=Decimal("999.00"),
                            created_by=3),
            PurchaseInvoice(id=4, invoice_no="MCP-INV-DEPARTMENT", supplier_id=1,
                            invoice_type="NORMAL", amount=Decimal("144.00"),
                            created_by=4),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:finance", "module:purchase"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=200))
    list_tools = [
        "erp_search_expenses", "erp_search_incomes", "erp_search_payments",
        "erp_search_receipts", "erp_search_settlements",
        "erp_search_purchase_prepays", "erp_search_purchase_invoices",
    ]
    with factory() as db:
        tracked_models = (Expense, Income, Payment, Receipt, Settlement,
                          PurchasePrepay, PurchaseInvoice)
        before = tuple(db.scalar(select(func.count(model.id))) for model in tracked_models)

    for tool_name in list_tools:
        first = call(rt, tool_name, {"page_size": 1})
        assert first.structuredContent["page"] == {
            "page": 1, "page_size": 1, "total": 2, "has_more": True,
        }, tool_name
        assert first.structuredContent["data"][0]["id"] == 2, tool_name
        second = call(rt, tool_name, {"page": 2, "page_size": 1})
        assert second.structuredContent["data"][0]["id"] == 1, tool_name
        empty = call(rt, tool_name, {"keyword": "NO-SUCH-MCP-FINANCE-DOCUMENT"})
        assert empty.structuredContent["data"] == [] and empty.structuredContent["page"]["total"] == 0, tool_name
        assert call(rt, tool_name, {"page_size": 101}).structuredContent["code"] == "INVALID_ARGUMENT", tool_name

    from app.api.finance_doc import router as finance_doc_router
    from app.api.pay_receipt import router as pay_receipt_router
    from app.api.prepay import router as prepay_router
    from app.api.purchase_invoice import router as purchase_invoice_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    for finance_router in (
        finance_doc_router, pay_receipt_router, prepay_router, purchase_invoice_router,
    ):
        rest_app.include_router(finance_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "财务", [])}
    rest_specs = (
        ("erp_search_expenses", "/expenses", "expense_no", "amount", "MCP-EXP-OWN-1"),
        ("erp_search_incomes", "/incomes", "income_no", "amount", "MCP-INCOME-OWN-1"),
        ("erp_search_payments", "/payments", "payment_no", "amount", "MCP-PAY-OWN-1"),
        ("erp_search_receipts", "/receipts", "receipt_no", "amount", "MCP-REC-OWN-1"),
        ("erp_search_settlements", "/settlements", "settlement_no", "amount", "MCP-SET-OWN-1"),
        ("erp_search_purchase_prepays", "/purchase-prepays", "prepay_no", "prepay_amount", "MCP-PRE-OWN-1"),
        ("erp_search_purchase_invoices", "/purchase-invoices", "invoice_no", "amount", "MCP-INV-OWN-1"),
    )
    detail_specs = (
        ("erp_finance_doc_get_expense", "/expenses/{}", "expense_id"),
        ("erp_finance_doc_get_income", "/incomes/{}", "income_id"),
        ("erp_finance_doc_get_settlement", "/settlements/{}", "settlement_id"),
        ("erp_finance_doc_get_payment", "/payments/{}", "payment_id"),
        ("erp_finance_doc_get_receipt", "/receipts/{}", "receipt_id"),
        ("erp_get_purchase_prepay", "/purchase-prepays/{}", "prepay_id"),
        ("erp_get_purchase_invoice", "/purchase-invoices/{}", "invoice_id"),
    )
    money_fields = {
        "amount", "prepay_amount", "order_amount", "write_off_amount",
        "unwritten_amount", "remaining_amount", "tax_amount", "net_amount",
    }

    def assert_projected_rows_match(mcp_rows, rest_rows, context):
        assert [row["id"] for row in mcp_rows] == [
            row["id"] for row in rest_rows
        ], context
        for mcp_row, rest_row in zip(mcp_rows, rest_rows):
            shared_fields = set(mcp_row) & set(rest_row)
            assert "id" in shared_fields, context
            for field in shared_fields:
                if mcp_row[field] is None or rest_row[field] is None:
                    assert mcp_row[field] is rest_row[field], (context, field)
                    continue
                if field in money_fields:
                    assert Decimal(str(mcp_row[field])) == Decimal(
                        str(rest_row[field])
                    ), (context, field)
                else:
                    assert mcp_row[field] == rest_row[field], (context, field)

    with TestClient(rest_app) as rest_client:
        for tool_name, path, number_field, amount_field, keyword in rest_specs:
            for page_number in (1, 2):
                mcp_page = call(rt, tool_name, {
                    "page": page_number, "page_size": 1,
                }).structuredContent
                rest_response = rest_client.get(path, params={
                    "page": page_number, "page_size": 1,
                }, headers=rest_headers)
                assert rest_response.status_code == 200, (tool_name, rest_response.text)
                rest_page = rest_response.json()["data"]
                assert mcp_page["page"]["total"] == rest_page["total"] == 2, tool_name
                assert_projected_rows_match(
                    mcp_page["data"], rest_page["items"],
                    (tool_name, "page", page_number),
                )
                assert mcp_page["data"][0][number_field] == rest_page["items"][0][number_field], tool_name

            mcp_filtered = call(rt, tool_name, {"keyword": keyword}).structuredContent
            rest_filtered = rest_client.get(path, params={"keyword": keyword}, headers=rest_headers)
            assert rest_filtered.status_code == 200, (tool_name, rest_filtered.text)
            rest_filtered_data = rest_filtered.json()["data"]
            assert mcp_filtered["page"]["total"] == rest_filtered_data["total"] == 1, tool_name
            assert_projected_rows_match(
                mcp_filtered["data"], rest_filtered_data["items"],
                (tool_name, "keyword", keyword),
            )
            assert mcp_filtered["data"][0][number_field] == rest_filtered_data["items"][0][number_field], tool_name
            assert Decimal(mcp_filtered["data"][0][amount_field]) == Decimal(
                str(rest_filtered_data["items"][0][amount_field])
            ), tool_name

        settlement_date_args = {
            "settlement_date_from": "2026-01-02",
            "settlement_date_to": "2026-01-02",
            "page_size": 100,
        }
        mcp_date_page = call(
            rt, "erp_search_settlements", settlement_date_args,
        ).structuredContent
        rest_date_response = rest_client.get(
            "/settlements", params=settlement_date_args, headers=rest_headers,
        )
        assert rest_date_response.status_code == 200
        rest_date_page = rest_date_response.json()["data"]
        assert_projected_rows_match(
            mcp_date_page["data"], rest_date_page["items"],
            ("erp_search_settlements", "settlement_date"),
        )
        assert [row["id"] for row in mcp_date_page["data"]] == [2]

        reversed_date_args = {
            "settlement_date_from": "2026-01-03",
            "settlement_date_to": "2026-01-01",
        }
        mcp_reversed_dates = call(
            rt, "erp_search_settlements", reversed_date_args,
        ).structuredContent
        rest_reversed_dates = rest_client.get(
            "/settlements", params=reversed_date_args, headers=rest_headers,
        )
        assert rest_reversed_dates.status_code == 200
        assert mcp_reversed_dates["data"] == []
        assert rest_reversed_dates.json()["data"]["items"] == []

        expected_ids_by_scope = {
            "SELF": {1, 2},
            "DEPARTMENT": {1, 2, 4},
            "COMPANY": {1, 2, 3, 4},
        }
        for scope, expected_ids in expected_ids_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            for tool_name, path, *_ in rest_specs:
                mcp_page = call(rt, tool_name, {"page_size": 20}).structuredContent
                rest_response = rest_client.get(
                    path, params={"page": 1, "page_size": 20}, headers=rest_headers,
                )
                assert rest_response.status_code == 200, (scope, tool_name, rest_response.text)
                rest_page = rest_response.json()["data"]
                mcp_ids = {row["id"] for row in mcp_page["data"]}
                rest_ids = {row["id"] for row in rest_page["items"]}
                assert mcp_ids == rest_ids == expected_ids, (scope, tool_name)
                assert mcp_page["page"]["total"] == rest_page["total"] == len(expected_ids), (scope, tool_name)
                assert_projected_rows_match(
                    mcp_page["data"], rest_page["items"],
                    (scope, tool_name, "all-summary-fields"),
                )

            for expense_id, should_be_visible in (
                (1, True), (4, scope != "SELF"), (3, scope == "COMPANY"),
            ):
                mcp_detail = call(rt, "erp_finance_doc_get_expense", {
                    "expense_id": expense_id,
                })
                rest_detail = rest_client.get(
                    f"/expenses/{expense_id}", headers=rest_headers,
                )
                if should_be_visible:
                    assert not mcp_detail.isError, (scope, expense_id)
                    assert rest_detail.status_code == 200, (scope, expense_id)
                    assert_projected_rows_match(
                        [mcp_detail.structuredContent["data"]],
                        [rest_detail.json()["data"]],
                        (scope, "erp_finance_doc_get_expense", expense_id),
                    )
                else:
                    assert mcp_detail.structuredContent["code"] == "FORBIDDEN", (scope, expense_id)
                    assert rest_detail.status_code == 404, (scope, expense_id)

            for tool_name, detail_path, argument_name in detail_specs[1:]:
                for document_id, should_be_visible in (
                    (1, True), (2, True),
                    (4, scope != "SELF"), (3, scope == "COMPANY"),
                ):
                    mcp_detail = call(rt, tool_name, {
                        argument_name: document_id,
                    })
                    rest_detail = rest_client.get(
                        detail_path.format(document_id), headers=rest_headers,
                    )
                    if should_be_visible:
                        assert not mcp_detail.isError, (
                            scope, tool_name, document_id,
                            mcp_detail.structuredContent,
                        )
                        assert rest_detail.status_code == 200, (
                            scope, tool_name, document_id, rest_detail.text,
                        )
                        assert_projected_rows_match(
                            [mcp_detail.structuredContent["data"]],
                            [rest_detail.json()["data"]],
                            (scope, tool_name, document_id, "detail"),
                        )
                    else:
                        assert mcp_detail.structuredContent["code"] == "FORBIDDEN", (
                            scope, tool_name, document_id,
                        )
                        assert rest_detail.status_code == 404, (
                            scope, tool_name, document_id, rest_detail.text,
                        )

    expense = call(rt, "erp_search_expenses", {"keyword": "MCP-EXP-OWN-2"}).structuredContent["data"][0]
    assert expense["expense_no"] == "MCP-EXP-OWN-2"
    assert Decimal(expense["amount"]) == Decimal("20.00")
    assert not {"description", "voucher_no", "created_by", "created_by_name"} & set(expense)
    income = call(rt, "erp_search_incomes", {"keyword": "MCP-INCOME-OWN-1"}).structuredContent["data"][0]
    assert Decimal(income["amount"]) == Decimal("30.00")
    assert not {"description", "voucher_no", "created_by", "created_by_name"} & set(income)
    payment = call(rt, "erp_search_payments", {"keyword": "MCP-PAY-OWN-1"}).structuredContent["data"][0]
    assert Decimal(payment["amount"]) == Decimal("50.00")
    assert not {"created_by", "created_by_name", "auditor_id", "auditor_name"} & set(payment)
    receipt = call(rt, "erp_search_receipts", {"keyword": "MCP-REC-OWN-1"}).structuredContent["data"][0]
    assert Decimal(receipt["amount"]) == Decimal("70.00")
    assert not {"created_by", "created_by_name", "auditor_id", "warnings"} & set(receipt)
    settlement = call(rt, "erp_search_settlements", {"keyword": "MCP-SET-OWN-1"}).structuredContent["data"][0]
    assert Decimal(settlement["amount"]) == Decimal("90.00")
    assert not {"source_file", "remark", "created_by", "created_by_name"} & set(settlement)
    assert call(rt, "erp_search_settlements", {
        "settlement_date_from": "2026-02-30",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    prepay = call(rt, "erp_search_purchase_prepays", {
        "keyword": "MCP-PRE-OWN-1",
    }).structuredContent["data"][0]
    assert Decimal(prepay["prepay_amount"]) == Decimal("110.00")
    assert not {"bank", "account_no", "account_balance", "pay_note", "created_by"} & set(prepay)
    invoice = call(rt, "erp_search_purchase_invoices", {
        "keyword": "MCP-INV-OWN-1",
    }).structuredContent["data"][0]
    assert Decimal(invoice["amount"]) == Decimal("130.00")
    assert not {"description", "voucher_no", "created_by", "cost_allocation"} & set(invoice)

    denied_token = issue(
        factory, staff_id=2, tool_scopes=tools,
        permission_scopes=["module:stock"],
    )["token"]
    denied_rt = runtime(factory, denied_token, limits=MemoryLimits(rpm=100))
    for tool_name in list_tools:
        denied = call(denied_rt, tool_name, {})
        assert denied.isError and denied.structuredContent["code"] == "FORBIDDEN", tool_name

    with factory() as db:
        after = tuple(db.scalar(select(func.count(model.id))) for model in tracked_models)
    assert before == after


def test_invoice_management_tools_are_company_scoped_and_hide_tax_identity(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.invoice import router as invoice_router
    from app.core.data_scope import clear_scope_policy
    from app.models.finance import Invoice

    tool_names = [
        ACCESS_TOOL, "erp_invoice_list_invoices", "erp_invoice_get_invoice",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add(Invoice(
            id=99, company_id=2, invoice_no="FOREIGN-INVOICE",
            document_type="INVOICE", status="ISSUED",
        ))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:finance"],
    )["token"]
    multi_company = call(runtime(factory, token), "erp_invoice_list_invoices", {})
    assert not multi_company.isError
    assert multi_company.structuredContent["page"]["total"] == 0

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Invoice(id=1, invoice_no="MCP-INV-1", document_type="INVOICE",
                    invoice_title="测试开票抬头", tax_no="TAX-PRIVATE-001",
                    invoice_type="SPECIAL", amount=Decimal("100.00"),
                    received_amount=Decimal("40.00"), is_received=False,
                    company="测试单位", status="ISSUED", applicant="PRIVATE-APPLICANT"),
            Invoice(id=2, invoice_no="MCP-INV-2", document_type="INVOICE",
                    invoice_title="第二抬头", tax_no="TAX-PRIVATE-002",
                    invoice_type="NORMAL", amount=Decimal("200.00"),
                    received_amount=Decimal("200.00"), is_received=True,
                    company="第二单位", status="RECEIVED", applicant="PRIVATE-APPLICANT"),
            Invoice(id=3, invoice_no="MCP-RECEIVABLE-1", document_type="RECEIVABLE",
                    amount=Decimal("300.00"), status="PENDING"),
        ])
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, token)
    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(invoice_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {
        "Authorization": "Bearer " + create_access_token(1, "财务", []),
    }
    rest_client = TestClient(rest_app)
    with factory() as db:
        before = db.scalar(select(func.count(Invoice.id)))

    shared_filters = {
        "invoice_type": "SPECIAL", "status": "ISSUED",
        "is_received": "false", "keyword": "MCP-INV",
        "page": 1, "page_size": 1,
    }
    rest_filtered = rest_client.get(
        "/invoices", params=shared_filters, headers=rest_headers,
    )
    assert rest_filtered.status_code == 200
    rest_filtered_data = rest_filtered.json()["data"]
    page_1 = call(rt, "erp_invoice_list_invoices", {
        **shared_filters, "is_received": False,
    })
    assert page_1.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 1, "has_more": False,
    }
    invoice = page_1.structuredContent["data"][0]
    rest_invoice = rest_filtered_data["items"][0]
    assert page_1.structuredContent["page"]["total"] == rest_filtered_data["total"]
    for field in (
        "id", "invoice_no", "document_type", "invoice_title", "invoice_type",
        "invoice_type_name", "is_received", "company", "status", "saved_at",
        "created_at", "updated_at",
    ):
        assert invoice[field] == rest_invoice[field], field
    for field in ("amount", "received_amount", "unreceived_amount"):
        assert Decimal(invoice[field]) == Decimal(str(rest_invoice[field])), field
    assert invoice["invoice_no"] == "MCP-INV-1"
    assert Decimal(invoice["unreceived_amount"]) == Decimal("60.00")
    assert not {"tax_no", "applicant"} & set(invoice)

    for private_search in ("TAX-PRIVATE-001", "PRIVATE-APPLICANT"):
        rest_private_search = rest_client.get(
            "/invoices", params={"keyword": private_search}, headers=rest_headers,
        )
        assert rest_private_search.status_code == 200
        rest_private_data = rest_private_search.json()["data"]
        assert rest_private_data["total"] == (1 if private_search.startswith("TAX-") else 2)
        for rest_row in rest_private_data["items"]:
            assert private_search in {rest_row["tax_no"], rest_row["applicant"]}
        mcp_private_search = call(rt, "erp_invoice_list_invoices", {
            "keyword": private_search,
        }).structuredContent
        assert mcp_private_search["page"]["total"] == 0
        assert mcp_private_search["data"] == []

    rest_empty = rest_client.get(
        "/invoices", params={"keyword": "NO-MATCH-MCP-INVOICE"},
        headers=rest_headers,
    )
    assert rest_empty.status_code == 200
    assert rest_empty.json()["data"]["items"] == []
    assert call(rt, "erp_invoice_list_invoices", {
        "keyword": "NO-MATCH-MCP-INVOICE",
    }).structuredContent["data"] == []

    page = call(rt, "erp_invoice_list_invoices", {"page_size": 1})
    assert page.structuredContent["page"]["total"] == 2
    assert page.structuredContent["page"]["has_more"] is True
    received_only = call(rt, "erp_invoice_list_invoices", {
        "is_received": True,
    }).structuredContent
    assert received_only["page"]["total"] == 1
    assert received_only["data"][0]["invoice_no"] == "MCP-INV-2"
    unreceived_only = call(rt, "erp_invoice_list_invoices", {
        "is_received": False,
    }).structuredContent
    assert unreceived_only["page"]["total"] == 1
    assert unreceived_only["data"][0]["invoice_no"] == "MCP-INV-1"
    assert call(rt, "erp_invoice_list_invoices", {
        "page": 2, "page_size": 1,
    }).structuredContent["data"][0]["invoice_no"] == "MCP-INV-1"
    detail = call(rt, "erp_invoice_get_invoice", {"invoice_id": 1})
    assert detail.structuredContent["data"]["invoice_title"] == "测试开票抬头"
    assert "tax_no" not in detail.structuredContent["data"]
    rest_detail_response = rest_client.get("/invoices/1", headers=rest_headers)
    assert rest_detail_response.status_code == 200
    rest_detail = rest_detail_response.json()["data"]
    for field in (
        "id", "invoice_no", "document_type", "invoice_title", "invoice_type",
        "invoice_type_name", "amount", "received_amount", "unreceived_amount",
        "is_received", "company", "status", "saved_at", "created_at", "updated_at",
    ):
        assert detail.structuredContent["data"][field] == rest_detail[field], field
    assert rest_detail["tax_no"] == "TAX-PRIVATE-001"
    assert "tax_no" not in detail.structuredContent["data"]
    assert "applicant" not in detail.structuredContent["data"]

    rest_receivable = rest_client.get("/invoices/3", headers=rest_headers)
    assert rest_receivable.status_code == 200
    assert rest_receivable.json()["data"]["document_type"] == "RECEIVABLE"
    assert call(rt, "erp_invoice_get_invoice", {
        "invoice_id": 3,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_invoice_get_invoice", {
        "invoice_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_invoice_list_invoices", {
        "status": "UNKNOWN",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        after = db.scalar(select(func.count(Invoice.id)))
    assert before == after


def test_supplier_balance_tools_mask_account_data_and_never_sync(factory):
    from decimal import Decimal
    from sqlalchemy import delete
    from app.api.invoice import router as invoice_router
    from app.core.data_scope import clear_scope_policy
    from app.models.party import Account, Customer, Supplier
    from app.models.rbac import StaffRole

    tool_names = [
        ACCESS_TOOL,
        "erp_invoice_list_supplier_account_balances",
        "erp_invoice_get_supplier_account_balance",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:finance"],
    )["token"]
    multi_company = call(
        runtime(factory, token), "erp_invoice_list_supplier_account_balances", {},
    )
    assert not multi_company.isError
    assert multi_company.structuredContent["page"]["total"] == 0

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Supplier(id=1, code="BAL-SUP", name="余额查询供应商"),
            Supplier(id=2, code="OTHER-SUP", name="另一家供应商"),
            Customer(id=1, code="BAL-CUS", name="客户账户"),
            Account(
                id=1, supplier_id=1, bank="测试银行", branch="测试支行",
                bank_no="1234 5678", account_no="1234567890123456",
                address="开户地址不外发", balance=Decimal("123.45"),
                kingdee_account_code="KINGDEE-PRIVATE",
                kingdee_sync_status="SYNCED",
                kingdee_sync_message="同步错误不外发", is_default=True,
            ),
            Account(
                id=2, supplier_id=1, bank="备用银行", account_no="9999",
                balance=Decimal("9.00"), kingdee_sync_status="NOT_SYNCED",
            ),
            Account(
                id=3, supplier_id=2, bank="另一银行", account_no="8888",
                balance=Decimal("50.00"), kingdee_sync_status="FAILED",
            ),
            Account(
                id=4, customer_id=1, bank="客户银行", account_no="7777",
                balance=Decimal("70.00"),
            ),
        ])
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, token)
    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(invoice_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    finance_headers = {
        "Authorization": "Bearer " + create_access_token(1, "甲", []),
    }
    with TestClient(rest_app) as rest_client:
        rest_page_first_response = rest_client.get(
            "/supplier-account-balances",
            params={"supplier_id": 1, "page": 1, "page_size": 1},
            headers=finance_headers,
        )
        rest_page_second_response = rest_client.get(
            "/supplier-account-balances",
            params={"supplier_id": 1, "page": 2, "page_size": 1},
            headers=finance_headers,
        )
        rest_filtered_response = rest_client.get(
            "/supplier-account-balances", params={"sync_status": "SYNCED"},
            headers=finance_headers,
        )
        rest_detail_response = rest_client.get(
            "/supplier-account-balances/1", headers=finance_headers,
        )
    assert all(response.status_code == 200 for response in (
        rest_page_first_response, rest_page_second_response,
        rest_filtered_response, rest_detail_response,
    ))

    with factory() as db:
        before = [
            (row.id, row.balance, row.kingdee_sync_status, row.balance_synced_at)
            for row in db.scalars(select(Account).order_by(Account.id)).all()
        ]

    page = call(rt, "erp_invoice_list_supplier_account_balances", {
        "supplier_id": 1, "page_size": 1,
    })
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    listed = page.structuredContent["data"][0]
    assert listed["id"] == 2
    rest_first = rest_page_first_response.json()["data"]
    assert listed["id"] == rest_first["items"][0]["id"]
    assert Decimal(listed["balance"]) == Decimal(str(rest_first["items"][0]["balance"]))
    second_page = call(rt, "erp_invoice_list_supplier_account_balances", {
        "supplier_id": 1, "page": 2, "page_size": 1,
    }).structuredContent
    assert second_page["data"][0]["id"] == 1
    assert second_page["data"][0]["id"] == rest_page_second_response.json()["data"]["items"][0]["id"]
    assert call(rt, "erp_invoice_list_supplier_account_balances", {
        "keyword": "BAL-SUP",
    }).structuredContent["page"]["total"] == 2
    filtered = call(rt, "erp_invoice_list_supplier_account_balances", {
        "sync_status": "SYNCED",
    }).structuredContent
    assert filtered["page"]["total"] == 1
    assert filtered["data"][0]["id"] == rest_filtered_response.json()["data"]["items"][0]["id"]
    for private_search in (
        "1234567890123456", "测试银行", "KINGDEE-PRIVATE", "同步错误不外发",
    ):
        assert call(rt, "erp_invoice_list_supplier_account_balances", {
            "keyword": private_search,
        }).structuredContent["page"]["total"] == 0

    detail = call(rt, "erp_invoice_get_supplier_account_balance", {"account_id": 1})
    safe = detail.structuredContent["data"]
    rest_detail = rest_detail_response.json()["data"]
    assert safe["supplier_code"] == "BAL-SUP"
    assert safe["id"] == rest_detail["id"]
    assert safe["supplier_code"] == rest_detail["supplier_code"]
    assert safe["bank"] == rest_detail["bank"]
    assert Decimal(safe["balance"]) == Decimal(str(rest_detail["balance"]))
    assert safe["bank_no"] == "****5678"
    assert safe["account_no"] == "****3456"
    assert Decimal(safe["balance"]) == Decimal("123.45")
    assert not {
        "address", "kingdee_account_code", "kingdee_sync_message",
    } & set(safe)
    assert call(rt, "erp_invoice_get_supplier_account_balance", {
        "account_id": 4,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_invoice_get_supplier_account_balance", {
        "account_id": 9999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_invoice_get_supplier_account_balance", {
        "account_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_invoice_list_supplier_account_balances", {
        "sync_status": "UNKNOWN",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    insufficient_scope = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:purchase"],
    )["token"]
    denied = call(
        runtime(factory, insufficient_scope),
        "erp_invoice_list_supplier_account_balances", {},
    )
    assert denied.structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after = [
            (row.id, row.balance, row.kingdee_sync_status, row.balance_synced_at)
            for row in db.scalars(select(Account).order_by(Account.id)).all()
        ]
    assert before == after


def test_logistics_tools_are_company_scoped_and_hide_contact_details(factory):
    from sqlalchemy import delete, func
    from app.models.platform import Logistics
    from app.models.rbac import StaffRole

    tool_names = [
        ACCESS_TOOL, "erp_logistics_list_logistics", "erp_logistics_get_logistics",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    multi_company = call(runtime(factory, token), "erp_logistics_list_logistics", {})
    assert not multi_company.isError
    assert multi_company.structuredContent["page"]["total"] == 0

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Logistics(
                id=1, name="顺丰", company="顺丰速运有限公司", contact="私人联系人",
                phone="13800138000", address="不对外的详细地址",
                is_default=True, is_enabled=True,
            ),
            Logistics(
                id=2, name="圆通", company="圆通速递有限公司", contact="另一联系人",
                phone="13900139000", address="另一详细地址",
                is_default=False, is_enabled=True,
            ),
            Logistics(
                id=3, name="已停用承运商", is_default=False, is_enabled=False,
            ),
        ])
        db.commit()
        before = [
            (row.id, row.name, row.is_default, row.is_enabled)
            for row in db.scalars(select(Logistics).order_by(Logistics.id)).all()
        ]

    rt = runtime(factory, token)
    page = call(rt, "erp_logistics_list_logistics", {
        "keyword": "顺丰", "page_size": 1,
    })
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 1, "has_more": False,
    }
    carrier = page.structuredContent["data"][0]
    assert carrier["name"] == "顺丰"
    assert carrier["is_default"] is True
    assert not {"contact", "phone", "address"} & set(carrier)
    assert call(rt, "erp_logistics_list_logistics", {
        "is_enabled": False,
    }).structuredContent["page"]["total"] == 1
    for private_search in ("私人联系人", "13800138000", "不对外的详细地址"):
        assert call(rt, "erp_logistics_list_logistics", {
            "keyword": private_search,
        }).structuredContent["page"]["total"] == 0

    detail = call(rt, "erp_logistics_get_logistics", {"logistics_id": 1})
    assert detail.structuredContent["data"]["company"] == "顺丰速运有限公司"
    assert not {"contact", "phone", "address"} & set(detail.structuredContent["data"])
    assert call(rt, "erp_logistics_get_logistics", {
        "logistics_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_logistics_get_logistics", {
        "logistics_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_logistics_list_logistics", {
        "is_enabled": "false",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    insufficient_scope = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:purchase"],
    )["token"]
    denied = call(runtime(factory, insufficient_scope), "erp_logistics_list_logistics", {})
    assert denied.structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after = [
            (row.id, row.name, row.is_default, row.is_enabled)
            for row in db.scalars(select(Logistics).order_by(Logistics.id)).all()
        ]
        assert db.scalar(select(func.count(Logistics.id))) == len(before)
    assert before == after


def test_freight_template_tools_page_and_gate_fees_by_finance_permission(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.platform import FreightTemplate, Logistics
    from app.models.rbac import StaffRole

    tool_names = [
        ACCESS_TOOL, "erp_logistics_list_freight_templates",
        "erp_logistics_get_freight_template",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    multi_company = call(
        runtime(factory, token), "erp_logistics_list_freight_templates", {},
    )
    assert not multi_company.isError
    assert multi_company.structuredContent["page"]["total"] == 0

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add(Logistics(id=1, name="顺丰", is_default=True, is_enabled=True))
        db.add_all([
            FreightTemplate(
                id=1, name="按件计费", logistics_id=1, charge_type="BY_COUNT",
                first_count=1, continue_count=1, base_fee=Decimal("12.50"),
                continue_fee=Decimal("3.00"), operation_fee=Decimal("1.00"),
                consumable_fee=Decimal("0.50"), ewaybill_fee=Decimal("0.20"),
                tax_fee=Decimal("0.80"), tax_point=Decimal("6.00"),
            ),
            FreightTemplate(
                id=2, name="按重计费", logistics_id=1, charge_type="BY_WEIGHT",
                first_count=1, continue_count=1, first_weight=Decimal("1.000"),
                continue_weight=Decimal("0.500"), deduct_weight=Decimal("0.200"),
                base_fee=Decimal("18.00"), continue_fee=Decimal("4.00"),
                operation_fee=Decimal("0.00"), consumable_fee=Decimal("0.00"),
                ewaybill_fee=Decimal("0.00"), tax_fee=Decimal("0.00"),
            ),
        ])
        db.commit()
        before = [
            (row.id, row.name, row.base_fee, row.continue_fee)
            for row in db.scalars(select(FreightTemplate).order_by(FreightTemplate.id)).all()
        ]

    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    rt = runtime(factory, token)
    page = call(rt, "erp_logistics_list_freight_templates", {
        "logistics_id": 1, "page_size": 1,
    })
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    item = page.structuredContent["data"][0]
    assert item["id"] == 2
    assert item["logistics_name"] == "顺丰"
    assert "base_fee" not in item and "tax_point" not in item
    assert call(rt, "erp_logistics_list_freight_templates", {
        "logistics_id": 1, "page": 2, "page_size": 1,
    }).structuredContent["data"][0]["id"] == 1
    assert call(rt, "erp_logistics_list_freight_templates", {
        "logistics_id": 999,
    }).structuredContent["page"]["total"] == 0
    detail = call(rt, "erp_logistics_get_freight_template", {"template_id": 1})
    assert detail.structuredContent["data"]["charge_type"] == "BY_COUNT"
    assert "base_fee" not in detail.structuredContent["data"]
    assert call(rt, "erp_logistics_get_freight_template", {
        "template_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    finance_role = Role(id=4, company_id=1, code="FINANCE", name="财务")
    with factory() as db:
        db.add(finance_role)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=4))
        db.commit()
    finance_token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:order", "module:finance"],
    )["token"]
    finance_detail = call(
        runtime(factory, finance_token), "erp_logistics_get_freight_template",
        {"template_id": 1},
    )
    assert Decimal(finance_detail.structuredContent["data"]["base_fee"]) == Decimal("12.50")
    assert Decimal(finance_detail.structuredContent["data"]["tax_point"]) == Decimal("6.00")

    insufficient_scope = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:purchase"],
    )["token"]
    denied = call(
        runtime(factory, insufficient_scope), "erp_logistics_list_freight_templates", {},
    )
    assert denied.structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        after = [
            (row.id, row.name, row.base_fee, row.continue_fee)
            for row in db.scalars(select(FreightTemplate).order_by(FreightTemplate.id)).all()
        ]
        assert db.scalar(select(func.count(FreightTemplate.id))) == len(before)
    assert before == after


def test_unread_notification_count_uses_current_staff_visibility(factory, monkeypatch):
    from app.services import notification_query_service

    seen = {}

    def visible_notifications(db, staff, **kwargs):
        seen.update(staff_id=staff.id, **kwargs)
        return iter((object(), object(), object()))

    monkeypatch.setattr(
        notification_query_service, "iter_visible_notifications", visible_notifications,
    )
    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_notification_count_unread"],
        permission_scopes=["module:purchase"],
    )["token"]
    result = call(runtime(factory, token), "erp_notification_count_unread", {})
    assert result.structuredContent["data"] == {"count": 3}
    assert seen == {"staff_id": 1, "direction": "desc", "read_state": "UNREAD"}


def test_unread_notification_count_batches_candidates_without_marking_read(factory, monkeypatch):
    from sqlalchemy import event, func
    from app.models.notification import NotificationInbox, NotificationOutbox
    from app.services import notification_query_service

    now = utc_now()
    with factory() as db:
        staff = db.get(Staff, 1)
        outbox = [
            NotificationOutbox(
                id=row_id, company_id=staff.company_id,
                event_code="approval.task_assigned", schema_version=1,
                event_key=f"batch-count-{row_id}", aggregate_type="purchase_order",
                aggregate_id=1, source_type="test", source_id=row_id,
                occurred_at=now, payload_json={"doc_type": "purchase_order", "doc_id": 1},
                status="PUBLISHED", available_at=now,
            )
            for row_id in range(1, 402)
        ]
        db.add_all(outbox)
        db.flush()
        db.add_all([
            NotificationInbox(
                id=row_id, company_id=staff.company_id,
                recipient_staff_id=staff.id, outbox_id=row_id, channel="IN_APP",
                event_code="approval.task_assigned", title="测试通知", body="通知内容",
                target_type="purchase_order", target_id=1, source_type="test",
                source_id=row_id, read_state="UNREAD", business_state="OPEN",
            )
            for row_id in range(1, 402)
        ])
        db.commit()

    monkeypatch.setattr(
        notification_query_service, "_notification_is_visible", lambda *_args: True,
    )
    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_notification_count_unread"],
        permission_scopes=["module:purchase"],
    )["token"]
    statements = []

    def capture_notification_select(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT") and "notification_inbox" in statement.lower():
            statements.append(statement)

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_notification_select)
    try:
        result = call(runtime(factory, token), "erp_notification_count_unread", {})
    finally:
        event.remove(engine, "before_cursor_execute", capture_notification_select)

    assert result.structuredContent["data"] == {"count": 401}
    assert len(statements) == 3  # 200 + 200 + 1 条，最后一批后停止。
    with factory() as db:
        assert db.scalar(select(func.count(NotificationInbox.id)).where(
            NotificationInbox.read_state == "UNREAD",
        )) == 401


def test_notification_detail_rechecks_current_visibility_without_marking_read(
    factory, monkeypatch,
):
    from types import SimpleNamespace
    from app.services import notification_query_service

    inbox = SimpleNamespace(id=23, read_state="UNREAD")
    seen = {}

    def get_visible(db, staff, inbox_id):
        seen.update(staff_id=staff.id, company_id=staff.company_id, inbox_id=inbox_id)
        return inbox if inbox_id == inbox.id else None

    def serialize(row, *, staff_id, company_id):
        seen.update(serialized_staff_id=staff_id, serialized_company_id=company_id)
        return {
            "id": row.id, "event_code": "approval.pending",
            "category": "approval", "title": "测试通知", "body": "待审批",
            "target": {"type": "purchase_plan", "id": 7},
            "read_state": row.read_state, "read_at": None,
            "business_state": "pending", "created_at": "2026-10-13T10:00:00",
            "cursor": "23",
        }

    monkeypatch.setattr(
        notification_query_service, "get_visible_notification", get_visible,
    )
    monkeypatch.setattr(
        notification_query_service, "serialize_notification", serialize,
    )
    token = issue(
        factory, tool_scopes=[ACCESS_TOOL, "erp_get_my_notification"],
    )["token"]
    rt = runtime(factory, token)
    result = call(rt, "erp_get_my_notification", {"inbox_id": inbox.id})
    assert result.structuredContent["data"] == {
        "id": inbox.id, "event_code": "approval.pending",
        "category": "approval", "title": "测试通知", "body": "待审批",
        "target": {"type": "purchase_plan", "id": 7},
        "read_state": "UNREAD", "read_at": None,
        "business_state": "pending", "created_at": "2026-10-13T10:00:00",
        "cursor": "23",
    }
    assert seen == {
        "staff_id": 1, "company_id": 1, "inbox_id": inbox.id,
        "serialized_staff_id": 1, "serialized_company_id": 1,
    }
    assert inbox.read_state == "UNREAD"
    assert call(rt, "erp_get_my_notification", {
        "inbox_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_get_my_notification", {
        "inbox_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"


def test_notification_list_pages_for_current_staff_without_marking_read(
    factory, monkeypatch,
):
    from types import SimpleNamespace
    from app.services import notification_query_service

    inbox = SimpleNamespace(id=23, read_state="UNREAD")
    observed = []

    def query_page(db, staff, *, cursor_id, category, read_state, page_size):
        observed.append({
            "staff_id": staff.id, "company_id": staff.company_id,
            "cursor_id": cursor_id, "category": category,
            "read_state": read_state, "page_size": page_size,
        })
        if cursor_id == 0:
            return [(inbox, SimpleNamespace())], inbox.id, True
        return [], None, False

    def serialize(row, *, staff_id, company_id):
        assert row is inbox
        return {
            "id": row.id, "event_code": "approval.pending",
            "category": "approval", "title": "审批待办通知", "body": "待审批",
            "target": {"type": "purchase_plan", "id": 7},
            "read_state": row.read_state, "read_at": None,
            "business_state": "pending", "created_at": "2026-10-13T10:00:00",
            "cursor": str(row.id),
        }

    monkeypatch.setattr(notification_query_service, "query_notifications_page", query_page)
    monkeypatch.setattr(notification_query_service, "serialize_notification", serialize)
    tool = "erp_list_my_notifications"
    tools = [ACCESS_TOOL, tool]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)

    first = call(rt, tool, {
        "category": "approval", "read_state": "UNREAD", "page_size": 1,
    }).structuredContent
    assert first["data"]["items"] == [{
        "id": inbox.id, "event_code": "approval.pending",
        "category": "approval", "title": "审批待办通知", "body": "待审批",
        "target": {"type": "purchase_plan", "id": 7},
        "read_state": "UNREAD", "read_at": None,
        "business_state": "pending", "created_at": "2026-10-13T10:00:00",
        "cursor": str(inbox.id),
    }]
    assert len(first["data"]["items"]) == 1
    assert first["page"]["page_size"] == 1
    assert first["page"]["has_more"] is True
    next_cursor = first["page"]["next_cursor"]
    assert notification_query_service.decode_cursor(
        next_cursor, staff_id=1, company_id=1,
    ) == inbox.id

    second = call(rt, tool, {
        "cursor": next_cursor, "category": "approval",
        "read_state": "UNREAD", "page_size": 1,
    }).structuredContent
    assert second["data"]["items"] == []
    assert second["page"]["has_more"] is False
    assert observed == [
        {"staff_id": 1, "company_id": 1, "cursor_id": 0,
         "category": "approval", "read_state": "UNREAD", "page_size": 1},
        {"staff_id": 1, "company_id": 1, "cursor_id": inbox.id,
         "category": "approval", "read_state": "UNREAD", "page_size": 1},
    ]
    assert inbox.read_state == "UNREAD"

    other_staff_token = issue(
        factory, staff_id=2, tool_scopes=tools,
        permission_scopes=["module:stock"],
    )["token"]
    assert call(runtime(factory, other_staff_token), tool, {
        "cursor": next_cursor,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert len(observed) == 2  # 无效的跨身份游标在触达查询函数前被拒绝。

    missing_tool_scope = issue(
        factory, tool_scopes=[ACCESS_TOOL], permission_scopes=["module:purchase"],
    )["token"]
    denied = call(runtime(factory, missing_tool_scope), tool, {})
    assert denied.isError and denied.structuredContent["code"] == "FORBIDDEN"


def test_notification_reads_match_rest_visibility_scope_and_unread_count(factory, monkeypatch):
    from sqlalchemy import event
    from app.api.notification import router as notification_router
    from app.core.data_scope import clear_scope_policy
    from app.models.notification import NotificationInbox, NotificationOutbox
    from app.services import notification_query_service

    now = utc_now()
    with factory() as db:
        for inbox_id, company_id, staff_id, read_state in [
            (1, 1, 1, "UNREAD"),
            (2, 1, 1, "UNREAD"),  # 当前单据权限复核后不可见。
            (3, 1, 1, "UNREAD"),
            (4, 1, 1, "READ"),
            (5, 2, 2, "UNREAD"),
        ]:
            db.add(NotificationOutbox(
                id=inbox_id, company_id=company_id,
                event_code="purchase.plan_created", schema_version=1,
                event_key=f"rest-parity-{inbox_id}", aggregate_type="purchase_plan",
                aggregate_id=1, source_type="test", source_id=inbox_id,
                occurred_at=now, payload_json={"doc_type": "purchase_plan", "doc_id": 1},
                status="PUBLISHED", available_at=now,
            ))
            db.add(NotificationInbox(
                id=inbox_id, company_id=company_id,
                recipient_staff_id=staff_id, outbox_id=inbox_id, channel="IN_APP",
                event_code="purchase.plan_created", title=f"通知{inbox_id}",
                body="测试通知内容", target_type="purchase_plan", target_id=1,
                source_type="test", source_id=inbox_id, read_state=read_state,
                business_state="OPEN",
            ))
        db.commit()

    monkeypatch.setattr(
        notification_query_service, "_notification_is_visible",
        lambda _db, _staff, inbox, _event: inbox.id != 2 and inbox.id % 7 != 0,
    )
    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(notification_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    primary_tools = [
        ACCESS_TOOL, "erp_list_my_notifications", "erp_notification_count_unread",
        "erp_get_my_notification",
    ]
    primary_token = issue(
        factory, staff_id=1, tool_scopes=primary_tools,
        permission_scopes=["module:purchase"],
    )["token"]
    primary_runtime = runtime(factory, primary_token)
    with TestClient(rest_app) as rest_client:
        rest_first = rest_client.get(
            "/notifications",
            params={"category": "purchase", "read_state": "UNREAD", "page_size": 1},
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
        assert rest_first.status_code == 200
        mcp_first = call(primary_runtime, "erp_list_my_notifications", {
            "category": "purchase", "read_state": "UNREAD", "page_size": 1,
        }).structuredContent
        assert mcp_first["data"]["items"] == rest_first.json()["data"]["items"]

        rest_second = rest_client.get(
            "/notifications",
            params={
                "category": "purchase", "read_state": "UNREAD", "page_size": 1,
                "cursor": rest_first.json()["data"]["next_cursor"],
            },
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
        mcp_second = call(primary_runtime, "erp_list_my_notifications", {
            "category": "purchase", "read_state": "UNREAD", "page_size": 1,
            "cursor": mcp_first["page"]["next_cursor"],
        }).structuredContent
        assert rest_second.status_code == 200
        assert mcp_second["data"]["items"] == rest_second.json()["data"]["items"]
        assert [
            row["id"] for row in mcp_first["data"]["items"] + mcp_second["data"]["items"]
        ] == [3, 1]

        rest_detail = rest_client.get(
            "/notifications/1",
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
        mcp_detail = call(
            primary_runtime, "erp_get_my_notification", {"inbox_id": 1},
        ).structuredContent
        assert rest_detail.status_code == 200
        assert mcp_detail["data"] == rest_detail.json()["data"]

        hidden_rest_detail = rest_client.get(
            "/notifications/2",
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
        hidden_mcp_detail = call(
            primary_runtime, "erp_get_my_notification", {"inbox_id": 2},
        ).structuredContent
        assert hidden_rest_detail.status_code == 404
        assert hidden_mcp_detail["code"] == "FORBIDDEN"

        rest_unread = rest_client.get(
            "/notifications/unread-count",
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
        mcp_unread = call(
            primary_runtime, "erp_notification_count_unread", {},
        ).structuredContent["data"]
        assert rest_unread.status_code == 200
        assert mcp_unread == rest_unread.json()["data"] == {"count": 2}

        secondary_token = issue(
            factory, staff_id=2,
            tool_scopes=[ACCESS_TOOL, "erp_notification_count_unread"],
            permission_scopes=["module:stock"],
        )["token"]
        secondary_mcp = call(
            runtime(factory, secondary_token), "erp_notification_count_unread", {},
        ).structuredContent["data"]
        secondary_rest = rest_client.get(
            "/notifications/unread-count",
            headers={"Authorization": "Bearer " + create_access_token(2, "乙", [])},
        )
        assert secondary_rest.status_code == 200
        assert secondary_mcp == secondary_rest.json()["data"] == {"count": 1}

        with factory() as db:
            db.add_all([
                NotificationOutbox(
                    id=inbox_id, company_id=1,
                    event_code="purchase.plan_created", schema_version=1,
                    event_key=f"notification-scale-{inbox_id}",
                    aggregate_type="purchase_plan", aggregate_id=1,
                    source_type="test", source_id=inbox_id, occurred_at=now,
                    payload_json={"doc_type": "purchase_plan", "doc_id": 1},
                    status="PUBLISHED", available_at=now,
                )
                for inbox_id in range(6, 1607)
            ])
            db.add_all([
                NotificationInbox(
                    id=inbox_id, company_id=1, recipient_staff_id=1,
                    outbox_id=inbox_id, channel="IN_APP",
                    event_code="purchase.plan_created", title=f"批量通知{inbox_id}",
                    body="批量测试通知", target_type="purchase_plan", target_id=1,
                    source_type="test", source_id=inbox_id,
                    read_state="UNREAD", business_state="OPEN",
                )
                for inbox_id in range(6, 1607)
            ])
            db.commit()

        scaled_rest = rest_client.get(
            "/notifications/unread-count",
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
        scale_scan_statements = []

        def capture_notification_scan(_connection, _cursor, statement, parameters, *_args):
            lowered = statement.lower()
            if (
                "from notification_inbox join notification_outbox" in lowered
                and "order by notification_inbox.id desc" in lowered
            ):
                scale_scan_statements.append((lowered, parameters))

        engine = factory.kw["bind"]
        event.listen(engine, "before_cursor_execute", capture_notification_scan)
        try:
            scaled_mcp = call(
                primary_runtime, "erp_notification_count_unread", {},
            ).structuredContent["data"]
        finally:
            event.remove(engine, "before_cursor_execute", capture_notification_scan)
        # 1601新增候选与既有未读项共需9批；逐项权限复核隐藏229条倍数记录。
        assert scaled_rest.status_code == 200
        assert scaled_mcp == scaled_rest.json()["data"] == {"count": 1374}
        assert len(scale_scan_statements) == 9
        assert all("limit" in statement for statement, _ in scale_scan_statements)
        for _statement, parameters in scale_scan_statements:
            values = parameters.values() if isinstance(parameters, dict) else parameters
            assert 200 in values

    with factory() as db:
        states = dict(db.query(NotificationInbox.id, NotificationInbox.read_state).all())
    assert {inbox_id: states[inbox_id] for inbox_id in range(1, 6)} == {
        1: "UNREAD", 2: "UNREAD", 3: "UNREAD", 4: "READ", 5: "UNREAD",
    }
    assert len(states) == 1606
    assert all(states[inbox_id] == "UNREAD" for inbox_id in range(6, 1607))


def test_purchase_prepay_detail_uses_scope_and_fixed_projection(factory, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    from sqlalchemy import delete
    from app.services import prepay_service

    seen = {}
    monkeypatch.setattr(
        prepay_service, "get_prepay_or_404",
        lambda db, prepay_id: (seen.update(prepay_id=prepay_id) or SimpleNamespace(id=prepay_id)),
    )

    def prepay_dict(db, row, *, with_approval, with_voucher):
        seen["detail_flags"] = (with_approval, with_voucher)
        return {
            "id": row.id, "prepay_no": "PREPAY-MCP-1",
            "supplier_id": 7, "supplier_name": "测试供应商",
            "prepay_amount": Decimal("52.10"), "status": "PENDING",
            "audit_status": "PENDING", "account_no": "PRIVATE-BANK-ACCOUNT",
            "pay_note": "PRIVATE-PAY-NOTE", "write_off_note": "PRIVATE-WRITE-OFF",
            "created_by_name": "PRIVATE-STAFF", "voucher": "PRIVATE-VOUCHER",
            "created_at": "2026-10-13T10:00:00", "updated_at": "2026-10-13T10:00:00",
            "order_amount": None, "order_id": None, "order_no": None,
            "unwritten_amount": "52.10", "write_off_amount": "0.00",
        }

    monkeypatch.setattr(prepay_service, "prepay_dict", prepay_dict)
    tools = [ACCESS_TOOL, "erp_get_purchase_prepay"]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)
    data = call(rt, "erp_get_purchase_prepay", {
        "prepay_id": 17,
    }).structuredContent["data"]
    assert data["prepay_no"] == "PREPAY-MCP-1"
    assert Decimal(data["prepay_amount"]) == Decimal("52.10")
    assert not {
        "account_no", "pay_note", "write_off_note", "created_by_name", "voucher",
    } & set(data)
    assert seen == {"prepay_id": 17, "detail_flags": (False, False)}
    assert call(rt, "erp_get_purchase_prepay", {
        "prepay_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    with factory() as db:
        db.execute(delete(StaffRole).where(
            StaffRole.staff_id == 1, StaffRole.role_id == 1,
        ))
        db.commit()
    assert call(rt, "erp_get_purchase_prepay", {
        "prepay_id": 17,
    }).structuredContent["code"] == "FORBIDDEN"


def test_purchase_invoice_detail_avoids_cost_allocation_and_free_text(factory, monkeypatch):
    from datetime import datetime
    from decimal import Decimal
    from types import SimpleNamespace
    from app.models.rbac import StaffRole
    from app.services import purchase_invoice_service

    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()

    invoice = SimpleNamespace(
        id=61, invoice_no="PUR-INV-MCP-1", supplier_id=999,
        invoice_type="SPECIAL", tax_rate=Decimal("13.00"),
        amount=Decimal("113.00"), tax_amount=Decimal("13.00"),
        write_off_amount=Decimal("23.00"), write_off_status="PARTIAL",
        business_type="PURCHASE", status="APPROVED", audit_status="APPROVED",
        cost_accounted_at=datetime(2026, 10, 1, 8),
        created_at=datetime(2026, 10, 1, 7), updated_at=datetime(2026, 10, 1, 8),
        description="PRIVATE-DESCRIPTION", voucher_no="PRIVATE-VOUCHER",
        cost_allocation='[{"cost_price":"PRIVATE-COST"}]', stock_in_ids="[1,2,3]",
        created_by=1, created_by_name="PRIVATE-STAFF",
    )
    seen = {}

    def get_invoice(db, invoice_id):
        seen["invoice_id"] = invoice_id
        return invoice if invoice_id == invoice.id else None

    monkeypatch.setattr(
        purchase_invoice_service, "get_invoice_or_404", get_invoice,
    )
    tools = [ACCESS_TOOL, "erp_get_purchase_invoice"]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, token)
    data = call(rt, "erp_get_purchase_invoice", {
        "invoice_id": invoice.id,
    }).structuredContent["data"]
    assert data["invoice_type_name"] == purchase_invoice_service.INVOICE_TYPES["SPECIAL"]["name"]
    assert Decimal(data["amount"]) == Decimal("113.00")
    assert Decimal(data["net_amount"]) == Decimal("100.00")
    assert not {
        "description", "voucher_no", "cost_allocation", "stock_in_ids",
        "created_by", "created_by_name",
    } & set(data)
    assert "PRIVATE" not in json.dumps(data)
    assert seen == {"invoice_id": invoice.id}
    assert call(rt, "erp_get_purchase_invoice", {
        "invoice_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"


def test_area_rule_tools_page_match_and_gate_freight_fees(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.platform import AreaRule, FreightTemplate, Logistics
    from app.models.rbac import StaffRole

    tool_names = [
        ACCESS_TOOL, "erp_logistics_match_area_rule",
        "erp_logistics_list_area_rules", "erp_logistics_get_area_rule",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    assert call(runtime(factory, token), "erp_logistics_list_area_rules", {}).structuredContent["code"] == "FORBIDDEN"
    assert call(runtime(factory, token), "erp_logistics_match_area_rule", {
        "logistics_id": 1,
    }).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add(Logistics(id=1, name="区域测试物流", is_default=True, is_enabled=True))
        db.add(FreightTemplate(
            id=1, name="区域测试模板", logistics_id=1, charge_type="BY_WEIGHT",
            first_count=1, continue_count=1, first_weight=Decimal("1.000"),
            continue_weight=Decimal("0.500"), base_fee=Decimal("18.00"),
            continue_fee=Decimal("4.00"), operation_fee=Decimal("0.00"),
            consumable_fee=Decimal("0.00"), ewaybill_fee=Decimal("0.00"),
            tax_fee=Decimal("0.00"),
        ))
        db.add_all([
            AreaRule(
                id=1, logistics_id=1, province="浙江", match_weight_min=Decimal("0.000"),
                match_weight_max=Decimal("10.000"), freight_template_id=1,
            ),
            AreaRule(
                id=2, logistics_id=1, province="浙江", city="杭州",
                match_weight_min=Decimal("1.000"), match_weight_max=Decimal("5.000"),
                freight_template_id=1,
            ),
        ])
        db.commit()
        before = [
            (row.id, row.province, row.city, row.freight_template_id)
            for row in db.scalars(select(AreaRule).order_by(AreaRule.id)).all()
        ]
        template_before = db.scalar(select(FreightTemplate.base_fee).where(FreightTemplate.id == 1))

    rt = runtime(factory, token)
    page = call(rt, "erp_logistics_list_area_rules", {
        "logistics_id": 1, "page_size": 1,
    })
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert page.structuredContent["data"][0]["id"] == 1
    assert call(rt, "erp_logistics_list_area_rules", {
        "logistics_id": 1, "page": 2, "page_size": 1,
    }).structuredContent["data"][0]["id"] == 2
    assert call(rt, "erp_logistics_list_area_rules", {
        "logistics_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    detail = call(rt, "erp_logistics_get_area_rule", {"rule_id": 2})
    assert detail.structuredContent["data"]["city"] == "杭州"
    match = call(rt, "erp_logistics_match_area_rule", {
        "logistics_id": 1, "province": "浙江", "city": "杭州", "weight": 1.5,
    })
    assert match.structuredContent["data"]["matched"] is True
    assert match.structuredContent["data"]["area_rule"]["id"] == 2
    assert "base_fee" not in match.structuredContent["data"]["freight_template"]
    no_match = call(rt, "erp_logistics_match_area_rule", {
        "logistics_id": 1, "province": "安徽", "city": "合肥",
    })
    assert no_match.structuredContent["data"]["matched"] is False
    assert call(rt, "erp_logistics_match_area_rule", {
        "logistics_id": 1, "weight": -1,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_logistics_get_area_rule", {
        "rule_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    finance_role = Role(id=4, company_id=1, code="FINANCE", name="财务")
    with factory() as db:
        db.add(finance_role)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=4))
        db.commit()
    finance_token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:order", "module:finance"],
    )["token"]
    finance_match = call(
        runtime(factory, finance_token), "erp_logistics_match_area_rule",
        {"logistics_id": 1, "province": "浙江", "city": "杭州", "weight": 1.5},
    )
    assert Decimal(
        finance_match.structuredContent["data"]["freight_template"]["base_fee"]
    ) == Decimal("18.00")
    with factory() as db:
        after = [
            (row.id, row.province, row.city, row.freight_template_id)
            for row in db.scalars(select(AreaRule).order_by(AreaRule.id)).all()
        ]
        template_after = db.scalar(select(FreightTemplate.base_fee).where(FreightTemplate.id == 1))
        assert db.scalar(select(func.count(AreaRule.id))) == len(before)
    assert before == after
    assert template_before == template_after


def test_freight_calculation_tool_gates_amounts_and_is_read_only(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.platform import FreightTemplate, Logistics
    from app.models.rbac import StaffRole

    tool_names = [ACCESS_TOOL, "erp_logistics_calculate_freight"]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    assert call(
        runtime(factory, token), "erp_logistics_calculate_freight",
        {"template_id": 1, "count": 3},
    ).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add(Logistics(id=1, name="运费试算物流", is_default=True, is_enabled=True))
        db.add_all([
            FreightTemplate(
                id=1, name="按件模板", logistics_id=1, charge_type="BY_COUNT",
                first_count=1, continue_count=1, base_fee=Decimal("12.50"),
                continue_fee=Decimal("3.00"), operation_fee=Decimal("1.00"),
                consumable_fee=Decimal("0.50"), ewaybill_fee=Decimal("0.20"),
                tax_fee=Decimal("0.80"), tax_point=Decimal("6.00"),
            ),
            FreightTemplate(
                id=2, name="按重模板", logistics_id=1, charge_type="BY_WEIGHT",
                first_count=1, continue_count=1, first_weight=Decimal("1.000"),
                continue_weight=Decimal("0.500"), deduct_weight=Decimal("0.200"),
                base_fee=Decimal("18.00"), continue_fee=Decimal("4.00"),
                operation_fee=Decimal("0.00"), consumable_fee=Decimal("0.00"),
                ewaybill_fee=Decimal("0.00"), tax_fee=Decimal("0.00"),
            ),
        ])
        db.commit()
        before = [
            (row.id, row.base_fee, row.continue_fee)
            for row in db.scalars(select(FreightTemplate).order_by(FreightTemplate.id)).all()
        ]

    rt = runtime(factory, token)
    count_result = call(rt, "erp_logistics_calculate_freight", {
        "template_id": 1, "count": 3,
    }).structuredContent["data"]
    assert count_result["quantity"] == 3
    assert count_result["additional_units"] == 2
    assert "total_fee" not in count_result and "base_fee" not in count_result
    weight_result = call(rt, "erp_logistics_calculate_freight", {
        "template_id": 2, "weight": 2.0,
    }).structuredContent["data"]
    assert Decimal(str(weight_result["charge_weight"])) == Decimal("1.800")
    assert "tax_amount" not in weight_result
    assert call(rt, "erp_logistics_calculate_freight", {
        "template_id": 1,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_logistics_calculate_freight", {
        "template_id": 1, "count": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        db.add(Role(id=4, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=4))
        db.commit()
    finance_token = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:order", "module:finance"],
    )["token"]
    finance_result = call(
        runtime(factory, finance_token), "erp_logistics_calculate_freight",
        {"template_id": 1, "count": 3},
    ).structuredContent["data"]
    assert Decimal(str(finance_result["total_fee"])) == Decimal("22.21")
    assert Decimal(str(finance_result["base_fee"])) == Decimal("12.50")
    with factory() as db:
        after = [
            (row.id, row.base_fee, row.continue_fee)
            for row in db.scalars(select(FreightTemplate).order_by(FreightTemplate.id)).all()
        ]
        assert db.scalar(select(func.count(FreightTemplate.id))) == len(before)
    assert before == after


def test_print_template_tools_return_metadata_without_template_content(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.order import PrintTemplate
    from app.models.platform import Logistics
    from app.models.rbac import StaffRole

    tool_names = [
        ACCESS_TOOL, "erp_logistics_list_print_templates",
        "erp_logistics_get_print_template",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    assert call(
        runtime(factory, token), "erp_logistics_list_print_templates", {},
    ).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add(Logistics(id=1, name="打印模板物流", is_default=True, is_enabled=True))
        db.add_all([
            PrintTemplate(
                id=1, name="电子面单", template_type="WAYBILL", logistics_id=1,
                content="<script>recipient_phone</script>",
                paper_width=Decimal("100.00"), paper_height=Decimal("150.00"),
                is_default=True,
            ),
            PrintTemplate(
                id=2, name="拣货单", template_type="PICKING", logistics_id=None,
                content="{{customer_address}}", is_default=False,
            ),
        ])
        db.commit()
        before = [
            (row.id, row.name, row.content)
            for row in db.scalars(select(PrintTemplate).order_by(PrintTemplate.id)).all()
        ]

    rt = runtime(factory, token)
    page = call(rt, "erp_logistics_list_print_templates", {
        "template_type": "WAYBILL", "page_size": 1,
    })
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 1, "has_more": False,
    }
    item = page.structuredContent["data"][0]
    assert item["name"] == "电子面单" and item["logistics_name"] == "打印模板物流"
    assert "content" not in item and "recipient_phone" not in json.dumps(item)
    by_logistics = call(rt, "erp_logistics_list_print_templates", {
        "logistics_id": 1,
    }).structuredContent
    assert by_logistics["page"]["total"] == 1
    assert by_logistics["data"][0]["name"] == "电子面单"
    detail = call(rt, "erp_logistics_get_print_template", {
        "template_id": 1,
    }).structuredContent["data"]
    assert detail["paper_width"] == "100.00"
    assert "content" not in detail and "customer_address" not in json.dumps(detail)
    assert call(rt, "erp_logistics_list_print_templates", {
        "logistics_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_logistics_get_print_template", {
        "template_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    with factory() as db:
        after = [
            (row.id, row.name, row.content)
            for row in db.scalars(select(PrintTemplate).order_by(PrintTemplate.id)).all()
        ]
        assert db.scalar(select(func.count(PrintTemplate.id))) == len(before)
    assert before == after


def test_logistics_query_tools_match_rest_for_same_identity_and_fixed_projection(factory):
    from decimal import Decimal
    from sqlalchemy import delete
    from app.api.logistics import router as logistics_router
    from app.core.data_scope import clear_scope_policy
    from app.models.order import PrintTemplate
    from app.models.platform import AreaRule, FreightTemplate, Logistics

    tools = [
        ACCESS_TOOL,
        "erp_logistics_list_logistics", "erp_logistics_get_logistics",
        "erp_logistics_list_freight_templates", "erp_logistics_calculate_freight",
        "erp_logistics_get_freight_template", "erp_logistics_match_area_rule",
        "erp_logistics_list_area_rules", "erp_logistics_get_area_rule",
        "erp_logistics_list_print_templates", "erp_logistics_get_print_template",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Logistics(
                id=1, name="MCP-LOGI-顺丰", company="MCP-LOGI-顺丰公司",
                contact="私人联系人", phone="13800138000", address="私人地址",
                is_default=True, is_enabled=True,
            ),
            Logistics(
                id=2, name="MCP-LOGI-圆通", company="MCP-LOGI-圆通公司",
                contact="另一联系人", phone="13900139000", address="另一地址",
                is_default=False, is_enabled=True,
            ),
        ])
        db.add_all([
            FreightTemplate(
                id=1, name="MCP-按件模板", logistics_id=1, charge_type="BY_COUNT",
                first_count=1, continue_count=1, base_fee=Decimal("12.50"),
                continue_fee=Decimal("3.00"), operation_fee=Decimal("1.00"),
                consumable_fee=Decimal("0.50"), ewaybill_fee=Decimal("0.20"),
                tax_fee=Decimal("0.80"), tax_point=Decimal("6.00"),
            ),
            FreightTemplate(
                id=2, name="MCP-按重模板", logistics_id=1, charge_type="BY_WEIGHT",
                first_count=1, continue_count=1, first_weight=Decimal("1.000"),
                continue_weight=Decimal("0.500"), deduct_weight=Decimal("0.200"),
                base_fee=Decimal("18.00"), continue_fee=Decimal("4.00"),
            ),
        ])
        db.add_all([
            AreaRule(
                id=1, logistics_id=1, province="浙江",
                match_weight_min=Decimal("0.000"), match_weight_max=Decimal("10.000"),
                freight_template_id=1,
            ),
            AreaRule(
                id=2, logistics_id=1, province="浙江", city="杭州",
                match_weight_min=Decimal("1.000"), match_weight_max=Decimal("5.000"),
                freight_template_id=1,
            ),
        ])
        db.add_all([
            PrintTemplate(
                id=1, name="MCP-面单一", template_type="WAYBILL", logistics_id=1,
                content="<script>recipient_phone</script>",
                paper_width=Decimal("100.00"), paper_height=Decimal("150.00"),
                is_default=True,
            ),
            PrintTemplate(
                id=2, name="MCP-面单二", template_type="WAYBILL", logistics_id=1,
                content="{{customer_address}}", is_default=False,
            ),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:order"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(logistics_router)

    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app.dependency_overrides[get_db] = sessions
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + create_access_token(1, "test", [])}

    def rest_data(path, params=None):
        response = client.get(path, params=params or {}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def mcp_pages(tool_name, filters):
        first = call(rt, tool_name, {**filters, "page": 1, "page_size": 1})
        assert not first.isError, first.structuredContent
        result = list(first.structuredContent["data"])
        page = first.structuredContent["page"]
        while page["has_more"]:
            next_result = call(rt, tool_name, {
                **filters, "page": page["page"] + 1, "page_size": 1,
            })
            assert not next_result.isError, next_result.structuredContent
            result.extend(next_result.structuredContent["data"])
            page = next_result.structuredContent["page"]
        return result

    def assert_fields_match(rest_row, mcp_row, fields):
        for field in fields:
            left, right = rest_row[field], mcp_row[field]
            if left is None or right is None:
                assert left is right, field
            elif all(
                isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
                for value in (left, right)
            ):
                assert Decimal(str(left)) == Decimal(str(right)), field
            else:
                assert left == right, field

    # Q050/Q052/Q056/Q058：REST 返回集合与MCP多页结果逐项一致；
    # 隐私和金额字段仅按MCP固定投影裁剪。
    rest_logistics = rest_data("/logistics", {"keyword": "MCP-LOGI"})
    mcp_logistics = mcp_pages("erp_logistics_list_logistics", {"keyword": "MCP-LOGI"})
    assert {row["id"] for row in mcp_logistics} == {row["id"] for row in rest_logistics}
    rest_logistics_by_id = {row["id"]: row for row in rest_logistics}
    for row in mcp_logistics:
        assert_fields_match(
            rest_logistics_by_id[row["id"]], row,
            ("id", "name", "company", "is_default", "is_enabled"),
        )
        assert not {"contact", "phone", "address"} & row.keys()

    rest_logistics_detail = rest_data("/logistics/1")
    mcp_logistics_detail = call(
        rt, "erp_logistics_get_logistics", {"logistics_id": 1},
    ).structuredContent["data"]
    assert_fields_match(
        rest_logistics_detail, mcp_logistics_detail,
        ("id", "name", "company", "is_default", "is_enabled"),
    )

    rest_templates = rest_data("/freight-templates", {"logistics_id": 1})
    mcp_templates = mcp_pages(
        "erp_logistics_list_freight_templates", {"logistics_id": 1},
    )
    assert {row["id"] for row in mcp_templates} == {row["id"] for row in rest_templates}
    rest_templates_by_id = {row["id"]: row for row in rest_templates}
    template_fields = (
        "id", "name", "logistics_id", "logistics_name", "charge_type",
        "first_count", "continue_count", "first_weight", "continue_weight",
        "deduct_weight",
    )
    for row in mcp_templates:
        assert_fields_match(rest_templates_by_id[row["id"]], row, template_fields)
        assert not {
            "base_fee", "continue_fee", "operation_fee", "consumable_fee",
            "ewaybill_fee", "tax_fee", "tax_point",
        } & row.keys()

    rest_template_detail = rest_data("/freight-templates/1")
    mcp_template_detail = call(
        rt, "erp_logistics_get_freight_template", {"template_id": 1},
    ).structuredContent["data"]
    assert_fields_match(rest_template_detail, mcp_template_detail, template_fields)
    rest_calculation = rest_data(
        "/freight-templates/1/calculate", {"count": 3},
    )
    mcp_calculation = call(rt, "erp_logistics_calculate_freight", {
        "template_id": 1, "count": 3,
    }).structuredContent["data"]
    assert_fields_match(
        rest_calculation, mcp_calculation,
        ("template_id", "template_name", "charge_type", "quantity_label",
         "quantity", "additional_units"),
    )
    assert not {"base_fee", "total_fee", "tax_amount"} & mcp_calculation.keys()

    rest_rules = rest_data("/area-rules", {"logistics_id": 1})
    mcp_rules = mcp_pages("erp_logistics_list_area_rules", {"logistics_id": 1})
    assert {row["id"] for row in mcp_rules} == {row["id"] for row in rest_rules}
    rest_rules_by_id = {row["id"]: row for row in rest_rules}
    rule_fields = (
        "id", "logistics_id", "logistics_name", "province", "city",
        "match_weight_min", "match_weight_max", "freight_template_id",
        "freight_template_name",
    )
    for row in mcp_rules:
        assert_fields_match(rest_rules_by_id[row["id"]], row, rule_fields)
    rest_rule_detail = rest_data("/area-rules/2")
    mcp_rule_detail = call(
        rt, "erp_logistics_get_area_rule", {"rule_id": 2},
    ).structuredContent["data"]
    assert_fields_match(rest_rule_detail, mcp_rule_detail, rule_fields)
    rest_match = rest_data("/area-rules/match", {
        "logistics_id": 1, "province": "浙江", "city": "杭州", "weight": "1.5",
    })
    mcp_match = call(rt, "erp_logistics_match_area_rule", {
        "logistics_id": 1, "province": "浙江", "city": "杭州", "weight": 1.5,
    }).structuredContent["data"]
    assert rest_match["matched"] is mcp_match["matched"] is True
    assert_fields_match(rest_match["area_rule"], mcp_match["area_rule"], rule_fields)
    assert_fields_match(
        rest_match["freight_template"], mcp_match["freight_template"], template_fields,
    )
    assert "base_fee" not in mcp_match["freight_template"]

    rest_print_templates = rest_data("/print-templates", {
        "logistics_id": 1, "template_type": "WAYBILL",
    })
    mcp_print_templates = mcp_pages("erp_logistics_list_print_templates", {
        "logistics_id": 1, "template_type": "WAYBILL",
    })
    assert {row["id"] for row in mcp_print_templates} == {
        row["id"] for row in rest_print_templates
    }
    rest_print_by_id = {row["id"]: row for row in rest_print_templates}
    print_fields = (
        "id", "name", "template_type", "logistics_id", "paper_width",
        "paper_height", "is_default",
    )
    for row in mcp_print_templates:
        assert_fields_match(rest_print_by_id[row["id"]], row, print_fields)
        assert "content" not in row
    rest_print_detail = rest_data("/print-templates/1")
    mcp_print_detail = call(
        rt, "erp_logistics_get_print_template", {"template_id": 1},
    ).structuredContent["data"]
    assert_fields_match(rest_print_detail, mcp_print_detail, print_fields)
    assert "content" not in mcp_print_detail

    with factory() as db:
        db.add(Role(id=4, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=4))
        db.commit()
    finance_token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:order", "module:finance"],
    )["token"]
    finance_rt = runtime(factory, finance_token, limits=MemoryLimits(rpm=100))
    finance_template_fields = (
        "base_fee", "continue_fee", "operation_fee", "consumable_fee",
        "ewaybill_fee", "tax_fee", "tax_point",
    )
    rest_finance_templates = rest_data("/freight-templates", {"logistics_id": 1})
    mcp_finance_templates = call(finance_rt, "erp_logistics_list_freight_templates", {
        "logistics_id": 1, "page_size": 10,
    }).structuredContent["data"]
    rest_finance_by_id = {row["id"]: row for row in rest_finance_templates}
    assert {row["id"] for row in mcp_finance_templates} == set(rest_finance_by_id)
    for row in mcp_finance_templates:
        assert_fields_match(rest_finance_by_id[row["id"]], row, finance_template_fields)

    finance_detail = call(finance_rt, "erp_logistics_get_freight_template", {
        "template_id": 2,
    }).structuredContent["data"]
    rest_finance_detail = rest_data("/freight-templates/2")
    assert_fields_match(rest_finance_detail, finance_detail, template_fields)
    assert_fields_match(rest_finance_detail, finance_detail, finance_template_fields)

    finance_calculation = call(finance_rt, "erp_logistics_calculate_freight", {
        "template_id": 1, "count": 3,
    }).structuredContent["data"]
    rest_finance_calculation = rest_data(
        "/freight-templates/1/calculate", {"count": 3},
    )
    assert_fields_match(
        rest_finance_calculation, finance_calculation,
        ("base_fee", "additional_fee", "freight_fee", "operation_fee",
         "consumable_fee", "ewaybill_fee", "tax_point", "percentage_tax",
         "fixed_tax_fee", "tax_amount", "total_fee"),
    )
    finance_match = call(finance_rt, "erp_logistics_match_area_rule", {
        "logistics_id": 1, "province": "浙江", "city": "杭州", "weight": 1.5,
    }).structuredContent["data"]
    rest_finance_match = rest_data("/area-rules/match", {
        "logistics_id": 1, "province": "浙江", "city": "杭州", "weight": "1.5",
    })
    assert_fields_match(
        rest_finance_match["freight_template"], finance_match["freight_template"],
        template_fields + finance_template_fields,
    )


def test_finance_payment_and_receipt_details_use_fixed_finance_projection(
    factory, monkeypatch,
):
    from decimal import Decimal
    from types import SimpleNamespace
    from sqlalchemy import delete
    from app.services import pay_receipt_service

    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()

    seen = {}
    monkeypatch.setattr(
        pay_receipt_service, "get_payment_or_404",
        lambda db, payment_id: (seen.update(payment_id=payment_id) or SimpleNamespace(id=payment_id)),
    )
    monkeypatch.setattr(
        pay_receipt_service, "get_receipt_or_404",
        lambda db, receipt_id: (seen.update(receipt_id=receipt_id) or SimpleNamespace(id=receipt_id)),
    )

    def payment_dict(db, row, *, with_approval, with_voucher):
        seen["payment_flags"] = (with_approval, with_voucher)
        return {
            "id": row.id, "payment_no": "PAY-MCP-1", "amount": Decimal("12.30"),
            "status": "APPROVED", "audit_status": "APPROVED",
            "supplier_name": "供应商甲", "account_no": "PRIVATE-BANK-ACCOUNT",
            "voucher": "PRIVATE-VOUCHER", "approval": "PRIVATE-APPROVAL",
            "created_by_name": "PRIVATE-STAFF",
            "supplier_id": 7, "stock_in_id": None, "stock_in_no": None,
            "invoice_id": None, "invoice_no": None, "income_id": None,
            "income_no": None, "prepay_id": None, "prepay_no": None,
            "payment_type": None, "pay_method": None, "has_warning": False,
            "created_at": "2026-10-13T10:00:00", "updated_at": "2026-10-13T10:00:00",
        }

    def receipt_dict(db, row, *, with_approval, with_voucher):
        seen["receipt_flags"] = (with_approval, with_voucher)
        return {
            "id": row.id, "receipt_no": "REC-MCP-1", "amount": Decimal("23.40"),
            "status": "APPROVED", "audit_status": "APPROVED",
            "customer_name": "客户甲", "customer_phone": "PRIVATE-PHONE",
            "voucher": "PRIVATE-VOUCHER", "approval": "PRIVATE-APPROVAL",
            "created_by_name": "PRIVATE-STAFF",
            "customer_id": 9, "expense_id": None, "expense_no": None,
            "invoice_id": None, "invoice_no": None, "order_id": None,
            "order_no": None, "sale_type": None, "settle_type": None,
            "has_warning": False, "created_at": "2026-10-13T10:00:00",
            "updated_at": "2026-10-13T10:00:00",
        }

    monkeypatch.setattr(pay_receipt_service, "payment_dict", payment_dict)
    monkeypatch.setattr(pay_receipt_service, "receipt_dict", receipt_dict)
    tools = [
        ACCESS_TOOL, "erp_finance_doc_get_payment", "erp_finance_doc_get_receipt",
    ]
    finance_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, finance_token)
    payment = call(rt, "erp_finance_doc_get_payment", {
        "payment_id": 31,
    }).structuredContent["data"]
    receipt = call(rt, "erp_finance_doc_get_receipt", {
        "receipt_id": 42,
    }).structuredContent["data"]
    assert payment["payment_no"] == "PAY-MCP-1"
    assert Decimal(payment["amount"]) == Decimal("12.30")
    assert receipt["receipt_no"] == "REC-MCP-1"
    assert Decimal(receipt["amount"]) == Decimal("23.40")
    assert not {"account_no", "voucher", "approval", "created_by_name"} & set(payment)
    assert not {"customer_phone", "voucher", "approval", "created_by_name"} & set(receipt)
    assert seen == {
        "payment_id": 31, "payment_flags": (False, False),
        "receipt_id": 42, "receipt_flags": (False, False),
    }
    assert call(rt, "erp_finance_doc_get_receipt", {
        "receipt_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        db.execute(delete(StaffRole).where(
            StaffRole.staff_id == 1, StaffRole.role_id == 3,
        ))
        db.commit()
    assert call(rt, "erp_finance_doc_get_payment", {
        "payment_id": 31,
    }).structuredContent["code"] == "FORBIDDEN"


def test_purchase_order_detail_and_items_recheck_parent_and_page_lines(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.party import Supplier
    from app.models.purchase import PurchaseOrder, PurchaseOrderItem

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.get(Staff, 2).company_id = 1
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add(Supplier(id=1, code="MCP-PO-SUP", name="采购订单供应商"))
        db.add_all([
            PurchaseOrder(
                id=1, order_no="MCP-PO-OWN", supplier_id=1, purchaser_id=1,
                created_by=1, total_qty=Decimal("3.000"),
                total_amount=Decimal("18.00"), description="PRIVATE-DESCRIPTION",
                attachments='["PRIVATE-ATTACHMENT"]',
            ),
            PurchaseOrder(
                id=2, order_no="MCP-PO-OTHER", supplier_id=1, purchaser_id=2,
                created_by=2, total_qty=Decimal("1.000"),
            ),
        ])
        db.add_all([
            Product(id=1, code="MCP-PO-P1", name="采购商品一"),
            Product(id=2, code="MCP-PO-P2", name="采购商品二"),
        ])
        db.add_all([
            ProductSku(
                id=1, product_id=1, spec_name="规格A", spec_code="MCP-PO-SKU-A",
                sale_price=Decimal("99.00"), cost_price=Decimal("20.00"),
            ),
            ProductSku(
                id=2, product_id=2, spec_name="规格B", spec_code="MCP-PO-SKU-B",
                sale_price=Decimal("88.00"), cost_price=Decimal("30.00"),
            ),
        ])
        db.add_all([
            PurchaseOrderItem(
                id=1, order_id=1, product_id=1, sku_id=1,
                purchase_price=Decimal("5.00"), qty=Decimal("1.000"),
                in_transit_qty=Decimal("1.000"), arrived_qty=Decimal("0.000"),
                amount=Decimal("5.00"),
            ),
            PurchaseOrderItem(
                id=2, order_id=1, product_id=2, sku_id=2,
                purchase_price=Decimal("13.00"), qty=Decimal("2.000"),
                in_transit_qty=Decimal("0.000"), arrived_qty=Decimal("1.000"),
                amount=Decimal("26.00"),
            ),
        ])
        db.commit()
        before = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(PurchaseOrderItem.id))),
        )

    tools = [
        ACCESS_TOOL, "erp_search_purchase_orders", "erp_get_purchase_order",
        "erp_list_purchase_order_items",
    ]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)

    from app.api.purchase_order import router as purchase_order_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(purchase_order_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    headers = {"Authorization": "Bearer " + create_access_token(1, "采购员", [])}
    with TestClient(rest_app) as rest_client:
        rest_search = rest_client.get("/purchase-orders", params={
            "keyword": "MCP-PO", "page": 1, "page_size": 100,
        }, headers=headers)
        assert rest_search.status_code == 200
        rest_search_data = rest_search.json()["data"]
        mcp_search = call(rt, "erp_search_purchase_orders", {
            "keyword": "MCP-PO", "page": 1, "page_size": 100,
        }).structuredContent
        assert mcp_search["page"]["total"] == rest_search_data["total"] == 1
        assert mcp_search["data"][0]["order_no"] == rest_search_data["items"][0]["order_no"]

        rest_detail = rest_client.get("/purchase-orders/1", headers=headers)
        assert rest_detail.status_code == 200
        rest_detail_data = rest_detail.json()["data"]
        mcp_detail = call(rt, "erp_get_purchase_order", {
            "order_id": 1,
        }).structuredContent["data"]
        for field in ("id", "order_no", "total_qty", "total_amount"):
            assert mcp_detail[field] == rest_detail_data[field], field

        mcp_items = call(rt, "erp_list_purchase_order_items", {
            "order_id": 1, "page_size": 100,
        }).structuredContent
        assert mcp_items["page"]["total"] == len(rest_detail_data["items"]) == 2
        for mcp_item, rest_item in zip(mcp_items["data"], rest_detail_data["items"]):
            for field in ("id", "product_id", "product_name", "sku_id", "spec_name",
                          "spec_code", "qty", "arrived_qty", "in_transit_qty",
                          "purchase_price", "amount"):
                if field in rest_item:
                    assert mcp_item[field] == rest_item[field], field

    detail = call(rt, "erp_get_purchase_order", {
        "order_id": 1,
    }).structuredContent["data"]
    assert detail["order_no"] == "MCP-PO-OWN"
    assert Decimal(detail["total_amount"]) == Decimal("18.00")
    assert not {"description", "attachments", "created_by", "items"} & set(detail)
    page = call(rt, "erp_list_purchase_order_items", {
        "order_id": 1, "page_size": 1,
    })
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    item = page.structuredContent["data"][0]
    assert item["product_name"] == "采购商品一"
    assert item["spec_code"] == "MCP-PO-SKU-A"
    assert Decimal(item["purchase_price"]) == Decimal("5.00")
    assert Decimal(item["difference_qty"]) == Decimal("1.000")
    assert not {"sale_price", "cost_price", "image"} & set(item)
    assert call(rt, "erp_get_purchase_order", {
        "order_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_list_purchase_order_items", {
        "order_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_get_purchase_order", {
        "order_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    with factory() as db:
        after = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(PurchaseOrderItem.id))),
        )
    assert before == after


def test_transfer_queries_are_scoped_sql_paged_and_redacted(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.transfer import router as transfer_router
    from app.core.data_scope import clear_scope_policy
    from app.models.org import Department
    from app.models.rbac import DataScope
    from app.models.stock import (
        Transfer, TransferIn, TransferInItem, TransferItem, TransferOut,
        TransferOutItem,
    )
    from app.models.warehouse import Location, Warehouse

    tool_names = [
        ACCESS_TOOL,
        "erp_transfer_list_transfers", "erp_transfer_get_transfer",
        "erp_transfer_list_transfer_items", "erp_transfer_list_transfer_outs",
        "erp_transfer_get_transfer_out", "erp_transfer_list_transfer_out_items",
        "erp_transfer_list_transfer_ins", "erp_transfer_get_transfer_in",
        "erp_transfer_list_transfer_in_items",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add_all([
            Department(id=1, company_id=1, name="调拨范围一部"),
            Department(id=2, company_id=1, name="调拨范围二部"),
            StaffRole(staff_id=1, role_id=3),
            Staff(id=3, company_id=1, department_id=2, name="跨部门调拨制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门调拨制单人"),
            Warehouse(id=1, code="MCP-TRANSFER-FROM", name="调出仓"),
            Warehouse(id=2, code="MCP-TRANSFER-TO", name="调入仓"),
            Location(id=1, warehouse_id=1, code="MCP-TRANSFER-LOC-1"),
            Location(id=2, warehouse_id=2, code="MCP-TRANSFER-LOC-2"),
            Product(id=1, code="MCP-TRANSFER-PRODUCT", name="调拨测试商品",
                    barcode="PRIVATE-TRANSFER-PRODUCT", cost_price=Decimal("25.00")),
            ProductSku(id=1, product_id=1, spec_code="MCP-TRANSFER-SKU",
                       spec_name="调拨规格", barcode="PRIVATE-TRANSFER-SKU",
                       cost_price=Decimal("15.00")),
        ])
        db.get(Staff, 1).department_id = 1
        db.add(DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1))
        db.add_all([
            Transfer(
                id=1, transfer_no="MCP-TRANSFER-OWN", from_warehouse_id=1,
                to_warehouse_id=2, status="OUTBOUND", audit_status="APPROVED",
                total_qty=Decimal("3.000"), created_by=1, auditor_id=1,
                remark="PRIVATE-TRANSFER-REMARK",
            ),
            Transfer(
                id=2, transfer_no="MCP-TRANSFER-OTHER", from_warehouse_id=1,
                to_warehouse_id=2, total_qty=Decimal("900.000"), created_by=3,
            ),
            Transfer(
                id=3, transfer_no="MCP-TRANSFER-DEPARTMENT", from_warehouse_id=1,
                to_warehouse_id=2, total_qty=Decimal("4.000"), created_by=4,
            ),
            TransferItem(
                id=1, transfer_id=1, product_id=1, sku_id=1,
                qty=Decimal("1.000"), barcode="PRIVATE-TRANSFER-ITEM-BARCODE",
            ),
            TransferItem(id=2, transfer_id=1, product_id=1, sku_id=1, qty=Decimal("2.000")),
            TransferItem(id=3, transfer_id=2, product_id=1, sku_id=1, qty=Decimal("900.000")),
            TransferItem(id=4, transfer_id=3, product_id=1, sku_id=1, qty=Decimal("4.000")),
            TransferOut(
                id=1, transfer_out_no="MCP-TRANSFER-OUT-OWN", transfer_id=1,
                warehouse_id=1, status="APPROVED", audit_status="APPROVED",
                created_by=1, remark="PRIVATE-TRANSFER-OUT-REMARK",
            ),
            TransferOut(
                id=2, transfer_out_no="MCP-TRANSFER-OUT-OTHER", transfer_id=2,
                warehouse_id=1, status="APPROVED", audit_status="APPROVED", created_by=3,
            ),
            TransferOut(
                id=3, transfer_out_no="MCP-TRANSFER-OUT-DEPARTMENT", transfer_id=3,
                warehouse_id=1, status="PENDING", audit_status="PENDING", created_by=4,
            ),
            TransferOutItem(
                id=1, transfer_out_id=1, product_id=1, sku_id=1, location_id=1,
                qty=Decimal("1.000"),
            ),
            TransferOutItem(
                id=2, transfer_out_id=1, product_id=1, sku_id=1, location_id=1,
                qty=Decimal("2.000"),
            ),
            TransferOutItem(
                id=3, transfer_out_id=2, product_id=1, sku_id=1, location_id=1,
                qty=Decimal("900.000"),
            ),
            TransferOutItem(
                id=4, transfer_out_id=3, product_id=1, sku_id=1, location_id=1,
                qty=Decimal("4.000"),
            ),
            TransferIn(
                id=1, transfer_in_no="MCP-TRANSFER-IN-OWN", transfer_id=1,
                warehouse_id=2, status="APPROVED", audit_status="APPROVED",
                created_by=1, remark="PRIVATE-TRANSFER-IN-REMARK",
            ),
            TransferIn(
                id=2, transfer_in_no="MCP-TRANSFER-IN-OTHER", transfer_id=2,
                warehouse_id=2, status="APPROVED", audit_status="APPROVED", created_by=3,
            ),
            TransferIn(
                id=3, transfer_in_no="MCP-TRANSFER-IN-DEPARTMENT", transfer_id=3,
                warehouse_id=2, status="PENDING", audit_status="PENDING", created_by=4,
            ),
            TransferInItem(
                id=1, transfer_in_id=1, product_id=1, sku_id=1, location_id=2,
                qty=Decimal("1.000"),
            ),
            TransferInItem(
                id=2, transfer_in_id=1, product_id=1, sku_id=1, location_id=2,
                qty=Decimal("2.000"),
            ),
            TransferInItem(
                id=3, transfer_in_id=2, product_id=1, sku_id=1, location_id=2,
                qty=Decimal("900.000"),
            ),
            TransferInItem(
                id=4, transfer_in_id=3, product_id=1, sku_id=1, location_id=2,
                qty=Decimal("4.000"),
            ),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    # 多公司环境不再触发全局拒绝；空列表正常返回，具体数据仍按公司过滤。
    transfer_list = call(rt, "erp_transfer_list_transfers", {})
    assert not transfer_list.isError
    assert transfer_list.structuredContent["page"]["total"] == 1
    assert transfer_list.structuredContent["data"][0]["transfer_no"] == "MCP-TRANSFER-OWN"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()
        before = {
            model.__tablename__: db.scalar(select(func.count(model.id)))
            for model in (Transfer, TransferItem, TransferOut, TransferOutItem,
                          TransferIn, TransferInItem)
        }

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(transfer_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "仓库", [])}
    with TestClient(rest_app) as rest_client:
        rest_transfer_list_response = rest_client.get("/transfers", params={
            "from_warehouse_id": 1, "to_warehouse_id": 2,
            "status": "OUTBOUND", "audit_status": "APPROVED",
            "keyword": "MCP-TRANSFER-OWN", "page": 1, "page_size": 1,
        }, headers=rest_headers)
        assert rest_transfer_list_response.status_code == 200
        rest_transfer_list = rest_transfer_list_response.json()["data"]
        mcp_transfer_list = call(rt, "erp_transfer_list_transfers", {
            "from_warehouse_id": 1, "to_warehouse_id": 2,
            "status": "OUTBOUND", "audit_status": "APPROVED",
            "keyword": "MCP-TRANSFER-OWN", "page": 1, "page_size": 1,
        }).structuredContent
        assert mcp_transfer_list["page"]["total"] == rest_transfer_list["total"] == 1
        for field in ("id", "transfer_no", "from_warehouse_id", "from_warehouse_name",
                      "to_warehouse_id", "to_warehouse_name", "status", "audit_status"):
            assert mcp_transfer_list["data"][0][field] == rest_transfer_list["items"][0][field]
        assert Decimal(mcp_transfer_list["data"][0]["total_qty"]) == Decimal(
            str(rest_transfer_list["items"][0]["total_qty"])
        )

        rest_transfer_response = rest_client.get("/transfers/1", headers=rest_headers)
        assert rest_transfer_response.status_code == 200
        rest_transfer = rest_transfer_response.json()["data"]
        mcp_transfer = call(rt, "erp_transfer_get_transfer", {
            "transfer_id": 1,
        }).structuredContent["data"]
        for field in ("id", "transfer_no", "from_warehouse_id", "from_warehouse_name",
                      "to_warehouse_id", "to_warehouse_name", "status", "audit_status"):
            assert mcp_transfer[field] == rest_transfer[field]
        assert mcp_transfer["item_count"] == len(rest_transfer["items"]) == 2
        rest_transfer_items = {item["id"]: item for item in rest_transfer["items"]}
        for page_number in (1, 2):
            mcp_items = call(rt, "erp_transfer_list_transfer_items", {
                "transfer_id": 1, "page": page_number, "page_size": 1,
            }).structuredContent
            assert mcp_items["page"]["total"] == len(rest_transfer_items)
            assert len(mcp_items["data"]) == 1
            mcp_item = mcp_items["data"][0]
            rest_item = rest_transfer_items[mcp_item["id"]]
            for field in ("id", "product_id", "product_name", "product_code", "sku_id",
                          "spec_name", "spec_code"):
                assert mcp_item[field] == rest_item[field]
            assert Decimal(mcp_item["qty"]) == Decimal(str(rest_item["qty"]))

        for direction, list_path, detail_path, list_tool, detail_tool, items_tool, id_field, doc_no, warehouse_id in (
            ("out", "/transfer-outs", "/transfer-outs/1",
             "erp_transfer_list_transfer_outs", "erp_transfer_get_transfer_out",
             "erp_transfer_list_transfer_out_items", "transfer_out_id", "transfer_out_no", 1),
            ("in", "/transfer-ins", "/transfer-ins/1",
             "erp_transfer_list_transfer_ins", "erp_transfer_get_transfer_in",
             "erp_transfer_list_transfer_in_items", "transfer_in_id", "transfer_in_no", 2),
        ):
            params = {
                "transfer_id": 1, "warehouse_id": warehouse_id,
                "status": "APPROVED", "audit_status": "APPROVED",
                "page": 1, "page_size": 1,
            }
            rest_child_list_response = rest_client.get(
                list_path, params=params, headers=rest_headers,
            )
            assert rest_child_list_response.status_code == 200, direction
            rest_child_list = rest_child_list_response.json()["data"]
            mcp_child_list = call(rt, list_tool, params).structuredContent
            assert mcp_child_list["page"]["total"] == rest_child_list["total"] == 1
            rest_child_summary = rest_child_list["items"][0]
            mcp_child_summary = mcp_child_list["data"][0]
            for field in ("id", "transfer_id", "warehouse_id", "warehouse_name",
                          "status", "audit_status"):
                assert mcp_child_summary[field] == rest_child_summary[field], direction
            assert mcp_child_summary[doc_no] == rest_child_summary[doc_no]
            assert Decimal(mcp_child_summary["total_qty"]) == Decimal(
                str(rest_child_summary["total_qty"])
            )

            rest_child_response = rest_client.get(detail_path, headers=rest_headers)
            assert rest_child_response.status_code == 200, direction
            rest_child = rest_child_response.json()["data"]
            mcp_child = call(rt, detail_tool, {id_field: 1}).structuredContent["data"]
            for field in ("id", doc_no, "transfer_id", "warehouse_id", "warehouse_name",
                          "status", "audit_status"):
                assert mcp_child[field] == rest_child[field], direction
            assert mcp_child["item_count"] == len(rest_child["items"]) == 2
            rest_child_items = {item["id"]: item for item in rest_child["items"]}
            for page_number in (1, 2):
                mcp_child_items = call(rt, items_tool, {
                    id_field: 1, "page": page_number, "page_size": 1,
                }).structuredContent
                assert mcp_child_items["page"]["total"] == len(rest_child_items), direction
                assert len(mcp_child_items["data"]) == 1, direction
                mcp_child_item = mcp_child_items["data"][0]
                rest_child_item = rest_child_items[mcp_child_item["id"]]
                for field in ("id", "product_id", "product_name", "product_code", "sku_id",
                              "spec_name", "spec_code", "location_id", "location_code"):
                    assert mcp_child_item[field] == rest_child_item[field], direction
                assert Decimal(mcp_child_item["qty"]) == Decimal(
                    str(rest_child_item["qty"])
                ), direction

    transfer_page = call(rt, "erp_transfer_list_transfers", {"page_size": 1}).structuredContent
    assert transfer_page["page"] == {
        "page": 1, "page_size": 1, "total": 1, "has_more": False,
    }
    transfer = transfer_page["data"][0]
    assert transfer["transfer_no"] == "MCP-TRANSFER-OWN"
    assert transfer["from_warehouse_name"] == "调出仓"
    assert transfer["to_warehouse_name"] == "调入仓"
    assert not {"remark", "created_by", "auditor_id", "audit_time", "items"} & set(transfer)
    filtered_transfer = call(rt, "erp_transfer_list_transfers", {
        "from_warehouse_id": 1, "to_warehouse_id": 2,
        "status": "OUTBOUND", "audit_status": "APPROVED",
        "keyword": "MCP-TRANSFER-OWN",
    }).structuredContent
    assert filtered_transfer["page"]["total"] == 1
    assert filtered_transfer["data"][0]["transfer_no"] == "MCP-TRANSFER-OWN"
    transfer_detail = call(rt, "erp_transfer_get_transfer", {
        "transfer_id": 1,
    }).structuredContent["data"]
    assert transfer_detail["item_count"] == 2
    assert "PRIVATE-TRANSFER-REMARK" not in json.dumps(transfer_detail, ensure_ascii=False)
    assert call(rt, "erp_transfer_get_transfer", {
        "transfer_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"
    transfer_items = call(rt, "erp_transfer_list_transfer_items", {
        "transfer_id": 1, "page_size": 1,
    }).structuredContent
    assert transfer_items["page"]["total"] == 2 and transfer_items["page"]["has_more"]
    assert transfer_items["data"][0]["product_name"] == "调拨测试商品"
    assert transfer_items["data"][0]["spec_code"] == "MCP-TRANSFER-SKU"
    assert "barcode" not in transfer_items["data"][0]
    assert call(rt, "erp_transfer_list_transfer_items", {
        "transfer_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"

    for direction, list_tool, get_tool, item_tool, item_key, doc_no, warehouse_id in (
        ("out", "erp_transfer_list_transfer_outs", "erp_transfer_get_transfer_out",
         "erp_transfer_list_transfer_out_items", "transfer_out_id", "transfer_out_no", 1),
        ("in", "erp_transfer_list_transfer_ins", "erp_transfer_get_transfer_in",
         "erp_transfer_list_transfer_in_items", "transfer_in_id", "transfer_in_no", 2),
    ):
        child_page = call(rt, list_tool, {"page_size": 1}).structuredContent
        assert child_page["page"] == {
            "page": 1, "page_size": 1, "total": 1, "has_more": False,
        }
        child = child_page["data"][0]
        assert child[doc_no].endswith("-OWN")
        assert child["item_count"] == 2 and child["total_qty"] == "3.000"
        assert not {"remark", "created_by", "auditor_id", "items"} & set(child)
        filtered_children = call(rt, list_tool, {
            "transfer_id": 1, "warehouse_id": warehouse_id,
            "status": "APPROVED", "audit_status": "APPROVED",
        }).structuredContent
        assert filtered_children["page"]["total"] == 1
        assert filtered_children["data"][0][doc_no].endswith("-OWN")
        child_id = 1
        child_detail = call(rt, get_tool, {item_key: child_id}).structuredContent["data"]
        assert child_detail[doc_no].endswith("-OWN")
        assert call(rt, get_tool, {item_key: 2}).structuredContent["code"] == "FORBIDDEN"
        child_items = call(rt, item_tool, {
            item_key: child_id, "page_size": 1,
        }).structuredContent
        assert child_items["page"]["total"] == 2 and child_items["page"]["has_more"]
        assert child_items["data"][0]["location_code"].startswith("MCP-TRANSFER-LOC-")
        assert call(rt, item_tool, {
            item_key: 2,
        }).structuredContent["code"] == "FORBIDDEN"

    expected_ids_by_scope = {
        "SELF": {1},
        "DEPARTMENT": {1, 3},
        "COMPANY": {1, 2, 3},
    }
    list_pairs = (
        ("erp_transfer_list_transfers", "/transfers"),
        ("erp_transfer_list_transfer_outs", "/transfer-outs"),
        ("erp_transfer_list_transfer_ins", "/transfer-ins"),
    )
    detail_specs = (
        ("erp_transfer_get_transfer", "/transfers", "transfer_id",
         "erp_transfer_list_transfer_items", "transfer_id"),
        ("erp_transfer_get_transfer_out", "/transfer-outs", "transfer_out_id",
         "erp_transfer_list_transfer_out_items", "transfer_out_id"),
        ("erp_transfer_get_transfer_in", "/transfer-ins", "transfer_in_id",
         "erp_transfer_list_transfer_in_items", "transfer_in_id"),
    )
    with TestClient(rest_app) as scoped_rest_client:
        for scope, expected_ids in expected_ids_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            for tool_name, rest_path in list_pairs:
                mcp_list = call(rt, tool_name, {"page_size": 20}).structuredContent
                rest_list = scoped_rest_client.get(
                    rest_path, params={"page": 1, "page_size": 20},
                    headers=rest_headers,
                )
                assert rest_list.status_code == 200, (scope, rest_path)
                rest_data = rest_list.json()["data"]
                assert {row["id"] for row in mcp_list["data"]} == {
                    row["id"] for row in rest_data["items"]
                } == expected_ids, (scope, rest_path)
                assert mcp_list["page"]["total"] == rest_data["total"] == len(expected_ids)

            for document_id in (1, 2, 3):
                should_be_visible = (
                    document_id == 1
                    or scope == "COMPANY"
                    or (scope == "DEPARTMENT" and document_id == 3)
                )
                for detail_tool, rest_path, id_field, items_tool, items_id_field in detail_specs:
                    mcp_detail = call(rt, detail_tool, {id_field: document_id})
                    rest_detail = scoped_rest_client.get(
                        f"{rest_path}/{document_id}", headers=rest_headers,
                    )
                    mcp_items = call(rt, items_tool, {
                        items_id_field: document_id, "page_size": 20,
                    })
                    if should_be_visible:
                        assert not mcp_detail.isError, (scope, detail_tool, document_id)
                        assert rest_detail.status_code == 200, (scope, rest_path, document_id)
                        assert not mcp_items.isError, (scope, items_tool, document_id)
                        rest_item_ids = {
                            item["id"] for item in rest_detail.json()["data"]["items"]
                        }
                        assert {
                            item["id"] for item in mcp_items.structuredContent["data"]
                        } == rest_item_ids, (scope, items_tool, document_id)
                    else:
                        assert mcp_detail.structuredContent["code"] == "FORBIDDEN", (
                            scope, detail_tool, document_id,
                        )
                        assert rest_detail.status_code == 404, (
                            scope, rest_path, document_id,
                        )
                        assert mcp_items.structuredContent["code"] == "FORBIDDEN", (
                            scope, items_tool, document_id,
                        )

    with factory() as db:
        after = {
            model.__tablename__: db.scalar(select(func.count(model.id)))
            for model in (Transfer, TransferItem, TransferOut, TransferOutItem,
                          TransferIn, TransferInItem)
        }
    assert before == after


def test_voucher_queries_are_owner_scoped_sql_paged_and_redacted(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import func
    from app.api.voucher import router as voucher_router
    from app.core.data_scope import clear_scope_policy
    from app.models.finance import AccountSubject, Voucher, VoucherItem
    from app.models.org import Department
    from app.models.rbac import DataScope

    tool_names = [
        ACCESS_TOOL, "erp_voucher_list_vouchers", "erp_voucher_get_voucher",
        "erp_voucher_list_voucher_items",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add_all([
            Department(id=1, company_id=1, name="凭证范围一部"),
            Department(id=2, company_id=1, name="凭证范围二部"),
            StaffRole(staff_id=1, role_id=3),
            Staff(id=3, company_id=1, department_id=2, name="跨部门凭证制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门凭证制单人"),
            AccountSubject(id=1, code="MCP-VOUCHER-SUBJECT", name="测试科目",
                           category="ASSET", is_enabled=True),
            Voucher(
                id=1, voucher_word="记", voucher_no="MCP-VOUCHER-OWN",
                business_date=datetime(2026, 10, 12, 10),
                record_date=datetime(2026, 10, 12, 11),
                reference="PRIVATE-VOUCHER-REFERENCE", total_debit=Decimal("12.30"),
                total_credit=Decimal("12.30"), status="APPROVED", created_by=1,
                auditor_id=1, posted_by=1, cashier_id=1, general_office_id=1,
            ),
            Voucher(
                id=2, voucher_word="记", voucher_no="MCP-VOUCHER-OTHER",
                business_date=datetime(2026, 10, 12, 12),
                total_debit=Decimal("90.00"), total_credit=Decimal("90.00"),
                status="DRAFT", created_by=3,
            ),
            Voucher(
                id=3, voucher_word="记", voucher_no="MCP-VOUCHER-DEPARTMENT",
                business_date=datetime(2026, 10, 12, 13),
                record_date=datetime(2026, 10, 12, 14),
                total_debit=Decimal("40.00"), total_credit=Decimal("40.00"),
                status="DRAFT", created_by=4,
            ),
            VoucherItem(
                id=1, voucher_id=1, subject_id=1,
                summary="PRIVATE-VOUCHER-ITEM-SUMMARY", debit=Decimal("12.30"),
                credit=Decimal("0.00"),
            ),
            VoucherItem(
                id=2, voucher_id=1, subject_id=1,
                summary="PRIVATE-VOUCHER-ITEM-SUMMARY-2", debit=Decimal("0.00"),
                credit=Decimal("12.30"),
            ),
            VoucherItem(
                id=3, voucher_id=2, subject_id=1,
                summary="OTHER-PRIVATE-SUMMARY", debit=Decimal("90.00"),
                credit=Decimal("90.00"),
            ),
            VoucherItem(
                id=4, voucher_id=3, subject_id=1,
                summary="DEPARTMENT-PRIVATE-SUMMARY", debit=Decimal("40.00"),
                credit=Decimal("40.00"),
            ),
        ])
        db.get(Staff, 1).department_id = 1
        db.add(DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1))
        db.commit()
        before = (
            db.scalar(select(func.count(Voucher.id))),
            db.scalar(select(func.count(VoucherItem.id))),
        )

    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(voucher_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "财务", [])}
    with TestClient(rest_app) as rest_client:
        rest_list_response = rest_client.get("/vouchers", params={
            "keyword": "MCP-VOUCHER-OWN", "status": "APPROVED",
            "page": 1, "page_size": 1,
        }, headers=rest_headers)
        assert rest_list_response.status_code == 200
        rest_list = rest_list_response.json()["data"]
        mcp_list = call(rt, "erp_voucher_list_vouchers", {
            "keyword": "MCP-VOUCHER-OWN", "status": "APPROVED",
            "page": 1, "page_size": 1,
        }).structuredContent
        assert mcp_list["page"]["total"] == rest_list["total"] == 1
        for field in ("id", "voucher_word", "voucher_no", "status", "item_count"):
            if field == "item_count":
                assert mcp_list["data"][0][field] == len(rest_list["items"][0]["items"])
            else:
                assert mcp_list["data"][0][field] == rest_list["items"][0][field]
        for field in ("total_debit", "total_credit"):
            assert Decimal(mcp_list["data"][0][field]) == Decimal(
                str(rest_list["items"][0][field])
            )

        exact_date_params = {
            "business_date_from": "2026-10-12T10:00:00+08:00",
            "business_date_to": "2026-10-12T10:00:00+08:00",
            "status": "APPROVED", "page": 1, "page_size": 10,
        }
        rest_exact_date = rest_client.get(
            "/vouchers", params=exact_date_params, headers=rest_headers,
        )
        assert rest_exact_date.status_code == 200
        mcp_exact_date = call(
            rt, "erp_voucher_list_vouchers", exact_date_params,
        ).structuredContent
        assert mcp_exact_date["page"]["total"] == rest_exact_date.json()["data"]["total"] == 1
        assert mcp_exact_date["data"][0]["id"] == rest_exact_date.json()["data"]["items"][0]["id"]

        reversed_date_params = {
            "business_date_from": "2026-10-13T00:00:00+08:00",
            "business_date_to": "2026-10-12T00:00:00+08:00",
        }
        rest_reversed_date = rest_client.get(
            "/vouchers", params=reversed_date_params, headers=rest_headers,
        )
        mcp_reversed_date = call(
            rt, "erp_voucher_list_vouchers", reversed_date_params,
        ).structuredContent
        assert rest_reversed_date.status_code == 400
        assert mcp_reversed_date["code"] == "INVALID_ARGUMENT"

        rest_detail_response = rest_client.get("/vouchers/1", headers=rest_headers)
        assert rest_detail_response.status_code == 200
        rest_detail = rest_detail_response.json()["data"]
        mcp_detail = call(rt, "erp_voucher_get_voucher", {
            "voucher_id": 1,
        }).structuredContent["data"]
        for field in ("id", "voucher_word", "voucher_no", "status"):
            assert mcp_detail[field] == rest_detail[field]
        assert mcp_detail["item_count"] == len(rest_detail["items"]) == 2

        rest_item_by_id = {item["id"]: item for item in rest_detail["items"]}
        for page_number in (1, 2):
            mcp_items = call(rt, "erp_voucher_list_voucher_items", {
                "voucher_id": 1, "page": page_number, "page_size": 1,
            }).structuredContent
            assert mcp_items["page"]["total"] == len(rest_detail["items"])
            assert len(mcp_items["data"]) == 1
            mcp_item = mcp_items["data"][0]
            rest_item = rest_item_by_id[mcp_item["id"]]
            for field in ("id", "subject_id", "subject_code", "subject_name"):
                assert mcp_item[field] == rest_item[field]
            for field in ("debit", "credit"):
                assert Decimal(mcp_item[field]) == Decimal(str(rest_item[field]))

    page = call(rt, "erp_voucher_list_vouchers", {"page_size": 1}).structuredContent
    assert page["page"] == {"page": 1, "page_size": 1, "total": 1, "has_more": False}
    summary = page["data"][0]
    assert summary["voucher_no"] == "MCP-VOUCHER-OWN"
    assert summary["item_count"] == 2
    assert Decimal(summary["total_debit"]) == Decimal("12.30")
    assert not {
        "reference", "created_by", "auditor_id", "auditor_name", "posted_by",
        "cashier_id", "general_office_id", "items",
    } & set(summary)
    assert call(rt, "erp_voucher_list_vouchers", {
        "keyword": "PRIVATE-VOUCHER-REFERENCE",
    }).structuredContent["page"]["total"] == 0
    assert call(rt, "erp_voucher_list_vouchers", {
        "business_date_from": "2026-10-12T10:00:00+08:00",
        "business_date_to": "2026-10-12T10:00:00+08:00",
        "status": "APPROVED",
    }).structuredContent["page"]["total"] == 1
    assert call(rt, "erp_voucher_list_vouchers", {
        "business_date_from": "2026-10-13T00:00:00+08:00",
        "business_date_to": "2026-10-12T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    detail = call(rt, "erp_voucher_get_voucher", {
        "voucher_id": 1,
    }).structuredContent["data"]
    assert detail["item_count"] == 2
    assert "reference" not in detail and "PRIVATE-VOUCHER-REFERENCE" not in json.dumps(detail)
    assert call(rt, "erp_voucher_get_voucher", {
        "voucher_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"
    items = call(rt, "erp_voucher_list_voucher_items", {
        "voucher_id": 1, "page_size": 1,
    }).structuredContent
    assert items["page"]["total"] == 2 and items["page"]["has_more"]
    item = items["data"][0]
    assert item["subject_name"] == "测试科目"
    assert "debit" in item and "credit" in item
    assert "summary" not in item and "PRIVATE-VOUCHER-ITEM-SUMMARY" not in json.dumps(item)
    assert call(rt, "erp_voucher_list_voucher_items", {
        "voucher_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"

    warehouse_token = issue(
        factory, staff_id=2, tool_scopes=tool_names,
        permission_scopes=["module:stock"],
    )["token"]
    assert call(
        runtime(factory, warehouse_token), "erp_voucher_list_vouchers", {},
    ).structuredContent["code"] == "FORBIDDEN"

    expected_ids_by_scope = {
        "SELF": {1},
        "DEPARTMENT": {1, 3},
        "COMPANY": {1, 2, 3},
    }
    with TestClient(rest_app) as scoped_rest_client:
        for scope, expected_ids in expected_ids_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            mcp_list = call(rt, "erp_voucher_list_vouchers", {
                "page_size": 20,
            }).structuredContent
            rest_list = scoped_rest_client.get(
                "/vouchers", params={"page": 1, "page_size": 20},
                headers=rest_headers,
            )
            assert rest_list.status_code == 200
            rest_list_data = rest_list.json()["data"]
            assert {row["id"] for row in mcp_list["data"]} == {
                row["id"] for row in rest_list_data["items"]
            } == expected_ids
            assert mcp_list["page"]["total"] == rest_list_data["total"] == len(expected_ids)

            for voucher_id in (1, 2, 3):
                should_be_visible = (
                    voucher_id == 1
                    or scope == "COMPANY"
                    or (scope == "DEPARTMENT" and voucher_id == 3)
                )
                mcp_detail = call(rt, "erp_voucher_get_voucher", {
                    "voucher_id": voucher_id,
                })
                rest_detail = scoped_rest_client.get(
                    f"/vouchers/{voucher_id}", headers=rest_headers,
                )
                mcp_items = call(rt, "erp_voucher_list_voucher_items", {
                    "voucher_id": voucher_id, "page_size": 20,
                })
                if should_be_visible:
                    assert not mcp_detail.isError, (scope, voucher_id)
                    assert rest_detail.status_code == 200, (scope, voucher_id)
                    assert not mcp_items.isError, (scope, voucher_id)
                    rest_item_ids = {
                        item["id"] for item in rest_detail.json()["data"]["items"]
                    }
                    assert {
                        item["id"] for item in mcp_items.structuredContent["data"]
                    } == rest_item_ids, (scope, voucher_id)
                else:
                    assert mcp_detail.structuredContent["code"] == "FORBIDDEN", (
                        scope, voucher_id,
                    )
                    assert rest_detail.status_code == 404, (scope, voucher_id)
                    assert mcp_items.structuredContent["code"] == "FORBIDDEN", (
                        scope, voucher_id,
                    )

    with factory() as db:
        after = (
            db.scalar(select(func.count(Voucher.id))),
            db.scalar(select(func.count(VoucherItem.id))),
        )
    assert before == after


def test_location_capacity_query_is_single_company_and_read_only(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.warehouse import Location, StockLocation, Warehouse

    tool_names = [ACCESS_TOOL, "erp_warehouse_get_location_capacity"]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=4, company_id=1, code="QUALITY", name="质检"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            StaffRole(staff_id=3, role_id=4),
            Staff(id=3, company_id=1, name="容量范围测试员工"),
            Warehouse(id=1, code="MCP-CAP-WH", name="容量测试仓"),
            Location(id=1, warehouse_id=1, code="MCP-CAP-LOC", capacity=5),
            Product(id=1, code="MCP-CAP-P", name="容量商品"),
            ProductSku(id=1, product_id=1, spec_code="MCP-CAP-SKU"),
            StockLocation(
                id=1, location_id=1, product_sku_id=1,
                qty=Decimal("2.000"), status="NORMAL",
            ),
        ])
        db.commit()

    warehouse_token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:stock"],
    )["token"]
    warehouse_rt = runtime(factory, warehouse_token, limits=MemoryLimits(rpm=50))
    assert call(
        warehouse_rt, "erp_warehouse_get_location_capacity", {"location_id": 1},
    ).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()
        before = (
            db.scalar(select(func.count(Location.id))),
            db.scalar(select(func.count(StockLocation.id))),
        )

    capacity = call(
        warehouse_rt, "erp_warehouse_get_location_capacity", {"location_id": 1},
    ).structuredContent["data"]
    assert capacity == {
        "location_id": 1, "capacity": 5, "used_qty": "2.000",
        "remaining_qty": "3.000", "unlimited": False,
    }
    assert call(
        warehouse_rt, "erp_warehouse_get_location_capacity", {"location_id": 999},
    ).structuredContent["code"] == "FORBIDDEN"
    quality_token = issue(
        factory, staff_id=3, tool_scopes=tool_names,
        permission_scopes=["module:stock"],
    )["token"]
    assert call(
        runtime(factory, quality_token),
        "erp_warehouse_get_location_capacity", {"location_id": 1},
    ).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        after = (
            db.scalar(select(func.count(Location.id))),
            db.scalar(select(func.count(StockLocation.id))),
        )
    assert before == after


def test_wave_queries_are_single_company_paged_and_redacted(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.wave import router as wave_router
    from app.core.data_scope import clear_scope_policy
    from app.models.order import LoadNo, Order, Wave, WaveOrder, WaveStrategy
    from app.models.platform import Logistics, Platform, Shop
    from app.models.warehouse import Warehouse

    tool_names = [
        ACCESS_TOOL, "erp_wave_list_wave_strategies", "erp_wave_get_wave_strategy",
        "erp_wave_get_wave_strategy_pending_count", "erp_wave_list_waves",
        "erp_wave_get_wave", "erp_wave_list_wave_orders", "erp_wave_list_load_numbers",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            Platform(id=1, name="MCP平台"),
            Shop(
                id=1, platform_id=1, name="波次店铺", login_account="PRIVATE-SHOP-LOGIN",
                url="PRIVATE-SHOP-URL", default_address="PRIVATE-SHOP-ADDRESS",
            ),
            Warehouse(id=1, code="MCP-WAVE-WH", name="波次仓"),
            Logistics(id=1, name="波次物流", phone="PRIVATE-LOGISTICS-PHONE"),
            WaveStrategy(
                id=1, name="MCP波次策略A", warehouse_id=1, logistics_id=1,
                shop_ids="1", wave_order_count=20, load_no_type="LOGISTICS",
                sort_type="LOCATION", is_enabled=True,
                description="PRIVATE-WAVE-STRATEGY-DESCRIPTION",
            ),
            WaveStrategy(
                id=2, name="MCP波次策略B", warehouse_id=1, logistics_id=1,
                shop_ids="not-an-id", wave_order_count=10, load_no_type="MANUAL",
                sort_type="ORDER_TIME", is_enabled=False,
            ),
            Wave(
                id=1, wave_no="MCP-WAVE-OWN", strategy_id=1, name="波次一",
                express_type="STD", total_order_count=2, created_by=1,
            ),
            Wave(
                id=2, wave_no="MCP-WAVE-SECOND", strategy_id=2, name="波次二",
                express_type="STD", total_order_count=0, created_by=1,
            ),
            Order(
                id=1, order_no="MCP-WAVE-ORDER-1", shop_id=1, warehouse_id=1,
                logistics_id=1, receiver_name="PRIVATE-RECEIVER", receiver_phone="13800000000",
                receiver_address="PRIVATE-ORDER-ADDRESS", status="PENDING",
                audit_status="APPROVED", pay_status="PAID", paid_amount=Decimal("99.00"),
                order_time=datetime(2026, 10, 12, 8), remark="PRIVATE-ORDER-REMARK",
            ),
            Order(
                id=2, order_no="MCP-WAVE-ORDER-2", shop_id=1, warehouse_id=1,
                logistics_id=1, receiver_name="PRIVATE-RECEIVER-2", receiver_phone="13900000000",
                receiver_address="PRIVATE-ORDER-ADDRESS-2", status="PENDING",
                audit_status="APPROVED", pay_status="PAID", paid_amount=Decimal("199.00"),
                order_time=datetime(2026, 10, 12, 9), remark="PRIVATE-ORDER-REMARK-2",
            ),
            Order(
                id=3, order_no="MCP-WAVE-ORDER-PENDING", shop_id=1, warehouse_id=1,
                logistics_id=1, status="PENDING", audit_status="APPROVED",
                order_time=datetime(2026, 10, 12, 10),
            ),
            WaveOrder(id=1, wave_id=1, order_id=1, load_no="MCP-LOAD-A", sequence=1),
            WaveOrder(id=2, wave_id=1, order_id=2, load_no="MCP-LOAD-B", sequence=2),
            LoadNo(id=1, wave_id=1, load_no="MCP-LOAD-A", express_type="STD"),
            LoadNo(id=2, wave_id=1, load_no="MCP-LOAD-B", express_type="STD"),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    assert call(rt, "erp_wave_list_waves", {}).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()
        before = {
            model.__tablename__: db.scalar(select(func.count(model.id)))
            for model in (WaveStrategy, Wave, WaveOrder, LoadNo)
        }

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(wave_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "采购员", [])}
    with TestClient(rest_app) as rest_client:
        strategy_params = {
            "keyword": "策略A", "is_enabled": True, "page": 1, "page_size": 1,
        }
        rest_strategies_response = rest_client.get(
            "/wave-strategies", params=strategy_params, headers=rest_headers,
        )
        assert rest_strategies_response.status_code == 200
        rest_strategies = rest_strategies_response.json()["data"]
        mcp_strategies = call(
            rt, "erp_wave_list_wave_strategies", strategy_params,
        ).structuredContent
        assert mcp_strategies["page"]["total"] == rest_strategies["total"] == 1
        for field in ("id", "name", "warehouse_id", "warehouse_name", "is_enabled"):
            assert mcp_strategies["data"][0][field] == rest_strategies["items"][0][field]

        rest_strategy_response = rest_client.get(
            "/wave-strategies/1", headers=rest_headers,
        )
        assert rest_strategy_response.status_code == 200
        rest_strategy = rest_strategy_response.json()["data"]
        mcp_strategy = call(rt, "erp_wave_get_wave_strategy", {
            "strategy_id": 1,
        }).structuredContent["data"]
        for field in ("id", "name", "logistics_id", "logistics_name", "warehouse_id",
                      "warehouse_name", "wave_order_count", "load_no_type", "sort_type",
                      "is_enabled"):
            assert mcp_strategy[field] == rest_strategy[field]

        pending_params = {"strategy_id": 1, "order_ids": [1, 3]}
        rest_pending_response = rest_client.get(
            "/wave-strategies/1/pending-count", params={"order_ids": [1, 3]},
            headers=rest_headers,
        )
        assert rest_pending_response.status_code == 200
        mcp_pending = call(
            rt, "erp_wave_get_wave_strategy_pending_count", pending_params,
        ).structuredContent["data"]
        assert mcp_pending == rest_pending_response.json()["data"]

        wave_params = {
            "strategy_id": 1, "express_type": "STD", "keyword": "MCP-WAVE-OWN",
            "start_time": "2000-01-01T00:00:00+08:00",
            "end_time": "2100-01-01T00:00:00+08:00", "page": 1, "page_size": 1,
        }
        rest_waves_response = rest_client.get(
            "/waves", params=wave_params, headers=rest_headers,
        )
        assert rest_waves_response.status_code == 200
        rest_waves = rest_waves_response.json()["data"]
        mcp_waves = call(rt, "erp_wave_list_waves", wave_params).structuredContent
        assert mcp_waves["page"]["total"] == rest_waves["total"] == 1
        for field in ("id", "wave_no", "strategy_id", "name", "express_type",
                      "total_order_count"):
            assert mcp_waves["data"][0][field] == rest_waves["items"][0][field]

        reversed_wave_params = {
            "start_time": "2026-10-13T00:00:00+08:00",
            "end_time": "2026-10-12T00:00:00+08:00",
        }
        rest_reversed_waves = rest_client.get(
            "/waves", params=reversed_wave_params, headers=rest_headers,
        )
        mcp_reversed_waves = call(
            rt, "erp_wave_list_waves", reversed_wave_params,
        ).structuredContent
        assert rest_reversed_waves.status_code == 400
        assert mcp_reversed_waves["code"] == "INVALID_ARGUMENT"

        rest_wave_response = rest_client.get("/waves/1", headers=rest_headers)
        assert rest_wave_response.status_code == 200
        rest_wave = rest_wave_response.json()["data"]
        mcp_wave = call(rt, "erp_wave_get_wave", {
            "wave_id": 1,
        }).structuredContent["data"]
        for field in ("id", "wave_no", "strategy_id", "name", "express_type",
                      "total_order_count"):
            assert mcp_wave[field] == rest_wave[field]
        assert mcp_wave["order_count"] == len(rest_wave["orders"]) == 2
        assert mcp_wave["load_number_count"] == len(rest_wave["load_numbers"]) == 2
        rest_wave_orders = {order["order_no"]: order for order in rest_wave["orders"]}
        for page_number in (1, 2):
            mcp_wave_orders = call(rt, "erp_wave_list_wave_orders", {
                "wave_id": 1, "page": page_number, "page_size": 1,
            }).structuredContent
            assert mcp_wave_orders["page"]["total"] == len(rest_wave_orders)
            assert len(mcp_wave_orders["data"]) == 1
            mcp_order = mcp_wave_orders["data"][0]
            rest_order = rest_wave_orders[mcp_order["order_no"]]
            for field in ("order_no", "warehouse_id", "warehouse_name", "logistics_id",
                          "logistics_name", "status", "load_no", "sequence"):
                if field in mcp_order:
                    assert mcp_order[field] == rest_order[field]

        rest_loads_response = rest_client.get("/waves/1/load-numbers", headers=rest_headers)
        assert rest_loads_response.status_code == 200
        rest_loads = rest_loads_response.json()["data"]
        for page_number in (1, 2):
            mcp_loads = call(rt, "erp_wave_list_load_numbers", {
                "wave_id": 1, "page": page_number, "page_size": 1,
            }).structuredContent
            assert mcp_loads["page"]["total"] == len(rest_loads) == 2
            assert len(mcp_loads["data"]) == 1
            mcp_load = mcp_loads["data"][0]
            rest_load = next(item for item in rest_loads if item["id"] == mcp_load["id"])
            for field in ("id", "wave_id", "load_no", "express_type"):
                assert mcp_load[field] == rest_load[field]

    strategies = call(rt, "erp_wave_list_wave_strategies", {"page_size": 1}).structuredContent
    assert strategies["page"] == {"page": 1, "page_size": 1, "total": 2, "has_more": True}
    strategy = strategies["data"][0]
    assert strategy["name"] == "MCP波次策略B"
    assert not {"shop_ids", "description"} & set(strategy)
    assert call(rt, "erp_wave_list_wave_strategies", {
        "keyword": "PRIVATE-WAVE-STRATEGY-DESCRIPTION",
    }).structuredContent["page"]["total"] == 0
    enabled_strategy_page = call(rt, "erp_wave_list_wave_strategies", {
        "keyword": "策略A", "is_enabled": True,
    }).structuredContent
    assert enabled_strategy_page["page"]["total"] == 1
    assert enabled_strategy_page["data"][0]["name"] == "MCP波次策略A"
    strategy_detail = call(rt, "erp_wave_get_wave_strategy", {
        "strategy_id": 1,
    }).structuredContent["data"]
    assert strategy_detail["warehouse_name"] == "波次仓"
    assert "description" not in strategy_detail and "shop_ids" not in strategy_detail
    assert call(rt, "erp_wave_get_wave_strategy_pending_count", {
        "strategy_id": 1,
    }).structuredContent["data"]["pending_count"] == 1
    assert call(rt, "erp_wave_get_wave_strategy_pending_count", {
        "strategy_id": 1, "order_ids": [1, 3],
    }).structuredContent["data"]["pending_count"] == 1
    assert call(rt, "erp_wave_get_wave_strategy_pending_count", {
        "strategy_id": 1, "order_ids": [1] * 101,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_wave_get_wave_strategy", {
        "strategy_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_wave_get_wave_strategy_pending_count", {
        "strategy_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_wave_get_wave_strategy_pending_count", {
        "strategy_id": 1, "order_ids": [0],
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_wave_get_wave_strategy_pending_count", {
        "strategy_id": 2,
    }).structuredContent["code"] == "FORBIDDEN"

    wave_page = call(rt, "erp_wave_list_waves", {"page_size": 1}).structuredContent
    assert wave_page["page"] == {"page": 1, "page_size": 1, "total": 2, "has_more": True}
    filtered_wave_page = call(rt, "erp_wave_list_waves", {
        "strategy_id": 1, "express_type": "STD", "keyword": "MCP-WAVE-OWN",
        "start_time": "2000-01-01T00:00:00+08:00",
        "end_time": "2100-01-01T00:00:00+08:00",
    }).structuredContent
    assert filtered_wave_page["page"]["total"] == 1
    assert filtered_wave_page["data"][0]["wave_no"] == "MCP-WAVE-OWN"
    wave_detail = call(rt, "erp_wave_get_wave", {"wave_id": 1}).structuredContent["data"]
    assert wave_detail["wave_no"] == "MCP-WAVE-OWN"
    assert wave_detail["order_count"] == 2 and wave_detail["load_number_count"] == 2
    assert not {"created_by", "created_by_name", "orders", "load_numbers"} & set(wave_detail)
    assert call(rt, "erp_wave_list_waves", {
        "start_time": "2026-10-13T00:00:00+08:00",
        "end_time": "2026-10-12T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    order_page = call(rt, "erp_wave_list_wave_orders", {
        "wave_id": 1, "page_size": 1,
    }).structuredContent
    assert order_page["page"] == {"page": 1, "page_size": 1, "total": 2, "has_more": True}
    order = order_page["data"][0]
    assert order["order_no"] == "MCP-WAVE-ORDER-1"
    assert order["warehouse_name"] == "波次仓" and order["logistics_name"] == "波次物流"
    assert not {"receiver_name", "receiver_phone", "receiver_address", "remark", "paid_amount"} & set(order)
    assert call(rt, "erp_wave_list_wave_orders", {
        "wave_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_wave_get_wave", {
        "wave_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    load_page = call(rt, "erp_wave_list_load_numbers", {
        "wave_id": 1, "page_size": 1,
    }).structuredContent
    assert load_page["page"] == {"page": 1, "page_size": 1, "total": 2, "has_more": True}
    assert load_page["data"][0]["load_no"] == "MCP-LOAD-A"
    assert call(rt, "erp_wave_list_load_numbers", {
        "wave_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after = {
            model.__tablename__: db.scalar(select(func.count(model.id)))
            for model in (WaveStrategy, Wave, WaveOrder, LoadNo)
        }
    assert before == after


def test_purchase_reports_are_sql_paged_scope_safe_and_gate_amounts(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import func
    from app.models.org import Department
    from app.models.party import Supplier
    from app.models.purchase import PurchaseInvoice, PurchaseOrder
    from app.models.rbac import DataScope

    with factory() as db:
        db.add_all([
            Department(id=1, company_id=1, name="报表权限一部"),
            Department(id=2, company_id=1, name="报表权限二部"),
        ])
        db.flush()
        db.get(Staff, 1).department_id = 1
        db.add_all([
            Staff(id=3, company_id=1, department_id=1, name="同部门报表员工"),
            Staff(id=4, company_id=1, department_id=2, name="跨部门报表员工"),
            DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1),
        ])
        db.add_all([
            Supplier(id=1, code="MCP-REPORT-S1", name="报表供应商一"),
            Supplier(id=2, code="MCP-REPORT-S2", name="报表供应商二"),
        ])
        db.add_all([
            PurchaseOrder(
                id=1, order_no="MCP-REPORT-PO-1", supplier_id=1,
                purchaser_id=1, created_by=1, total_qty=Decimal("2.000"),
                total_amount=Decimal("20.00"), arrived_qty=Decimal("1.000"),
                created_at=datetime(2026, 10, 1, 4, 0),
            ),
            PurchaseOrder(
                id=2, order_no="MCP-REPORT-PO-2", supplier_id=2,
                purchaser_id=1, created_by=1, total_qty=Decimal("3.000"),
                total_amount=Decimal("30.00"), arrived_qty=Decimal("1.000"),
                created_at=datetime(2026, 10, 1, 4, 0, 1),
            ),
            PurchaseOrder(
                id=3, order_no="MCP-REPORT-PO-PRIVATE", supplier_id=1,
                purchaser_id=2, created_by=2, total_qty=Decimal("99.000"),
                total_amount=Decimal("990.00"), arrived_qty=Decimal("0.000"),
                created_at=datetime(2026, 10, 1, 4, 0),
            ),
            PurchaseOrder(
                id=4, order_no="MCP-REPORT-PO-DEPARTMENT", supplier_id=1,
                purchaser_id=3, created_by=3, total_qty=Decimal("1.500"),
                total_amount=Decimal("10.00"), arrived_qty=Decimal("0.500"),
                created_at=datetime(2026, 10, 1, 4, 0, 2),
            ),
            PurchaseOrder(
                id=5, order_no="MCP-REPORT-PO-COMPANY", supplier_id=2,
                purchaser_id=4, created_by=4, total_qty=Decimal("4.000"),
                total_amount=Decimal("40.00"), arrived_qty=Decimal("1.000"),
                created_at=datetime(2026, 10, 1, 4, 0, 3),
            ),
            PurchaseInvoice(
                id=1, invoice_no="MCP-REPORT-INV-1", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("113.00"),
                tax_amount=Decimal("13.00"), write_off_amount=Decimal("50.00"),
                write_off_status="PARTIAL", created_by=1,
                created_at=datetime(2026, 10, 1, 4, 0),
            ),
            PurchaseInvoice(
                id=2, invoice_no="MCP-REPORT-INV-2", supplier_id=2,
                invoice_type="NORMAL", amount=Decimal("56.50"),
                tax_amount=Decimal("6.50"), write_off_amount=Decimal("56.50"),
                write_off_status="FULL", created_by=1,
                created_at=datetime(2026, 10, 1, 4, 0, 1),
            ),
            PurchaseInvoice(
                id=3, invoice_no="MCP-REPORT-INV-PRIVATE", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("990.00"),
                tax_amount=Decimal("90.00"), write_off_amount=Decimal("0.00"),
                write_off_status="NONE", created_by=2,
                created_at=datetime(2026, 10, 1, 4, 0),
            ),
            PurchaseInvoice(
                id=4, invoice_no="MCP-REPORT-INV-DEPARTMENT", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("10.00"),
                tax_amount=Decimal("1.00"), write_off_amount=Decimal("5.00"),
                write_off_status="PARTIAL", created_by=3,
                created_at=datetime(2026, 10, 1, 4, 0, 2),
            ),
            PurchaseInvoice(
                id=5, invoice_no="MCP-REPORT-INV-COMPANY", supplier_id=2,
                invoice_type="NORMAL", amount=Decimal("20.00"),
                tax_amount=Decimal("2.00"), write_off_amount=Decimal("20.00"),
                write_off_status="FULL", created_by=4,
                created_at=datetime(2026, 10, 1, 4, 0, 3),
            ),
        ])
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
        before = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(PurchaseInvoice.id))),
        )

    order_token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_report_purchase_orders"],
        permission_scopes=["module:purchase"],
    )["token"]
    order_report = call(runtime(factory, order_token), "erp_report_purchase_orders", {
        "page_size": 1,
    }).structuredContent
    assert order_report["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert order_report["data"]["summary"] == {
        "supplier_count": 2, "order_count": 2,
        "total_qty": "5.000", "arrived_qty": "2.000",
        "in_transit_qty": "3.000", "total_amount": "50.00",
    }
    assert order_report["data"]["items"][0]["supplier_name"] == "报表供应商一"
    boundary = "2026-10-01T12:00:00+08:00"
    order_boundary = call(runtime(factory, order_token), "erp_report_purchase_orders", {
        "start_time": boundary, "end_time": boundary,
    }).structuredContent
    assert order_boundary["page"]["total"] == 1
    assert order_boundary["data"]["summary"]["order_count"] == 1
    assert order_boundary["data"]["summary"]["total_amount"] == "20.00"
    scoped_order_report = call(runtime(factory, order_token), "erp_report_purchase_orders", {
        "start_time": "2000-01-01T00:00:00+08:00",
        "end_time": "2100-01-01T00:00:00+08:00",
        "supplier_id": 2,
    }).structuredContent
    assert scoped_order_report["page"]["total"] == 1
    assert scoped_order_report["data"]["summary"]["total_amount"] == "30.00"
    assert call(runtime(factory, order_token), "erp_report_purchase_orders", {
        "start_time": "2026-10-03T00:00:00+08:00",
        "end_time": "2026-10-01T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(runtime(factory, order_token), "erp_report_purchase_orders", {
        "start_time": "not-a-date",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    # Finance role without finance credential scope may see counts but not amounts.
    purchase_only_token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_report_purchase_invoices"],
        permission_scopes=["module:purchase"],
    )["token"]
    hidden = call(runtime(factory, purchase_only_token), "erp_report_purchase_invoices", {
        "page_size": 1,
    }).structuredContent
    assert hidden["page"]["total"] == 2
    assert hidden["data"]["summary"] == {"invoice_count": 2}
    assert "amount" not in hidden["data"]["items"][0]

    finance_token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_report_purchase_invoices"],
        permission_scopes=["module:purchase", "module:finance"],
    )["token"]
    invoice_report = call(runtime(factory, finance_token), "erp_report_purchase_invoices", {
        "page_size": 1,
    }).structuredContent
    assert invoice_report["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert invoice_report["data"]["summary"] == {
        "invoice_count": 2, "amount": "169.50", "tax_amount": "19.50",
        "net_amount": "150.00", "write_off_amount": "106.50",
    }
    assert invoice_report["data"]["items"][0]["supplier_id"] == 1
    invoice_boundary = call(runtime(factory, finance_token), "erp_report_purchase_invoices", {
        "start_time": boundary, "end_time": boundary,
    }).structuredContent
    assert invoice_boundary["page"]["total"] == 1
    assert invoice_boundary["data"]["summary"]["invoice_count"] == 1
    assert invoice_boundary["data"]["summary"]["amount"] == "113.00"
    scoped_invoice_report = call(runtime(factory, finance_token), "erp_report_purchase_invoices", {
        "start_time": "2000-01-01T00:00:00+08:00",
        "end_time": "2100-01-01T00:00:00+08:00",
        "supplier_id": 2, "write_off_status": "FULL",
    }).structuredContent
    assert scoped_invoice_report["page"]["total"] == 1
    assert scoped_invoice_report["data"]["summary"]["amount"] == "56.50"
    assert call(runtime(factory, finance_token), "erp_report_purchase_invoices", {
        "write_off_status": "INVALID",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(runtime(factory, finance_token), "erp_report_purchase_invoices", {
        "start_time": "2026-10-13T00:00:00+08:00",
        "end_time": "2026-10-12T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    from app.api.report import router as report_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(report_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "财务采购", [])}
    with TestClient(rest_app) as rest_client:
        rest_order_report = rest_client.get(
            "/reports/purchase-orders", headers=rest_headers,
        )
        assert rest_order_report.status_code == 200
        rest_order_data = rest_order_report.json()["data"]
        for field, mcp_value in order_report["data"]["summary"].items():
            rest_value = rest_order_data["summary"][field]
            if field in {"supplier_count", "order_count"}:
                assert mcp_value == rest_value, field
            else:
                assert Decimal(mcp_value) == Decimal(str(rest_value)), field
        assert order_report["data"]["items"][0]["supplier_id"] == rest_order_data["items"][0]["supplier_id"]
        for field in ("order_count", "total_qty", "total_amount", "arrived_qty", "in_transit_qty"):
            left = order_report["data"]["items"][0][field]
            right = rest_order_data["items"][0][field]
            assert Decimal(str(left)) == Decimal(str(right)), field
        rest_order_boundary = rest_client.get(
            "/reports/purchase-orders", headers=rest_headers,
            params={"start_time": boundary, "end_time": boundary},
        )
        assert rest_order_boundary.status_code == 200
        rest_order_boundary_summary = rest_order_boundary.json()["data"]["summary"]
        for field in ("order_count", "total_qty", "total_amount", "arrived_qty", "in_transit_qty"):
            assert Decimal(str(order_boundary["data"]["summary"][field])) == Decimal(
                str(rest_order_boundary_summary[field])
            ), field

        rest_invoice_report = rest_client.get(
            "/reports/purchase-invoices", headers=rest_headers,
        )
        assert rest_invoice_report.status_code == 200
        rest_invoice_data = rest_invoice_report.json()["data"]
        for field, mcp_value in invoice_report["data"]["summary"].items():
            assert Decimal(str(mcp_value)) == Decimal(
                str(rest_invoice_data["summary"][field])
            ), field
        assert invoice_report["data"]["items"][0]["supplier_id"] == rest_invoice_data["items"][0]["supplier_id"]
        for field in ("invoice_count", "amount", "tax_amount", "net_amount", "write_off_amount"):
            assert Decimal(str(invoice_report["data"]["items"][0][field])) == Decimal(
                str(rest_invoice_data["items"][0][field])
            ), field
        rest_invoice_boundary = rest_client.get(
            "/reports/purchase-invoices", headers=rest_headers,
            params={"start_time": boundary, "end_time": boundary},
        )
        assert rest_invoice_boundary.status_code == 200
        rest_invoice_boundary_summary = rest_invoice_boundary.json()["data"]["summary"]
        for field in ("invoice_count", "amount", "tax_amount", "net_amount", "write_off_amount"):
            assert Decimal(str(invoice_boundary["data"]["summary"][field])) == Decimal(
                str(rest_invoice_boundary_summary[field])
            ), field

        expected_scope_results = (
            ("SELF", 2, Decimal("50.00"), 2, Decimal("169.50")),
            ("DEPARTMENT", 3, Decimal("60.00"), 3, Decimal("179.50")),
            ("COMPANY", 4, Decimal("100.00"), 4, Decimal("199.50")),
        )
        for scope, order_count, order_amount, invoice_count, invoice_amount in expected_scope_results:
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            scoped_orders = call(
                runtime(factory, order_token), "erp_report_purchase_orders",
                {"page_size": 50},
            ).structuredContent["data"]["summary"]
            scoped_invoices = call(
                runtime(factory, finance_token), "erp_report_purchase_invoices",
                {"page_size": 50},
            ).structuredContent["data"]["summary"]
            assert scoped_orders["order_count"] == order_count, scope
            assert Decimal(scoped_orders["total_amount"]) == order_amount, scope
            assert scoped_invoices["invoice_count"] == invoice_count, scope
            assert Decimal(scoped_invoices["amount"]) == invoice_amount, scope

            rest_orders = rest_client.get(
                "/reports/purchase-orders", headers=rest_headers,
            ).json()["data"]["summary"]
            rest_invoices = rest_client.get(
                "/reports/purchase-invoices", headers=rest_headers,
            ).json()["data"]["summary"]
            assert rest_orders["order_count"] == order_count, scope
            assert Decimal(str(rest_orders["total_amount"])) == order_amount, scope
            assert rest_invoices["invoice_count"] == invoice_count, scope
            assert Decimal(str(rest_invoices["amount"])) == invoice_amount, scope

    with factory() as db:
        after = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(PurchaseInvoice.id))),
        )
    assert before == after


def test_purchase_inbound_report_pages_groups_and_scopes_both_parent_documents(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import func
    from app.models.org import Department
    from app.models.party import Supplier
    from app.models.purchase import PurchaseOrder
    from app.models.rbac import DataScope
    from app.models.stock import StockIn, StockInItem
    from app.models.warehouse import Warehouse

    with factory() as db:
        db.add_all([
            Department(id=1, company_id=1, name="入库报表权限一部"),
            Department(id=2, company_id=1, name="入库报表权限二部"),
        ])
        db.flush()
        db.get(Staff, 1).department_id = 1
        db.add_all([
            Staff(id=3, company_id=1, department_id=1, name="同部门入库员工"),
            Staff(id=4, company_id=1, department_id=2, name="跨部门入库员工"),
            DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1),
        ])
        db.add(Supplier(id=1, code="MCP-IN-SUP", name="入库报表供应商"))
        db.add(Warehouse(id=1, code="MCP-IN-WH", name="入库报表仓"))
        db.add_all([
            Product(id=1, code="MCP-IN-P1", name="入库商品一"),
            Product(id=2, code="MCP-IN-P2", name="入库商品二"),
            Product(id=3, code="MCP-IN-P3", name="入库商品三"),
            Product(id=4, code="MCP-IN-P4", name="入库商品四"),
        ])
        db.add_all([
            ProductSku(
                id=1, product_id=1, spec_name="规格一", spec_code="MCP-IN-S1",
            ),
            ProductSku(
                id=2, product_id=2, spec_name="规格二", spec_code="MCP-IN-S2",
            ),
            ProductSku(
                id=3, product_id=3, spec_name="规格三", spec_code="MCP-IN-S3",
            ),
            ProductSku(
                id=4, product_id=4, spec_name="规格四", spec_code="MCP-IN-S4",
            ),
        ])
        db.add_all([
            PurchaseOrder(
                id=1, order_no="MCP-IN-PO-OWN", supplier_id=1,
                warehouse_id=1, purchaser_id=1, created_by=1,
            ),
            PurchaseOrder(
                id=2, order_no="MCP-IN-PO-PRIVATE", supplier_id=1,
                warehouse_id=1, purchaser_id=2, created_by=2,
            ),
            PurchaseOrder(
                id=3, order_no="MCP-IN-PO-DEPARTMENT", supplier_id=1,
                warehouse_id=1, purchaser_id=3, created_by=3,
            ),
            PurchaseOrder(
                id=4, order_no="MCP-IN-PO-COMPANY", supplier_id=1,
                warehouse_id=1, purchaser_id=4, created_by=4,
            ),
        ])
        db.add_all([
            StockIn(
                id=1, stock_in_no="MCP-IN-1", warehouse_id=1,
                stock_in_type="PURCHASE", purchase_order_id=1,
                audit_status="APPROVED", created_by=1,
                created_at=datetime(2026, 10, 1, 4, 0),
            ),
            StockIn(
                id=2, stock_in_no="MCP-IN-2", warehouse_id=1,
                stock_in_type="PURCHASE", purchase_order_id=1,
                audit_status="APPROVED", created_by=1,
                created_at=datetime(2026, 10, 1, 4, 0, 1),
            ),
            StockIn(
                id=3, stock_in_no="MCP-IN-PRIVATE", warehouse_id=1,
                stock_in_type="PURCHASE", purchase_order_id=2,
                audit_status="APPROVED", created_by=2,
                created_at=datetime(2026, 10, 1, 4, 0),
            ),
            StockIn(
                id=4, stock_in_no="MCP-IN-DEPARTMENT", warehouse_id=1,
                stock_in_type="PURCHASE", purchase_order_id=3,
                audit_status="APPROVED", created_by=3,
                created_at=datetime(2026, 10, 1, 4, 0, 2),
            ),
            StockIn(
                id=5, stock_in_no="MCP-IN-HIDDEN-STOCKIN-OWNER", warehouse_id=1,
                stock_in_type="PURCHASE", purchase_order_id=3,
                audit_status="APPROVED", created_by=4,
                created_at=datetime(2026, 10, 1, 4, 0, 3),
            ),
            StockIn(
                id=6, stock_in_no="MCP-IN-HIDDEN-ORDER-OWNER", warehouse_id=1,
                stock_in_type="PURCHASE", purchase_order_id=4,
                audit_status="APPROVED", created_by=3,
                created_at=datetime(2026, 10, 1, 4, 0, 4),
            ),
            StockIn(
                id=7, stock_in_no="MCP-IN-COMPANY", warehouse_id=1,
                stock_in_type="PURCHASE", purchase_order_id=4,
                audit_status="APPROVED", created_by=4,
                created_at=datetime(2026, 10, 1, 4, 0, 5),
            ),
        ])
        db.add_all([
            StockInItem(
                id=1, stock_in_id=1, product_id=1, sku_id=1,
                qty=Decimal("4.000"), cost_price=Decimal("10.00"),
                amount=Decimal("40.00"),
            ),
            StockInItem(
                id=2, stock_in_id=2, product_id=2, sku_id=2,
                qty=Decimal("3.000"), cost_price=Decimal("10.00"),
                amount=Decimal("30.00"),
            ),
            StockInItem(
                id=3, stock_in_id=3, product_id=1, sku_id=1,
                qty=Decimal("99.000"), cost_price=Decimal("10.00"),
                amount=Decimal("990.00"),
            ),
            StockInItem(
                id=4, stock_in_id=4, product_id=3, sku_id=3,
                qty=Decimal("2.000"), cost_price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
            StockInItem(
                id=5, stock_in_id=5, product_id=4, sku_id=4,
                qty=Decimal("3.000"), cost_price=Decimal("10.00"),
                amount=Decimal("30.00"),
            ),
            StockInItem(
                id=6, stock_in_id=6, product_id=3, sku_id=3,
                qty=Decimal("5.000"), cost_price=Decimal("10.00"),
                amount=Decimal("50.00"),
            ),
            StockInItem(
                id=7, stock_in_id=7, product_id=4, sku_id=4,
                qty=Decimal("7.000"), cost_price=Decimal("10.00"),
                amount=Decimal("70.00"),
            ),
        ])
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
        before = (
            db.scalar(select(func.count(StockIn.id))),
            db.scalar(select(func.count(StockInItem.id))),
        )

    tool_scopes = [ACCESS_TOOL, "erp_report_purchase_inbounds"]
    purchase_token = issue(
        factory, tool_scopes=tool_scopes,
        permission_scopes=["module:purchase"],
    )["token"]
    hidden = call(runtime(factory, purchase_token), "erp_report_purchase_inbounds", {
        "page_size": 1,
    }).structuredContent
    assert hidden["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert hidden["data"]["summary"] == {
        "stock_in_count": 2, "qty": "7.000",
    }
    assert hidden["data"]["items"][0]["product_name"] == "入库商品一"
    assert "amount" not in hidden["data"]["items"][0]
    filtered_inbound = call(runtime(factory, purchase_token), "erp_report_purchase_inbounds", {
        "start_time": "2000-01-01T00:00:00+08:00",
        "end_time": "2100-01-01T00:00:00+08:00",
        "supplier_id": 1, "purchaser_id": 1, "product_id": 1,
    }).structuredContent
    assert filtered_inbound["page"]["total"] == 1
    assert filtered_inbound["data"]["summary"]["qty"] == "4.000"
    assert call(runtime(factory, purchase_token), "erp_report_purchase_inbounds", {
        "purchaser_id": 2,
    }).structuredContent["page"]["total"] == 0

    finance_token = issue(
        factory, tool_scopes=tool_scopes,
        permission_scopes=["module:purchase", "module:finance"],
    )["token"]
    visible = call(runtime(factory, finance_token), "erp_report_purchase_inbounds", {
        "page_size": 1,
    }).structuredContent
    assert visible["data"]["summary"]["amount"] == "70.00"
    assert visible["data"]["items"][0]["amount"] == "40.00"
    boundary = "2026-10-01T12:00:00+08:00"
    inbound_boundary = call(runtime(factory, finance_token), "erp_report_purchase_inbounds", {
        "start_time": boundary, "end_time": boundary,
    }).structuredContent
    assert inbound_boundary["page"]["total"] == 1
    assert inbound_boundary["data"]["summary"]["stock_in_count"] == 1
    assert inbound_boundary["data"]["summary"]["amount"] == "40.00"

    from app.api.report import router as report_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(report_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "财务采购", [])}
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get(
            "/reports/purchase-inbounds", headers=rest_headers,
        )
        assert rest_response.status_code == 200
        rest_data = rest_response.json()["data"]
        for field in ("stock_in_count", "qty", "amount"):
            assert Decimal(str(visible["data"]["summary"][field])) == Decimal(
                str(rest_data["summary"][field])
            ), field
        mcp_row = visible["data"]["items"][0]
        rest_row = rest_data["items"][0]
        for field in (
            "supplier_id", "purchaser_id", "product_id", "product_code",
            "product_name", "sku_id", "spec_code", "spec_name", "stock_in_count",
        ):
            if field in mcp_row:
                assert mcp_row[field] == rest_row[field], field
        assert Decimal(mcp_row["qty"]) == Decimal(str(rest_row["qty"]))
        assert Decimal(mcp_row["amount"]) == Decimal(str(rest_row["amount"]))
        rest_inbound_boundary = rest_client.get(
            "/reports/purchase-inbounds", headers=rest_headers,
            params={"start_time": boundary, "end_time": boundary},
        )
        assert rest_inbound_boundary.status_code == 200
        rest_inbound_summary = rest_inbound_boundary.json()["data"]["summary"]
        for field in ("stock_in_count", "qty", "amount"):
            assert Decimal(str(inbound_boundary["data"]["summary"][field])) == Decimal(
                str(rest_inbound_summary[field])
            ), field

    for scope, expected_count, expected_qty in (
        ("SELF", 2, Decimal("7.000")),
        ("DEPARTMENT", 3, Decimal("9.000")),
        ("COMPANY", 6, Decimal("24.000")),
    ):
        with factory() as db:
            db.get(DataScope, 1).scope_type = scope
            db.commit()
        scoped = call(runtime(factory, purchase_token), "erp_report_purchase_inbounds", {
            "page_size": 100,
        }).structuredContent["data"]["summary"]
        assert scoped["stock_in_count"] == expected_count, scope
        assert Decimal(scoped["qty"]) == expected_qty, scope

    with factory() as db:
        db.get(DataScope, 1).scope_type = "SELF"
        db.commit()

    assert call(runtime(factory, finance_token), "erp_report_purchase_inbounds", {
        "product_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(runtime(factory, purchase_token), "erp_report_purchase_inbounds", {
        "start_time": "2026-10-13T00:00:00+08:00",
        "end_time": "2026-10-12T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    with factory() as db:
        after = (
            db.scalar(select(func.count(StockIn.id))),
            db.scalar(select(func.count(StockInItem.id))),
        )
    assert before == after


def test_inventory_transit_report_is_single_company_sql_paged_and_owner_scoped(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.org import Department
    from app.models.party import Supplier
    from app.models.purchase import PurchaseOrder, PurchaseOrderItem
    from app.models.rbac import DataScope
    from app.models.warehouse import Location, StockLocation, Warehouse

    tool_scopes = [ACCESS_TOOL, "erp_report_inventory_in_transit"]
    token = issue(
        factory, tool_scopes=tool_scopes,
        permission_scopes=["module:purchase"],
    )["token"]
    initial = call(runtime(factory, token), "erp_report_inventory_in_transit", {})
    assert not initial.isError

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Department(id=1, company_id=1, name="在途报表权限一部"),
            Department(id=2, company_id=1, name="在途报表权限二部"),
        ])
        db.flush()
        db.get(Staff, 1).department_id = 1
        db.add_all([
            Staff(id=3, company_id=1, department_id=1, name="同部门在途员工"),
            Staff(id=4, company_id=1, department_id=2, name="跨部门在途员工"),
            DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1),
        ])
        db.add(Supplier(id=1, code="MCP-TRANSIT-SUP", name="在途供应商"))
        db.add(Warehouse(
            id=1, code="MCP-TRANSIT-WH", name="在途仓",
            is_virtual=False, is_enabled=True,
        ))
        db.add(Location(id=1, warehouse_id=1, code="MCP-TRANSIT-LOC"))
        db.add_all([
            Product(id=1, code="MCP-TRANSIT-P1", name="在途商品一"),
            Product(id=2, code="MCP-TRANSIT-P2", name="在途商品二"),
        ])
        db.add_all([
            ProductSku(
                id=1, product_id=1, spec_name="规格一", spec_code="MCP-TRANSIT-S1",
                cost_price=Decimal("55.00"), sale_price=Decimal("99.00"),
            ),
            ProductSku(
                id=2, product_id=2, spec_name="规格二", spec_code="MCP-TRANSIT-S2",
                cost_price=Decimal("66.00"), sale_price=Decimal("111.00"),
            ),
        ])
        db.add(StockLocation(
            id=1, location_id=1, product_sku_id=1,
            qty=Decimal("5.000"), status="NORMAL",
        ))
        db.add_all([
            PurchaseOrder(
                id=1, order_no="MCP-TRANSIT-OWN", supplier_id=1,
                warehouse_id=1, purchaser_id=1, created_by=1,
                audit_status="APPROVED", status="APPROVED",
            ),
            PurchaseOrder(
                id=2, order_no="MCP-TRANSIT-ORPHAN", supplier_id=1,
                warehouse_id=1, purchaser_id=None, created_by=None,
                audit_status="APPROVED", status="APPROVED",
            ),
            PurchaseOrder(
                id=3, order_no="MCP-TRANSIT-DEPARTMENT", supplier_id=1,
                warehouse_id=1, purchaser_id=3, created_by=3,
                audit_status="APPROVED", status="APPROVED",
            ),
            PurchaseOrder(
                id=4, order_no="MCP-TRANSIT-COMPANY", supplier_id=1,
                warehouse_id=1, purchaser_id=4, created_by=4,
                audit_status="APPROVED", status="APPROVED",
            ),
        ])
        db.add_all([
            PurchaseOrderItem(
                id=1, order_id=1, product_id=1, sku_id=1,
                qty=Decimal("10.000"), arrived_qty=Decimal("4.000"),
                in_transit_qty=Decimal("6.000"), purchase_price=Decimal("2.00"),
            ),
            PurchaseOrderItem(
                id=2, order_id=1, product_id=2, sku_id=2,
                qty=Decimal("2.000"), arrived_qty=Decimal("0.000"),
                in_transit_qty=Decimal("2.000"), purchase_price=Decimal("3.00"),
            ),
            PurchaseOrderItem(
                id=3, order_id=2, product_id=1, sku_id=1,
                qty=Decimal("99.000"), arrived_qty=Decimal("0.000"),
                in_transit_qty=Decimal("99.000"), purchase_price=Decimal("4.00"),
            ),
            PurchaseOrderItem(
                id=4, order_id=3, product_id=1, sku_id=1,
                qty=Decimal("1.500"), arrived_qty=Decimal("0.000"),
                in_transit_qty=Decimal("1.500"), purchase_price=Decimal("4.00"),
            ),
            PurchaseOrderItem(
                id=5, order_id=4, product_id=2, sku_id=2,
                qty=Decimal("3.000"), arrived_qty=Decimal("0.000"),
                in_transit_qty=Decimal("3.000"), purchase_price=Decimal("4.00"),
            ),
        ])
        db.commit()
        before = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(PurchaseOrderItem.id))),
            db.scalar(select(func.count(StockLocation.id))),
        )

    rt = runtime(factory, token)
    first = call(rt, "erp_report_inventory_in_transit", {
        "page_size": 1,
    }).structuredContent
    assert first["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert first["data"]["summary"] == {
        "sku_count": 2, "current_qty": "5.000",
        "in_transit_qty": "8.000", "total_qty": "13.000",
    }
    assert first["data"]["items"][0]["product_name"] == "在途商品一"
    assert not {"cost_price", "purchase_price", "sale_price", "image"} & set(
        first["data"]["items"][0]
    )
    second = call(rt, "erp_report_inventory_in_transit", {
        "page": 2, "page_size": 1,
    }).structuredContent
    assert second["data"]["items"][0]["product_name"] == "在途商品二"
    filtered = call(rt, "erp_report_inventory_in_transit", {
        "product_id": 2,
    }).structuredContent
    assert filtered["page"]["total"] == 1
    assert filtered["data"]["summary"]["current_qty"] == "0.000"
    assert filtered["data"]["summary"]["in_transit_qty"] == "2.000"
    fully_filtered = call(rt, "erp_report_inventory_in_transit", {
        "warehouse_id": 1, "supplier_id": 1, "product_id": 2,
        "keyword": "在途商品二",
    }).structuredContent
    assert fully_filtered["page"]["total"] == 1
    assert fully_filtered["data"]["items"][0]["product_name"] == "在途商品二"
    assert call(rt, "erp_report_inventory_in_transit", {
        "warehouse_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    from app.api.report import router as report_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(report_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "采购员", [])}
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get(
            "/reports/inventory-in-transit", headers=rest_headers,
        )
        assert rest_response.status_code == 200
        rest_data = rest_response.json()["data"]
        for field in ("sku_count", "current_qty", "in_transit_qty", "total_qty"):
            assert Decimal(str(first["data"]["summary"][field])) == Decimal(
                str(rest_data["summary"][field])
            ), field
        mcp_row = first["data"]["items"][0]
        rest_row = rest_data["items"][0]
        for field in (
            "product_id", "product_code", "product_name", "sku_id", "spec_code", "spec_name",
        ):
            if field in mcp_row:
                assert mcp_row[field] == rest_row[field], field
        for field in ("current_qty", "in_transit_qty", "total_qty"):
            assert Decimal(mcp_row[field]) == Decimal(str(rest_row[field])), field

    for scope, expected_in_transit in (
        ("SELF", Decimal("8.000")),
        ("DEPARTMENT", Decimal("9.500")),
        ("COMPANY", Decimal("12.500")),
    ):
        with factory() as db:
            db.get(DataScope, 1).scope_type = scope
            db.commit()
        scoped = call(rt, "erp_report_inventory_in_transit", {
            "page_size": 50,
        }).structuredContent["data"]["summary"]
        assert scoped["sku_count"] == 2, scope
        assert Decimal(scoped["current_qty"]) == Decimal("5.000"), scope
        assert Decimal(scoped["in_transit_qty"]) == expected_in_transit, scope

    with factory() as db:
        after = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(PurchaseOrderItem.id))),
            db.scalar(select(func.count(StockLocation.id))),
        )
    assert before == after


def test_stock_check_queries_are_scoped_sql_paged_and_redacted(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.stock_check import router as stock_check_router
    from app.models.approval import ApprovalRecord
    from app.models.org import Department
    from app.models.rbac import DataScope
    from app.models.stock import StockCheck, StockCheckItem
    from app.models.warehouse import Location, Warehouse
    from app.core.data_scope import clear_scope_policy

    tools = [
        ACCESS_TOOL,
        "erp_stock_check_list_stock_checks",
        "erp_stock_check_get_stock_check",
        "erp_stock_check_list_items",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add_all([
            Department(id=1, company_id=1, name="盘点范围一部"),
            Department(id=2, company_id=1, name="盘点范围二部"),
        ])
        db.flush()
        db.get(Staff, 1).department_id = 1
        db.add_all([
            Staff(id=3, company_id=1, department_id=2, name="跨部门制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门制单人"),
            DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1),
        ])
        db.add(Warehouse(
            id=1, code="MCP-CHECK-WH", name="盘点测试仓",
            address="不外发仓库地址", remark="不外发仓库备注",
        ))
        db.add(Location(
            id=1, warehouse_id=1, code="MCP-CHECK-LOC", capacity=99,
        ))
        db.add(Product(
            id=1, code="MCP-CHECK-P1", name="盘点商品",
            barcode="PRIVATE-PRODUCT-BARCODE", cost_price=Decimal("88.88"),
            description="不得外发商品描述",
        ))
        db.add(ProductSku(
            id=1, product_id=1, spec_code="MCP-CHECK-SKU",
            spec_name="盘点规格", barcode="PRIVATE-SKU-BARCODE",
            cost_price=Decimal("77.77"),
        ))
        db.add_all([
            StockCheck(
                id=1, check_no="MCP-CHECK-OWN-1", warehouse_id=1,
                created_by=1, status="APPROVED", audit_status="APPROVED",
                remark="不得外发盘点备注", lock_warehouse=True,
            ),
            StockCheck(
                id=2, check_no="MCP-CHECK-OWN-2", warehouse_id=1,
                created_by=1, status="PENDING", audit_status="PENDING",
            ),
            StockCheck(
                id=3, check_no="MCP-CHECK-OTHER", warehouse_id=1,
                created_by=3, status="APPROVED", audit_status="APPROVED",
            ),
            StockCheck(
                id=4, check_no="MCP-CHECK-HIDDEN", warehouse_id=1,
                created_by=3, status="APPROVED", audit_status="APPROVED",
            ),
            StockCheck(
                id=5, check_no="MCP-CHECK-DEPARTMENT", warehouse_id=1,
                created_by=4, status="PENDING", audit_status="PENDING",
            ),
        ])
        db.add_all([
            StockCheckItem(
                id=1, check_id=1, product_id=1, sku_id=1, location_id=1,
                current_qty=Decimal("5.000"), actual_qty=Decimal("7.000"),
                barcode="PRIVATE-ITEM-BARCODE",
            ),
            StockCheckItem(
                id=2, check_id=1, product_id=1, sku_id=1, location_id=1,
                current_qty=Decimal("8.000"), actual_qty=Decimal("4.000"),
            ),
            StockCheckItem(
                id=3, check_id=1, product_id=1, sku_id=1, location_id=1,
                current_qty=Decimal("2.000"), actual_qty=None,
            ),
            StockCheckItem(
                id=4, check_id=2, product_id=1, sku_id=1, location_id=1,
                current_qty=Decimal("1.000"), actual_qty=Decimal("1.000"),
            ),
            StockCheckItem(
                id=5, check_id=5, product_id=1, sku_id=1, location_id=1,
                current_qty=Decimal("3.000"), actual_qty=Decimal("4.000"),
            ),
        ])
        db.add(AuditLog(
            actor_id=1, action="IMPORT_RESULT", target_type="stock_check",
            target_id="1", summary="导入成功", detail='{"private":"do not return"}',
        ))
        db.add(ApprovalRecord(
            doc_type="stock_check", doc_id=3, approver_id=1,
            action="APPROVE", status="APPROVED", comment="不得外发审批意见",
        ))
        db.commit()

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=500))
    assert call(rt, tools[1], {}).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, tools[2], {"check_id": 1}).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()

    first = call(rt, tools[1], {"page_size": 1}).structuredContent
    assert first["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert first["data"][0]["check_no"] == "MCP-CHECK-OWN-2"
    own = call(rt, tools[1], {"page": 2, "page_size": 1}).structuredContent
    summary = own["data"][0]
    assert summary["check_no"] == "MCP-CHECK-OWN-1"
    assert summary["item_count"] == 3
    assert summary["result_entered_count"] == 2
    assert summary["current_total_qty"] == "15.000"
    assert summary["actual_total_qty"] == "11.000"
    assert summary["gain_total_qty"] == "2.000"
    assert summary["loss_total_qty"] == "4.000"
    assert summary["has_imported_result"] is True

    detail = call(rt, tools[2], {"check_id": 1}).structuredContent["data"]
    assert detail["warehouse_name"] == "盘点测试仓"
    assert not {
        "remark", "lock_warehouse", "created_by", "auditor_id", "approval",
    } & set(detail)
    assert call(rt, tools[2], {"check_id": 3}).structuredContent["data"][
        "check_no"
    ] == "MCP-CHECK-OTHER"
    assert call(rt, tools[1], {"keyword": "OTHER"}).structuredContent[
        "page"]["total"] == 0
    filtered_checks = call(rt, tools[1], {
        "warehouse_id": 1, "status": "APPROVED", "audit_status": "APPROVED",
        "keyword": "MCP-CHECK-OWN-1",
    }).structuredContent
    assert filtered_checks["page"]["total"] == 1
    assert filtered_checks["data"][0]["check_no"] == "MCP-CHECK-OWN-1"

    page_one = call(rt, tools[3], {"check_id": 1, "page_size": 1}).structuredContent
    assert page_one["page"] == {
        "page": 1, "page_size": 1, "total": 3, "has_more": True,
    }
    item = page_one["data"][0]
    assert item["product_name"] == "盘点商品"
    assert item["difference_qty"] == "2.000"
    assert not {"barcode", "cost_price", "capacity", "description"} & set(item)
    page_two = call(rt, tools[3], {
        "check_id": 1, "page": 2, "page_size": 1,
    }).structuredContent
    page_three = call(rt, tools[3], {
        "check_id": 1, "page": 3, "page_size": 1,
    }).structuredContent
    assert page_two["page"]["has_more"] is True
    assert page_three["page"]["has_more"] is False
    assert [row["id"] for row in (
        page_one["data"] + page_two["data"] + page_three["data"]
    )] == [1, 2, 3]
    assert call(rt, tools[3], {"check_id": 4}).structuredContent[
        "code"
    ] == "FORBIDDEN"
    assert call(rt, tools[3], {"check_id": 1, "page_size": 101}).structuredContent[
        "code"
    ] == "INVALID_ARGUMENT"

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(stock_check_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {
        "Authorization": "Bearer " + create_access_token(1, "盘点范围测试", [])
    }
    expected_by_scope = {
        "SELF": {1, 2},
        "DEPARTMENT": {1, 2, 5},
        "COMPANY": {1, 2, 3, 4, 5},
    }
    with TestClient(rest_app) as rest_client:
        summary_qty_fields = (
            "current_total_qty", "actual_total_qty", "gain_total_qty",
            "loss_total_qty",
        )

        def assert_summary_matches(mcp_row, rest_row):
            for field in (
                "id", "check_no", "warehouse_id", "warehouse_name", "status",
                "audit_status", "item_count", "result_entered_count",
                "has_imported_result", "created_at", "updated_at",
            ):
                if field in rest_row:
                    assert mcp_row[field] == rest_row[field], field
            for field in summary_qty_fields:
                if field in rest_row:
                    assert Decimal(str(mcp_row[field])) == Decimal(str(rest_row[field])), field

        def assert_item_matches(mcp_row, rest_row):
            for field in (
                "id", "product_id", "product_code", "product_name", "sku_id",
                "spec_code", "spec_name", "location_id", "location_code",
            ):
                if field in rest_row:
                    assert mcp_row[field] == rest_row[field], field
            for field in ("current_qty", "actual_qty", "difference_qty"):
                if field in rest_row:
                    rest_value = rest_row[field]
                    mcp_value = mcp_row[field]
                    if rest_value is None:
                        assert mcp_value is None, field
                    else:
                        assert Decimal(str(mcp_value)) == Decimal(str(rest_value)), field

        for scope, expected_ids in expected_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            ordered_ids = sorted(expected_ids, reverse=True)
            for page, expected_page_ids in enumerate(
                (ordered_ids[index:index + 1] for index in range(0, len(ordered_ids))),
                start=1,
            ):
                mcp_page = call(rt, tools[1], {
                    "page": page, "page_size": 1,
                }).structuredContent
                rest_page = rest_client.get(
                    "/stock-checks",
                    params={"page": page, "page_size": 1},
                    headers=rest_headers,
                )
                assert rest_page.status_code == 200, (scope, page)
                rest_data = rest_page.json()["data"]
                mcp_ids = [row["id"] for row in mcp_page["data"]]
                rest_ids = [row["id"] for row in rest_data["items"]]
                assert mcp_ids == rest_ids == expected_page_ids, (scope, page)
                assert mcp_page["page"]["total"] == rest_data["total"] == len(expected_ids)
                assert mcp_page["page"]["has_more"] == (page < len(expected_ids))
                for mcp_row, rest_row in zip(mcp_page["data"], rest_data["items"]):
                    assert_summary_matches(mcp_row, rest_row)

            filtered_mcp = call(rt, tools[1], {
                "status": "APPROVED", "page_size": 20,
            }).structuredContent
            filtered_rest = rest_client.get(
                "/stock-checks", params={"status": "APPROVED", "page_size": 20},
                headers=rest_headers,
            )
            assert filtered_rest.status_code == 200
            expected_approved = expected_ids & {1, 3, 4}
            assert {row["id"] for row in filtered_mcp["data"]} == expected_approved
            assert {row["id"] for row in filtered_rest.json()["data"]["items"]} == expected_approved
            assert filtered_mcp["page"]["total"] == len(expected_approved)
            assert filtered_rest.json()["data"]["total"] == len(expected_approved)

            for filters in (
                {"warehouse_id": 1},
                {"status": "APPROVED"},
                {"audit_status": "PENDING"},
                {"keyword": "MCP-CHECK-OWN-1"},
                {"keyword": "NO-SUCH-CHECK"},
                {
                    "warehouse_id": 1, "status": "APPROVED",
                    "audit_status": "APPROVED", "keyword": "MCP-CHECK-OWN-1",
                },
            ):
                filtered_mcp = call(rt, tools[1], {
                    **filters, "page": 1, "page_size": 20,
                }).structuredContent
                filtered_rest = rest_client.get(
                    "/stock-checks",
                    params={**filters, "page": 1, "page_size": 20},
                    headers=rest_headers,
                )
                assert filtered_rest.status_code == 200, (scope, filters)
                rest_filtered_data = filtered_rest.json()["data"]
                rest_filtered_rows = rest_filtered_data["items"]
                assert "page" in filtered_mcp, (scope, filters, filtered_mcp)
                assert filtered_mcp["page"]["total"] == rest_filtered_data["total"], (scope, filters)
                assert [row["id"] for row in filtered_mcp["data"]] == [
                    row["id"] for row in rest_filtered_rows
                ], (scope, filters)
                assert filtered_mcp["page"]["has_more"] is False, (scope, filters)
                for mcp_row, rest_row in zip(filtered_mcp["data"], rest_filtered_rows):
                    assert_summary_matches(mcp_row, rest_row)

            # 审批参与者可读详情但不会因此扩大列表或其关联明细可见范围。
            for check_id in (1, 2, 3, 4, 5):
                in_owner_scope = check_id in expected_ids
                detail_visible = in_owner_scope or check_id == 3
                mcp_detail = call(rt, tools[2], {"check_id": check_id})
                rest_detail = rest_client.get(
                    f"/stock-checks/{check_id}", headers=rest_headers,
                )
                mcp_items = call(rt, tools[3], {
                    "check_id": check_id, "page_size": 20,
                })
                if detail_visible:
                    assert not mcp_detail.isError, (scope, check_id)
                    assert rest_detail.status_code == 200, (scope, check_id)
                    assert not mcp_items.isError, (scope, check_id)
                    rest_detail_data = rest_detail.json()["data"]
                    mcp_detail_data = mcp_detail.structuredContent["data"]
                    assert_summary_matches(mcp_detail_data, rest_detail_data)
                    rest_item_ids = {
                        row["id"] for row in rest_detail_data["items"]
                    }
                    mcp_item_rows = mcp_items.structuredContent["data"]
                    mcp_item_ids = {row["id"] for row in mcp_item_rows}
                    assert mcp_item_ids == rest_item_ids, (scope, check_id)
                    rest_items_by_id = {
                        row["id"]: row for row in rest_detail_data["items"]
                    }
                    for mcp_item in mcp_item_rows:
                        assert_item_matches(mcp_item, rest_items_by_id[mcp_item["id"]])
                    if check_id == 1:
                        all_mcp_items = (
                            page_one["data"] + page_two["data"] + page_three["data"]
                        )
                        assert [row["id"] for row in all_mcp_items] == [
                            row["id"] for row in rest_detail_data["items"]
                        ]
                        for mcp_item, rest_item in zip(
                            all_mcp_items, rest_detail_data["items"],
                        ):
                            assert_item_matches(mcp_item, rest_item)
                else:
                    assert mcp_detail.structuredContent["code"] == "FORBIDDEN"
                    assert rest_detail.status_code == 404
                    assert mcp_items.structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        assert db.scalar(select(func.count(StockCheck.id))) == 5
        assert db.scalar(select(func.count(StockCheckItem.id))) == 5


def test_stock_receipt_putaway_and_issue_queries_are_bounded_and_scoped(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.party import Supplier
    from app.models.purchase import PurchaseOrder, PurchaseReturn
    from app.models.org import Department
    from app.models.rbac import DataScope
    from app.models.stock import (
        Putaway, StockIn, StockInItem, StockOut, StockOutItem, Transfer, TransferIn,
    )
    from app.models.warehouse import Location, Warehouse

    tools = [
        ACCESS_TOOL,
        "erp_stock_in_list_pending_putaways", "erp_stock_in_list_putaways",
        "erp_stock_in_list_stock_ins", "erp_stock_in_get_stock_in",
        "erp_stock_in_list_stock_in_items", "erp_stock_out_list_stock_outs",
        "erp_stock_out_get_stock_out", "erp_stock_out_list_stock_out_items",
    ]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=4, company_id=1, code="FINANCE", name="财务"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            StaffRole(staff_id=1, role_id=4),
            Department(id=1, company_id=1, name="库存范围一部"),
            Department(id=2, company_id=1, name="库存范围二部"),
            Staff(id=3, company_id=1, department_id=2, name="跨部门库存制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门库存制单人"),
        ])
        db.add(Warehouse(id=1, code="MCP-STOCK-WH", name="库存测试仓"))
        db.add(Supplier(id=1, code="MCP-STOCK-SUP", name="库存测试供应商"))
        db.add(Location(id=1, warehouse_id=1, code="MCP-STOCK-LOC"))
        db.get(Staff, 1).department_id = 1
        db.add(DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1))
        db.add(Product(
            id=1, code="MCP-STOCK-P1", name="入出库测试商品",
            barcode="PRIVATE-PRODUCT", cost_price=Decimal("55.00"),
        ))
        db.add(ProductSku(
            id=1, product_id=1, spec_code="MCP-STOCK-SKU",
            spec_name="测试规格", barcode="PRIVATE-SKU", cost_price=Decimal("33.00"),
        ))
        db.add_all([
            PurchaseOrder(
                id=1, order_no="MCP-STOCK-PO", supplier_id=1,
                purchaser_id=1, created_by=1,
            ),
            PurchaseReturn(
                id=1, return_no="MCP-STOCK-RETURN", supplier_id=1,
                warehouse_id=1, created_by=1,
            ),
            StockIn(
                id=1, stock_in_no="MCP-STOCK-IN-OWN", warehouse_id=1,
                purchase_order_id=1, stock_in_type="PURCHASE", created_by=1,
                status="APPROVED",
                audit_status="APPROVED", total_qty=Decimal("5.000"),
                remark="不外发入库备注",
            ),
            StockIn(
                id=2, stock_in_no="MCP-STOCK-IN-OTHER", warehouse_id=1,
                stock_in_type="PURCHASE", created_by=3, status="APPROVED",
                audit_status="APPROVED", total_qty=Decimal("100.000"),
            ),
            StockIn(
                id=3, stock_in_no="MCP-STOCK-IN-DEPARTMENT", warehouse_id=1,
                stock_in_type="PURCHASE", created_by=4, status="PENDING",
                audit_status="PENDING", total_qty=Decimal("4.000"),
            ),
            StockInItem(
                id=1, stock_in_id=1, product_id=1, sku_id=1,
                qty=Decimal("5.000"), cost_price=Decimal("10.00"),
                amount=Decimal("50.00"), barcode="PRIVATE-IN-BARCODE",
            ),
            StockInItem(
                id=2, stock_in_id=2, product_id=1, sku_id=1,
                qty=Decimal("100.000"), cost_price=Decimal("1.00"),
                amount=Decimal("100.00"),
            ),
            StockInItem(
                id=3, stock_in_id=3, product_id=1, sku_id=1,
                qty=Decimal("4.000"), cost_price=Decimal("2.00"),
                amount=Decimal("8.00"),
            ),
            Transfer(
                id=1, transfer_no="MCP-TRANSFER-OWN", from_warehouse_id=1,
                to_warehouse_id=1, created_by=1,
            ),
            TransferIn(
                id=1, transfer_id=1, transfer_in_no="MCP-TRANSFER-IN-OWN",
                warehouse_id=1, created_by=1, status="APPROVED",
                audit_status="APPROVED",
            ),
            Transfer(
                id=2, transfer_no="MCP-TRANSFER-OTHER", from_warehouse_id=1,
                to_warehouse_id=1, created_by=3,
            ),
            TransferIn(
                id=2, transfer_id=2, transfer_in_no="MCP-TRANSFER-IN-OTHER",
                warehouse_id=1, created_by=3, status="APPROVED",
                audit_status="APPROVED",
            ),
            Putaway(
                id=1, warehouse_id=1, source_type="STOCK_IN", source_id=1,
                location_id=1, product_id=1, sku_id=1, qty=Decimal("1.000"),
                operator_id=1,
            ),
            Putaway(
                id=2, warehouse_id=1, source_type="STOCK_IN", source_id=2,
                location_id=1, product_id=1, sku_id=1, qty=Decimal("10.000"),
                operator_id=3,
            ),
            Putaway(
                id=3, warehouse_id=1, source_type="TRANSFER_IN", source_id=1,
                location_id=1, product_id=1, sku_id=1, qty=Decimal("2.000"),
                operator_id=1,
            ),
            Putaway(
                id=4, warehouse_id=1, source_type="TRANSFER_IN", source_id=2,
                location_id=1, product_id=1, sku_id=1, qty=Decimal("100.000"),
                operator_id=3,
            ),
            StockOut(
                id=1, stock_out_no="MCP-STOCK-OUT-OWN", warehouse_id=1,
                stock_out_type="PURCHASE_RETURN", purchase_return_id=1, created_by=1,
                status="APPROVED", audit_status="APPROVED",
                off_shelf_status="DONE", total_qty=Decimal("3.000"),
                remark="不外发出库备注",
            ),
            StockOut(
                id=2, stock_out_no="MCP-STOCK-OUT-OTHER", warehouse_id=1,
                stock_out_type="PURCHASE_RETURN", created_by=3,
                status="PENDING", audit_status="PENDING",
                off_shelf_status="NONE", total_qty=Decimal("99.000"),
            ),
            StockOut(
                id=3, stock_out_no="MCP-STOCK-OUT-DEPARTMENT", warehouse_id=1,
                stock_out_type="PURCHASE_RETURN", created_by=4,
                status="PENDING", audit_status="PENDING",
                off_shelf_status="NONE", total_qty=Decimal("4.000"),
            ),
            StockOutItem(
                id=1, stock_out_id=1, product_id=1, sku_id=1, location_id=1,
                qty=Decimal("3.000"), cost_price=Decimal("9.99"),
                barcode="PRIVATE-OUT-BARCODE",
            ),
            StockOutItem(
                id=2, stock_out_id=2, product_id=1, sku_id=1, location_id=1,
                qty=Decimal("99.000"), cost_price=Decimal("1.00"),
            ),
            StockOutItem(
                id=3, stock_out_id=3, product_id=1, sku_id=1, location_id=1,
                qty=Decimal("4.000"), cost_price=Decimal("2.00"),
            ),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=200))
    assert call(rt, tools[1], {}).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()

    pending = call(rt, tools[1], {"page_size": 1}).structuredContent
    assert pending["page"] == {
        "page": 1, "page_size": 1, "total": 1, "has_more": False,
    }
    assert pending["data"][0]["received_qty"] == "5.000"
    assert pending["data"][0]["putaway_qty"] == "1.000"
    assert pending["data"][0]["pending_putaway_qty"] == "4.000"
    filtered_pending = call(rt, tools[1], {
        "warehouse_id": 1, "keyword": "MCP-STOCK-SKU",
    }).structuredContent
    assert filtered_pending["page"]["total"] == 1

    putaways = call(rt, tools[2], {"page_size": 1}).structuredContent
    assert putaways["page"]["total"] == 2
    assert putaways["page"]["has_more"] is True
    assert not {"operator_id", "operator_name"} & set(putaways["data"][0])
    assert call(rt, tools[2], {"stock_in_id": 2}).structuredContent[
        "page"]["total"] == 0
    filtered_putaways = call(rt, tools[2], {
        "warehouse_id": 1, "location_id": 1, "product_id": 1,
        "sku_id": 1, "stock_in_id": 1,
    }).structuredContent
    assert filtered_putaways["page"]["total"] == 1

    stock_ins = call(rt, tools[3], {"page_size": 1}).structuredContent
    assert stock_ins["page"]["total"] == 1
    assert stock_ins["data"][0]["stock_in_no"] == "MCP-STOCK-IN-OWN"
    assert stock_ins["data"][0]["putaway_qty"] == "1.000"
    assert not {"remark", "created_by", "auditor_id"} & set(stock_ins["data"][0])
    filtered_stock_ins = call(rt, tools[3], {
        "warehouse_id": 1, "purchase_order_id": 1,
        "status": "APPROVED", "audit_status": "APPROVED",
        "keyword": "MCP-STOCK-IN-OWN",
    }).structuredContent
    assert filtered_stock_ins["page"]["total"] == 1
    assert call(rt, tools[4], {"stock_in_id": 2}).structuredContent[
        "code"] == "FORBIDDEN"
    items = call(rt, tools[5], {"stock_in_id": 1, "page_size": 1}).structuredContent
    assert items["page"]["total"] == 1
    assert items["data"][0]["pending_putaway_qty"] == "4.000"
    assert not {"cost_price", "amount", "barcode"} & set(items["data"][0])
    stock_in_detail = call(rt, tools[4], {"stock_in_id": 1}).structuredContent["data"]
    assert stock_in_detail["stock_in_no"] == "MCP-STOCK-IN-OWN"

    stock_outs = call(rt, tools[6], {"page_size": 1}).structuredContent
    assert stock_outs["page"]["total"] == 1
    assert stock_outs["data"][0]["stock_out_no"] == "MCP-STOCK-OUT-OWN"
    filtered_stock_outs = call(rt, tools[6], {
        "warehouse_id": 1, "purchase_return_id": 1,
        "status": "APPROVED", "audit_status": "APPROVED",
        "off_shelf_status": "DONE", "keyword": "MCP-STOCK-OUT-OWN",
    }).structuredContent
    assert filtered_stock_outs["page"]["total"] == 1
    assert call(rt, tools[7], {"stock_out_id": 2}).structuredContent[
        "code"] == "FORBIDDEN"
    stock_out_detail = call(rt, tools[7], {"stock_out_id": 1}).structuredContent["data"]
    assert stock_out_detail["stock_out_no"] == "MCP-STOCK-OUT-OWN"
    out_items = call(rt, tools[8], {
        "stock_out_id": 1, "page_size": 1,
    }).structuredContent
    assert out_items["data"][0]["qty"] == "3.000"
    assert not {"cost_price", "barcode"} & set(out_items["data"][0])

    from app.api.stock_in import router as stock_in_router
    from app.api.stock_out import router as stock_out_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(stock_in_router)
    rest_app.include_router(stock_out_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "仓库", [])}
    with TestClient(rest_app) as rest_client:
        # The legacy REST pending/putaway endpoints are company-wide; compare
        # equivalent results under COMPANY scope while separate matrix checks
        # below verify narrower SELF/DEPARTMENT visibility on MCP.
        with factory() as db:
            db.get(DataScope, 1).scope_type = "COMPANY"
            db.commit()

        rest_pending_response = rest_client.get("/stock-ins/putaway/pending", params={
            "warehouse_id": 1, "keyword": "MCP-STOCK-SKU",
        }, headers=rest_headers)
        assert rest_pending_response.status_code == 200
        rest_pending_rows = rest_pending_response.json()["data"]
        mcp_pending_page = call(rt, tools[1], {
            "warehouse_id": 1, "keyword": "MCP-STOCK-SKU", "page_size": 20,
        }).structuredContent
        assert mcp_pending_page["page"]["total"] == len(rest_pending_rows) == 1
        for field in (
            "warehouse_id", "warehouse_name", "product_id", "product_name",
            "product_code", "sku_id", "spec_name", "spec_code",
        ):
            assert mcp_pending_page["data"][0][field] == rest_pending_rows[0][field]
        for field in ("received_qty", "putaway_qty", "pending_putaway_qty"):
            assert Decimal(mcp_pending_page["data"][0][field]) == Decimal(
                str(rest_pending_rows[0][field])
            )

        rest_putaways_response = rest_client.get(
            "/putaways", params={"warehouse_id": 1}, headers=rest_headers,
        )
        assert rest_putaways_response.status_code == 200
        rest_putaways = {
            row["id"]: row for row in rest_putaways_response.json()["data"]
        }
        mcp_putaways = {}
        for page_number in (1, 2):
            mcp_page = call(rt, tools[2], {
                "warehouse_id": 1, "page": page_number, "page_size": 2,
            }).structuredContent
            assert mcp_page["page"]["total"] == len(rest_putaways) == 4
            mcp_putaways.update({row["id"]: row for row in mcp_page["data"]})
        assert set(mcp_putaways) == set(rest_putaways)
        for putaway_id, mcp_row in mcp_putaways.items():
            rest_row = rest_putaways[putaway_id]
            for field in (
                "id", "source_type", "source_id", "warehouse_id", "warehouse_name",
                "location_id", "location_code", "product_id", "product_name",
                "product_code", "sku_id", "spec_name", "spec_code",
            ):
                assert mcp_row[field] == rest_row[field], (putaway_id, field)
            assert Decimal(mcp_row["qty"]) == Decimal(str(rest_row["qty"]))
            assert not {"operator_id", "operator_name"} & set(mcp_row)
            if rest_row["source_type"] == "STOCK_IN":
                assert mcp_row["source_no"] == rest_row["stock_in_no"]

        rest_stock_in_list = rest_client.get("/stock-ins", params={
            "warehouse_id": 1, "purchase_order_id": 1, "status": "APPROVED",
            "audit_status": "APPROVED", "keyword": "MCP-STOCK-IN-OWN",
            "page": 1, "page_size": 20,
        }, headers=rest_headers)
        assert rest_stock_in_list.status_code == 200
        rest_stock_in_data = rest_stock_in_list.json()["data"]
        assert filtered_stock_ins["page"]["total"] == rest_stock_in_data["total"] == 1
        assert filtered_stock_ins["data"][0]["stock_in_no"] == rest_stock_in_data["items"][0]["stock_in_no"]

        rest_stock_in = rest_client.get("/stock-ins/1", headers=rest_headers)
        assert rest_stock_in.status_code == 200
        rest_stock_in_detail = rest_stock_in.json()["data"]
        for field in (
            "id", "stock_in_no", "warehouse_id", "warehouse_name", "stock_in_type",
            "purchase_order_id", "purchase_order_no", "status", "audit_status",
            "item_count", "created_at", "updated_at",
        ):
            if field in rest_stock_in_detail and field in stock_in_detail:
                assert stock_in_detail[field] == rest_stock_in_detail[field], field
        for field in ("total_qty", "putaway_qty", "pending_putaway_qty"):
            assert Decimal(stock_in_detail[field]) == Decimal(str(rest_stock_in_detail[field]))
        assert len(items["data"]) == len(rest_stock_in_detail["items"]) == 1
        for mcp_item, rest_item in zip(items["data"], rest_stock_in_detail["items"]):
            for field in ("id", "product_id", "product_code", "product_name", "sku_id", "spec_code", "spec_name"):
                assert mcp_item[field] == rest_item[field], field
            for field in ("qty", "putaway_qty", "pending_putaway_qty"):
                assert Decimal(mcp_item[field]) == Decimal(str(rest_item[field])), field
        assert "approval" not in stock_in_detail and "remark" not in stock_in_detail

        rest_stock_out_list = rest_client.get("/stock-outs", params={
            "warehouse_id": 1, "purchase_return_id": 1, "status": "APPROVED",
            "audit_status": "APPROVED", "off_shelf_status": "DONE",
            "keyword": "MCP-STOCK-OUT-OWN", "page": 1, "page_size": 20,
        }, headers=rest_headers)
        assert rest_stock_out_list.status_code == 200
        rest_stock_out_data = rest_stock_out_list.json()["data"]
        assert filtered_stock_outs["page"]["total"] == rest_stock_out_data["total"] == 1
        assert filtered_stock_outs["data"][0]["stock_out_no"] == rest_stock_out_data["items"][0]["stock_out_no"]

        rest_stock_out = rest_client.get("/stock-outs/1", headers=rest_headers)
        assert rest_stock_out.status_code == 200
        rest_stock_out_detail = rest_stock_out.json()["data"]
        for field in (
            "id", "stock_out_no", "warehouse_id", "warehouse_name", "stock_out_type",
            "purchase_return_id", "purchase_return_no", "status", "audit_status",
            "off_shelf_status", "created_at", "updated_at",
        ):
            if field in rest_stock_out_detail and field in stock_out_detail:
                assert stock_out_detail[field] == rest_stock_out_detail[field], field
        assert Decimal(stock_out_detail["total_qty"]) == Decimal(str(rest_stock_out_detail["total_qty"]))
        assert len(out_items["data"]) == len(rest_stock_out_detail["items"]) == 1
        for field in ("id", "product_id", "product_code", "product_name", "sku_id", "spec_code", "spec_name", "location_id", "location_code"):
            assert out_items["data"][0][field] == rest_stock_out_detail["items"][0][field], field
        assert Decimal(out_items["data"][0]["qty"]) == Decimal(
            str(rest_stock_out_detail["items"][0]["qty"])
        )
        assert "approval" not in stock_out_detail and "remark" not in stock_out_detail

        expected_ids_by_scope = {
            "SELF": {1},
            "DEPARTMENT": {1, 3},
            "COMPANY": {1, 2, 3},
        }
        expected_putaway_ids_by_scope = {
            "SELF": {1, 3},
            "DEPARTMENT": {1, 3},
            "COMPANY": {1, 2, 3, 4},
        }
        expected_pending_by_scope = {
            "SELF": (Decimal("5.000"), Decimal("1.000"), Decimal("4.000")),
            "DEPARTMENT": (Decimal("5.000"), Decimal("1.000"), Decimal("4.000")),
            "COMPANY": (Decimal("105.000"), Decimal("11.000"), Decimal("94.000")),
        }
        for scope, expected_ids in expected_ids_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            scoped_pending = call(rt, tools[1], {
                "warehouse_id": 1, "keyword": "MCP-STOCK-SKU", "page_size": 2,
            }).structuredContent
            assert scoped_pending["page"]["total"] == 1, scope
            assert tuple(
                Decimal(scoped_pending["data"][0][field])
                for field in ("received_qty", "putaway_qty", "pending_putaway_qty")
            ) == expected_pending_by_scope[scope], scope
            rest_pending_in_scope = rest_client.get(
                "/stock-ins/putaway/pending",
                params={"warehouse_id": 1, "keyword": "MCP-STOCK-SKU"},
                headers=rest_headers,
            )
            assert rest_pending_in_scope.status_code == 200
            assert tuple(
                Decimal(str(rest_pending_in_scope.json()["data"][0][field]))
                for field in ("received_qty", "putaway_qty", "pending_putaway_qty")
            ) == expected_pending_by_scope["COMPANY"]

            scoped_putaway_ids = set()
            for page_number in (1, 2):
                scoped_putaways = call(rt, tools[2], {
                    "warehouse_id": 1, "page": page_number, "page_size": 2,
                }).structuredContent
                assert scoped_putaways["page"]["total"] == len(
                    expected_putaway_ids_by_scope[scope]
                ), scope
                scoped_putaway_ids.update(
                    row["id"] for row in scoped_putaways["data"]
                )
            assert scoped_putaway_ids == expected_putaway_ids_by_scope[scope], scope
            rest_putaways_in_scope = rest_client.get(
                "/putaways", params={"warehouse_id": 1}, headers=rest_headers,
            )
            assert rest_putaways_in_scope.status_code == 200
            assert {
                row["id"] for row in rest_putaways_in_scope.json()["data"]
            } == {1, 2, 3, 4}

            for mcp_tool, rest_path in (
                (tools[3], "/stock-ins"), (tools[6], "/stock-outs"),
            ):
                mcp_list = call(rt, mcp_tool, {"page_size": 20}).structuredContent
                rest_list = rest_client.get(
                    rest_path, params={"page": 1, "page_size": 20},
                    headers=rest_headers,
                )
                assert rest_list.status_code == 200, (scope, rest_path)
                rest_data = rest_list.json()["data"]
                mcp_ids = {row["id"] for row in mcp_list["data"]}
                rest_ids = {row["id"] for row in rest_data["items"]}
                assert mcp_ids == rest_ids == expected_ids, (scope, rest_path)
                assert mcp_list["page"]["total"] == rest_data["total"] == len(expected_ids)

            for document_id in (1, 2, 3):
                should_be_visible = (
                    document_id == 1
                    or scope == "COMPANY"
                    or (scope == "DEPARTMENT" and document_id == 3)
                )
                for detail_tool, child_tool, rest_path, child_key in (
                    (tools[4], tools[5], "/stock-ins", "stock_in_id"),
                    (tools[7], tools[8], "/stock-outs", "stock_out_id"),
                ):
                    mcp_detail = call(rt, detail_tool, {child_key: document_id})
                    rest_detail = rest_client.get(
                        f"{rest_path}/{document_id}", headers=rest_headers,
                    )
                    mcp_child = call(rt, child_tool, {
                        child_key: document_id, "page_size": 20,
                    })
                    if should_be_visible:
                        assert not mcp_detail.isError, (scope, detail_tool, document_id)
                        assert rest_detail.status_code == 200, (scope, rest_path, document_id)
                        assert not mcp_child.isError, (scope, child_tool, document_id)
                        expected_item_ids = {
                            row["id"] for row in rest_detail.json()["data"]["items"]
                        }
                        assert {
                            row["id"] for row in mcp_child.structuredContent["data"]
                        } == expected_item_ids, (scope, child_tool, document_id)
                    else:
                        assert mcp_detail.structuredContent["code"] == "FORBIDDEN", (
                            scope, detail_tool, document_id,
                        )
                        assert rest_detail.status_code == 404, (
                            scope, rest_path, document_id,
                        )
                        assert mcp_child.structuredContent["code"] == "FORBIDDEN", (
                            scope, child_tool, document_id,
                        )

    finance_token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:stock", "module:finance"],
    )["token"]
    finance_items = call(runtime(factory, finance_token), tools[5], {
        "stock_in_id": 1,
    }).structuredContent["data"][0]
    assert finance_items["cost_price"] == "10.00"
    assert finance_items["amount"] == "50.00"
    assert not {"barcode", "remark", "created_by"} & set(finance_items)
    finance_out_items = call(runtime(factory, finance_token), tools[8], {
        "stock_out_id": 1,
    }).structuredContent["data"][0]
    assert not {"cost_price", "amount", "barcode"} & set(finance_out_items)
    with factory() as db:
        assert db.scalar(select(func.count(StockIn.id))) == 3
        assert db.scalar(select(func.count(StockOut.id))) == 3
        assert db.scalar(select(func.count(Putaway.id))) == 4


def test_stock_inventory_reports_and_location_adjust_queries_are_safe(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.stock_query import router as stock_query_router
    from app.core.data_scope import clear_scope_policy
    from app.models.stock import (
        LocationAdjust, LocationAdjustItem, StockCostRewriteLog, StockFlow,
        StockOut, StockOutItem, StockWarning, Transfer, TransferOut,
        TransferOutItem,
    )
    from app.models.warehouse import Location, StockLocation, Warehouse

    tools = [
        ACCESS_TOOL,
        "erp_stock_query_list_stock_summary",
        "erp_stock_query_list_stock_locations",
        "erp_stock_query_list_stock_warnings",
        "erp_stock_query_list_stock_costs",
        "erp_stock_query_list_location_adjusts",
        "erp_stock_query_get_location_adjust",
        "erp_stock_query_list_location_adjust_items",
        "erp_search_stock_flows",
    ]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=4, company_id=1, code="FINANCE", name="财务"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            StaffRole(staff_id=1, role_id=4),
        ])
        db.add(Warehouse(id=1, code="MCP-INV-WH", name="库存汇总仓"))
        db.add_all([
            Location(id=1, warehouse_id=1, code="MCP-INV-LOC-1", capacity=50),
            Location(id=2, warehouse_id=1, code="MCP-INV-LOC-2", capacity=100),
        ])
        db.add(Product(
            id=1, code="MCP-INV-P", name="库存汇总商品", warn_stock=4,
            cost_price=Decimal("12.00"),
        ))
        db.add_all([
            ProductSku(
                id=1, product_id=1, spec_code="MCP-INV-SKU-1",
                spec_name="库存规格一", cost_price=Decimal("5.00"),
            ),
            ProductSku(
                id=2, product_id=1, spec_code="MCP-INV-SKU-2",
                spec_name="库存规格二", cost_price=None,
            ),
        ])
        db.add_all([
            StockLocation(
                id=1, location_id=1, product_sku_id=1,
                qty=Decimal("6.000"), status="NORMAL",
            ),
            StockLocation(
                id=2, location_id=2, product_sku_id=1,
                qty=Decimal("2.000"), status="DEFECTIVE",
            ),
            StockLocation(
                id=3, location_id=1, product_sku_id=2,
                qty=Decimal("0.000"), status="NORMAL",
            ),
            StockWarning(
                id=1, product_id=1, sku_id=1,
                current_qty=Decimal("8.000"), warn_qty=Decimal("9.000"),
            ),
            StockFlow(
                id=1, warehouse_id=1, product_id=1, sku_id=1,
                location_id=1, direction="IN", biz_type="PURCHASE",
                qty=Decimal("10.000"), cost_price=Decimal("5.00"),
                total_cost=Decimal("50.00"), source_no="MCP-FLOW-SOURCE-1",
                order_no="MCP-FLOW-ORDER-1",
                flow_time=datetime(2026, 10, 14, 10, 0),
                remark="库存流水私有备注",
            ),
            StockFlow(
                id=2, warehouse_id=1, product_id=1, sku_id=1,
                location_id=1, direction="OUT", biz_type="SALE",
                qty=Decimal("2.000"), cost_price=Decimal("5.00"),
                total_cost=Decimal("10.00"), source_no="MCP-FLOW-SOURCE-2",
                order_no="MCP-FLOW-ORDER-2",
                flow_time=datetime(2026, 10, 15, 10, 0),
                remark="库存流水私有备注",
            ),
            StockOut(
                id=1, stock_out_no="MCP-INV-OUT", warehouse_id=1,
                stock_out_type="PURCHASE_RETURN", created_by=1,
                status="PENDING", off_shelf_status="NONE",
            ),
            StockOutItem(
                id=1, stock_out_id=1, product_id=1, sku_id=1,
                qty=Decimal("4.000"),
            ),
            Transfer(
                id=1, transfer_no="MCP-INV-TRANSFER", from_warehouse_id=1,
                to_warehouse_id=1, created_by=1,
            ),
            TransferOut(
                id=1, transfer_id=1, transfer_out_no="MCP-INV-TO",
                warehouse_id=1, created_by=1, status="APPROVED",
            ),
            TransferOutItem(
                id=1, transfer_out_id=1, product_id=1, sku_id=1,
                location_id=1, qty=Decimal("1.000"),
            ),
            LocationAdjust(
                id=1, adjust_no="MCP-INV-ADJ", warehouse_id=1,
                created_by=1, status="APPROVED", audit_status="APPROVED",
                remark="不外发库位调整备注",
            ),
            LocationAdjustItem(
                id=1, adjust_id=1, product_id=1, sku_id=1,
                from_location_id=1, to_location_id=2, qty=Decimal("2.000"),
            ),
        ])
        db.commit()

    stock_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, stock_token)
    assert call(rt, tools[1], {}).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(stock_query_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {
        "Authorization": "Bearer " + create_access_token(1, "库存报表对照", [])
    }

    summary = call(rt, tools[1], {"page_size": 1}).structuredContent
    assert summary["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    first_summary = summary["data"][0]
    assert first_summary["total_qty"] == "8.000"
    assert first_summary["occupied_qty"] == "5.000"
    assert first_summary["available_qty"] == "3.000"
    assert first_summary["warn_qty"] == "9.000"
    assert first_summary["is_below_warning"] is True
    assert not {"cost_price", "stock_cost", "product_image"} & set(first_summary)
    assert call(rt, tools[1], {"below_warning": True}).structuredContent[
        "page"]["total"] == 2
    filtered_summary = call(rt, tools[1], {
        "warehouse_id": 1, "product_id": 1, "sku_id": 1,
        "keyword": "MCP-INV-SKU-1",
    }).structuredContent
    assert filtered_summary["page"]["total"] == 1

    locations = call(rt, tools[2], {"location_id": 1, "page_size": 1}).structuredContent
    assert locations["page"]["total"] == 2
    assert locations["page"]["has_more"] is True
    assert "location_capacity" not in locations["data"][0]
    filtered_locations = call(rt, tools[2], {
        "warehouse_id": 1, "location_id": 1, "product_id": 1, "sku_id": 1,
    }).structuredContent
    assert filtered_locations["page"]["total"] == 1
    warnings = call(rt, tools[3], {"only_below": True}).structuredContent
    assert warnings["page"]["total"] == 1
    assert warnings["data"][0]["sku_id"] == 2
    costs = call(rt, tools[4], {"page_size": 1}).structuredContent
    assert costs["page"]["total"] == 2
    assert "cost_price" not in costs["data"][0]
    assert "total_cost" not in costs["data"][0]

    adjusts = call(rt, tools[5], {"page_size": 1}).structuredContent
    assert adjusts["page"]["total"] == 1
    assert adjusts["data"][0]["item_count"] == 1
    assert "remark" not in adjusts["data"][0]
    filtered_adjusts = call(rt, tools[5], {
        "warehouse_id": 1, "status": "APPROVED", "audit_status": "APPROVED",
        "keyword": "MCP-INV-ADJ",
    }).structuredContent
    assert filtered_adjusts["page"]["total"] == 1
    detail = call(rt, tools[6], {"adjust_id": 1}).structuredContent["data"]
    assert detail["adjust_no"] == "MCP-INV-ADJ"
    assert "items" not in detail and "created_by" not in detail
    items = call(rt, tools[7], {"adjust_id": 1}).structuredContent
    assert items["page"]["total"] == 1
    assert items["data"][0]["qty"] == "2.000"
    assert not {"remark", "cost_price", "operator_id"} & set(items["data"][0])

    flows = call(rt, "erp_search_stock_flows", {
        "keyword": "库存汇总商品",
        "start_time": "2026-10-14T00:00:00+08:00",
        "end_time": "2026-10-15T23:59:59+08:00",
        "page_size": 1,
    }).structuredContent
    assert flows["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    flow = flows["data"][0]
    assert flow["id"] == 2 and flow["direction"] == "OUT"
    assert flow["product_name"] == "库存汇总商品"
    assert flow["qty"] == "2.000"
    assert flow["order_no"] == "MCP-FLOW-ORDER-2"
    assert not {"cost_price", "total_cost", "remark", "product_image"} & set(flow)
    filtered_flows = call(rt, "erp_search_stock_flows", {
        "direction": "IN", "source_no": "MCP-FLOW-SOURCE-1",
        "biz_type": "PURCHASE", "page_size": 10,
    }).structuredContent
    assert filtered_flows["page"]["total"] == 1
    assert filtered_flows["data"][0]["id"] == 1

    finance_token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:stock", "module:finance"],
    )["token"]
    financial_rt = runtime(factory, finance_token)
    financial_summary = call(financial_rt, tools[1], {
        "sku_id": 1,
    }).structuredContent["data"][0]
    assert financial_summary["cost_price"] == "5.00"
    assert financial_summary["stock_cost"] == "40.00"
    financial_costs_result = call(financial_rt, tools[4], {
        "page_size": 1,
    })
    assert "data" in financial_costs_result.structuredContent, financial_costs_result.structuredContent
    financial_costs = financial_costs_result.structuredContent["data"]
    assert financial_costs[0]["cost_price"] == "5.00"
    assert financial_costs[0]["total_cost"] == "40.00"

    with TestClient(rest_app) as rest_client:
        # 库存汇总分页、筛选和数量/预警口径与同身份ERP查询一致。
        for page in (1, 2):
            mcp_page = call(rt, tools[1], {
                "page": page, "page_size": 1,
            }).structuredContent
            rest_response = rest_client.get(
                "/stock-summary", params={"page": page, "page_size": 1},
                headers=rest_headers,
            )
            assert rest_response.status_code == 200
            rest_page = rest_response.json()["data"]
            mcp_row = mcp_page["data"][0]
            rest_row = rest_page["items"][0]
            assert mcp_row["sku_id"] == rest_row["sku_id"]
            for field in (
                "warehouse_id", "warehouse_name", "product_id", "product_code",
                "product_name", "spec_code", "spec_name", "is_below_warning",
            ):
                assert mcp_row[field] == rest_row[field], (page, field)
            for field in (
                "normal_qty", "defective_qty", "total_qty", "occupied_qty",
                "available_qty", "shortage_qty", "warn_qty",
            ):
                assert Decimal(mcp_row[field]) == Decimal(str(rest_row[field])), (page, field)
            assert mcp_page["page"]["total"] == rest_page["total"] == 2

        filtered_summary = call(rt, tools[1], {
            "below_warning": True, "page_size": 20,
        }).structuredContent
        rest_filtered_summary = rest_client.get(
            "/stock-summary", params={"below_warning": True, "page_size": 20},
            headers=rest_headers,
        )
        assert rest_filtered_summary.status_code == 200
        rest_summary_data = rest_filtered_summary.json()["data"]
        assert [row["sku_id"] for row in filtered_summary["data"]] == [
            row["sku_id"] for row in rest_summary_data["items"]
        ]
        assert filtered_summary["page"]["total"] == rest_summary_data["total"]

        warnings = call(rt, tools[3], {
            "only_below": True, "page_size": 20,
        }).structuredContent
        rest_warnings = rest_client.get(
            "/stock-warnings", params={"only_below": True}, headers=rest_headers,
        )
        assert rest_warnings.status_code == 200
        rest_warning_rows = rest_warnings.json()["data"]
        assert [row["sku_id"] for row in warnings["data"]] == [
            row["sku_id"] for row in rest_warning_rows
        ]
        for mcp_row, rest_row in zip(warnings["data"], rest_warning_rows):
            for field in (
                "product_id", "product_code", "product_name", "sku_id",
                "spec_code", "spec_name", "is_below_warning",
            ):
                assert mcp_row[field] == rest_row[field], field
            for field in ("current_qty", "warn_qty"):
                assert Decimal(mcp_row[field]) == Decimal(str(rest_row[field])), field

        rest_locations = rest_client.get(
            "/stock-locations", params={"warehouse_id": 1}, headers=rest_headers,
        )
        assert rest_locations.status_code == 200
        rest_location_rows = rest_locations.json()["data"]
        mcp_location_rows = []
        for page in range(1, len(rest_location_rows) + 1):
            mcp_location_page = call(rt, tools[2], {
                "warehouse_id": 1, "page": page, "page_size": 1,
            }).structuredContent
            mcp_location_rows.extend(mcp_location_page["data"])
        assert [row["stock_location_id"] for row in mcp_location_rows] == [
            row["stock_location_id"] for row in rest_location_rows
        ]
        for mcp_row, rest_row in zip(mcp_location_rows, rest_location_rows):
            for field in (
                "warehouse_id", "warehouse_name", "location_id", "location_code",
                "product_id", "product_code", "product_name", "sku_id",
                "spec_code", "spec_name", "status",
            ):
                assert mcp_row[field] == rest_row[field], field
            assert Decimal(mcp_row["qty"]) == Decimal(str(rest_row["qty"]))

        mcp_adjust_page = call(rt, tools[5], {
            "page_size": 1,
        }).structuredContent
        rest_adjust_page = rest_client.get(
            "/location-adjusts", params={"page": 1, "page_size": 1},
            headers=rest_headers,
        )
        assert rest_adjust_page.status_code == 200
        rest_adjust_data = rest_adjust_page.json()["data"]
        mcp_adjust = mcp_adjust_page["data"][0]
        rest_adjust = rest_adjust_data["items"][0]
        for field in (
            "id", "adjust_no", "warehouse_id", "warehouse_name", "status",
            "audit_status", "created_at", "updated_at",
        ):
            assert mcp_adjust[field] == rest_adjust[field], field
        assert mcp_adjust["item_count"] == len(rest_adjust["items"])
        mcp_adjust_detail = call(rt, tools[6], {"adjust_id": 1}).structuredContent["data"]
        rest_adjust_detail = rest_client.get(
            "/location-adjusts/1", headers=rest_headers,
        )
        assert rest_adjust_detail.status_code == 200
        rest_adjust_detail_data = rest_adjust_detail.json()["data"]
        for field in (
            "id", "adjust_no", "warehouse_id", "status", "audit_status",
            "created_at", "updated_at",
        ):
            assert mcp_adjust_detail[field] == rest_adjust_detail_data[field], field
        mcp_adjust_items = call(rt, tools[7], {
            "adjust_id": 1, "page_size": 1,
        }).structuredContent
        rest_adjust_items = rest_adjust_detail_data["items"]
        assert [row["id"] for row in mcp_adjust_items["data"]] == [
            row["id"] for row in rest_adjust_items
        ]
        for mcp_item, rest_item in zip(mcp_adjust_items["data"], rest_adjust_items):
            for field in (
                "id", "product_id", "product_code", "product_name", "sku_id",
                "spec_code", "spec_name", "from_location_id", "from_location_code",
                "to_location_id", "to_location_code",
            ):
                assert mcp_item[field] == rest_item[field], field
            assert Decimal(mcp_item["qty"]) == Decimal(str(rest_item["qty"]))

        # 库存凭据不含财务授权时隐藏成本；增加财务授权后金额与REST逐行一致。
        rest_costs = rest_client.get("/stock-costs", headers=rest_headers)
        assert rest_costs.status_code == 200
        rest_cost_rows = {
            row["sku_id"]: row for row in rest_costs.json()["data"]
        }
        for page in (1, 2):
            mcp_cost_page = call(rt, tools[4], {
                "page": page, "page_size": 1,
            }).structuredContent
            mcp_cost = mcp_cost_page["data"][0]
            assert "cost_price" not in mcp_cost and "total_cost" not in mcp_cost
            assert Decimal(mcp_cost["qty"]) == Decimal(
                str(rest_cost_rows[mcp_cost["sku_id"]]["qty"])
            )
            financial_cost_page = call(financial_rt, tools[4], {
                "page": page, "page_size": 1,
            }).structuredContent
            financial_cost = financial_cost_page["data"][0]
            rest_cost = rest_cost_rows[financial_cost["sku_id"]]
            for field in ("qty", "cost_price", "total_cost"):
                assert Decimal(financial_cost[field]) == Decimal(str(rest_cost[field])), field

        # 同身份的时间区间两端闭合；时区偏移归一后与ERP返回同一流水。
        time_range = {
            "start_time": "2026-10-14T10:00:00+08:00",
            "end_time": "2026-10-15T10:00:00+08:00",
        }
        for page in (1, 2):
            mcp_flow_page = call(rt, tools[8], {
                **time_range, "page": page, "page_size": 1,
            }).structuredContent
            rest_flow_response = rest_client.get(
                "/stock-flows",
                params={**time_range, "page": page, "page_size": 1},
                headers=rest_headers,
            )
            assert rest_flow_response.status_code == 200
            rest_flow_page = rest_flow_response.json()["data"]
            mcp_flow = mcp_flow_page["data"][0]
            rest_flow = rest_flow_page["items"][0]
            assert mcp_flow["id"] == rest_flow["id"]
            assert mcp_flow["direction"] == rest_flow["direction"]
            assert Decimal(mcp_flow["qty"]) == Decimal(str(rest_flow["qty"]))
            assert mcp_flow_page["page"]["total"] == rest_flow_page["total"] == 2

        exact_boundary = {
            "start_time": "2026-10-14T10:00:00+08:00",
            "end_time": "2026-10-14T10:00:00+08:00",
        }
        mcp_exact = call(rt, tools[8], exact_boundary).structuredContent
        rest_exact = rest_client.get(
            "/stock-flows", params=exact_boundary, headers=rest_headers,
        )
        assert rest_exact.status_code == 200
        assert [row["id"] for row in mcp_exact["data"]] == [1]
        assert [row["id"] for row in rest_exact.json()["data"]["items"]] == [1]

        reversed_range = {
            "start_time": "2026-10-15T10:00:00+08:00",
            "end_time": "2026-10-14T10:00:00+08:00",
        }
        assert call(rt, tools[8], reversed_range).structuredContent["code"] == "INVALID_ARGUMENT"
        assert rest_client.get(
            "/stock-flows", params=reversed_range, headers=rest_headers,
        ).status_code == 400

    with factory() as db:
        assert db.scalar(select(func.count(StockLocation.id))) == 3
        assert db.scalar(select(func.count(StockCostRewriteLog.id))) == 0
        assert db.scalar(select(func.count(LocationAdjustItem.id))) == 1


def test_shop_stock_queries_are_single_company_paged_and_redacted(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.platform import Platform, Shop
    from app.models.stock import ShopStock, StockOut, StockOutItem, StockSetting
    from app.models.warehouse import Location, StockLocation, Warehouse

    tools = [
        ACCESS_TOOL,
        "erp_stock_settlement_list_shop_stocks",
        "erp_stock_settlement_get_shop_stock",
        "erp_get_stock_settings",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add(Platform(id=1, name="测试平台", code="MCP-PLATFORM"))
        db.add_all([
            Shop(
                id=1, platform_id=1, name="MCP店铺一",
                login_account="不应外发账号", url="https://secret.invalid",
                default_address="不应外发地址",
            ),
            Shop(id=2, platform_id=1, name="MCP店铺二"),
        ])
        db.add(Warehouse(id=1, code="MCP-SHOP-WH", name="店铺库存仓"))
        db.add(Location(id=1, warehouse_id=1, code="MCP-SHOP-LOC"))
        db.add(Product(id=1, code="MCP-SHOP-P", name="店铺库存商品"))
        db.flush()
        db.add_all([
            ProductSku(id=1, product_id=1, spec_code="MCP-SHOP-SKU-1", spec_name="规格一"),
            ProductSku(id=2, product_id=1, spec_code="MCP-SHOP-SKU-2", spec_name="规格二"),
        ])
        db.add_all([
            StockLocation(
                id=1, location_id=1, product_sku_id=1,
                qty=Decimal("8.000"), status="NORMAL",
            ),
            StockOut(
                id=1, stock_out_no="MCP-SHOP-OUT", warehouse_id=1,
                stock_out_type="MANUAL", created_by=1,
                status="PENDING", off_shelf_status="NONE",
            ),
        ])
        db.flush()
        db.add(StockOutItem(
            id=1, stock_out_id=1, product_id=1, sku_id=1,
            qty=Decimal("2.000"),
        ))
        db.add_all([
            ShopStock(
                id=1, shop_id=1, warehouse_id=1, product_id=1, sku_id=1,
                exclusive_qty=Decimal("5.000"), is_sync=True, is_enabled=True,
                remark="不得外发备注", created_by=1,
            ),
            ShopStock(
                id=2, shop_id=2, warehouse_id=1, product_id=1, sku_id=2,
                exclusive_qty=Decimal("4.000"), is_sync=False, is_enabled=False,
                effective_start=datetime(2100, 1, 1),
                effective_end=datetime(2000, 1, 1),
            ),
        ])
        db.commit()

    wrong_module_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    assert call(runtime(factory, wrong_module_token), tools[1], {}).structuredContent[
        "code"
    ] == "FORBIDDEN"

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, token)
    assert call(rt, tools[1], {}).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()

    from app.api.stock_settlement import router as stock_settlement_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(stock_settlement_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    headers = {"Authorization": "Bearer " + create_access_token(1, "仓库员", [])}
    with TestClient(rest_app) as rest_client:
        rest_settings_response = rest_client.get("/stock-settings", headers=headers)
        assert rest_settings_response.status_code == 200
        rest_settings = rest_settings_response.json()["data"]
        mcp_settings = call(rt, "erp_get_stock_settings", {}).structuredContent["data"]
        for field in (
            "id", "sync_stock_default", "shop_stock_enabled",
            "floating_cost_enabled", "off_shelf_below_warning",
        ):
            assert mcp_settings[field] == rest_settings[field], field
        assert mcp_settings["id"] is None

        rest_stock_pages = []
        mcp_stock_pages = []
        for page_number in (1, 2):
            rest_response = rest_client.get(
                "/shop-stocks", params={"page": page_number, "page_size": 1},
                headers=headers,
            )
            assert rest_response.status_code == 200
            rest_page = rest_response.json()["data"]
            rest_stock_pages.extend(rest_page["items"])
            mcp_page = call(rt, "erp_stock_settlement_list_shop_stocks", {
                "page": page_number, "page_size": 1,
            }).structuredContent
            mcp_stock_pages.extend(mcp_page["data"])
            assert mcp_page["page"]["total"] == rest_page["total"]
            assert mcp_page["page"]["has_more"] == (page_number == 1)
        assert [row["id"] for row in mcp_stock_pages] == [
            row["id"] for row in rest_stock_pages
        ]
        for mcp_row, rest_row in zip(mcp_stock_pages, rest_stock_pages):
            for field in (
                "id", "shop_id", "shop_name", "warehouse_id", "warehouse_name",
                "product_id", "product_code", "product_name", "sku_id",
                "spec_code", "spec_name", "is_sync", "is_enabled", "is_effective",
            ):
                assert mcp_row[field] == rest_row[field], (field, mcp_row, rest_row)
            for field in (
                "exclusive_qty", "stock_qty", "occupied_qty", "available_qty",
                "shop_available_qty",
            ):
                assert Decimal(mcp_row[field]) == Decimal(str(rest_row[field])), field
            assert not {"remark", "created_by", "last_sync_qty", "last_sync_at"} & set(mcp_row)

        rest_detail_response = rest_client.get("/shop-stocks/1", headers=headers)
        assert rest_detail_response.status_code == 200
        rest_detail = rest_detail_response.json()["data"]
        mcp_detail = call(rt, "erp_stock_settlement_get_shop_stock", {
            "shop_stock_id": 1,
        }).structuredContent["data"]
        for field in (
            "id", "shop_id", "warehouse_id", "product_id", "sku_id",
            "is_sync", "is_enabled", "is_effective",
        ):
            assert mcp_detail[field] == rest_detail[field], field
        assert Decimal(mcp_detail["shop_available_qty"]) == Decimal(
            str(rest_detail["shop_available_qty"])
        )

    result = call(rt, tools[1], {"page_size": 1}).structuredContent
    assert result["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    row = result["data"][0]
    assert row["shop_name"] == "MCP店铺一"
    assert row["stock_qty"] == "8.000"
    assert row["occupied_qty"] == "2.000"
    assert row["available_qty"] == "6.000"
    assert row["shop_available_qty"] == "5.000"
    assert row["is_effective"] is True
    assert not {
        "login_account", "url", "default_address", "remark", "created_by",
        "last_sync_qty", "last_sync_at",
    } & set(row)
    disabled_stock = call(rt, tools[1], {
        "keyword": "MCP店铺二", "is_enabled": False,
    }).structuredContent
    assert disabled_stock["page"]["total"] == 1
    assert disabled_stock["data"][0]["is_effective"] is False
    filtered_stock = call(rt, tools[1], {
        "shop_id": 1, "warehouse_id": 1, "product_id": 1, "sku_id": 1,
    }).structuredContent
    assert filtered_stock["page"]["total"] == 1

    detail = call(rt, tools[2], {"shop_stock_id": 1}).structuredContent["data"]
    assert detail["id"] == 1 and detail["shop_available_qty"] == "5.000"
    assert call(rt, tools[2], {"shop_stock_id": 99999}).structuredContent[
        "code"
    ] == "FORBIDDEN"
    with factory() as db:
        assert db.scalar(select(func.count(StockSetting.id))) == 0
        assert db.scalar(select(func.count(ShopStock.id))) == 2
        assert db.scalar(select(func.count(StockLocation.id))) == 1
        assert db.scalar(select(func.count(StockOutItem.id))) == 1


def test_stock_settlement_queries_are_sql_paged_and_finance_gated(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.stock_settlement import router as stock_settlement_router
    from app.core.data_scope import clear_scope_policy
    from app.models.stock import StockCostRewriteLog, StockSettlement
    from app.models.warehouse import Warehouse

    tools = [
        ACCESS_TOOL,
        "erp_stock_settlement_list_periods",
        "erp_stock_settlement_list_settlements",
        "erp_stock_settlement_list_cost_rewrite_logs",
    ]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=4, company_id=1, code="FINANCE", name="财务"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            StaffRole(staff_id=1, role_id=4),
        ])
        db.add(Warehouse(id=1, code="MCP-SETTLE-WH", name="结存仓"))
        db.add(Product(id=1, code="MCP-SETTLE-P", name="结存商品"))
        db.flush()
        db.add_all([
            ProductSku(id=1, product_id=1, spec_code="MCP-SETTLE-SKU-1", spec_name="规格一"),
            ProductSku(id=2, product_id=1, spec_code="MCP-SETTLE-SKU-2", spec_name="规格二"),
        ])
        db.add_all([
            StockSettlement(
                id=1, settlement_date=datetime(2026, 10, 31),
                product_id=1, sku_id=1, qty=Decimal("2.000"),
                cost_price=Decimal("3.50"),
            ),
            StockSettlement(
                id=2, settlement_date=datetime(2026, 10, 31),
                product_id=1, sku_id=2, qty=Decimal("4.000"),
                cost_price=Decimal("5.00"),
            ),
            StockSettlement(
                id=3, settlement_date=datetime(2026, 9, 30),
                product_id=1, sku_id=1, qty=Decimal("1.000"),
                cost_price=None,
            ),
            StockCostRewriteLog(
                id=1, batch_no="MCP-REWRITE-BATCH",
                period_start=datetime(2026, 10, 1), period_end=datetime(2026, 10, 31),
                target_type="STOCK_FLOW", target_id=77, warehouse_id=1,
                product_id=1, sku_id=1, qty=Decimal("2.000"),
                cost_source="SETTLEMENT", old_cost_price=Decimal("2.50"),
                new_cost_price=Decimal("3.50"), old_total_cost=Decimal("5.00"),
                new_total_cost=Decimal("7.00"), created_by=1,
                created_at=datetime(2026, 10, 12, 12, 0, 0),
            ),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    rt = runtime(factory, token)
    assert call(rt, tools[1], {}).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(stock_settlement_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {
        "Authorization": "Bearer " + create_access_token(1, "库存结存对照", [])
    }

    periods = call(rt, tools[1], {"page_size": 1}).structuredContent
    assert periods["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert "total_cost" not in periods["data"][0]
    assert periods["data"][0]["settlement_date"] == "2026-10-31"
    settlements = call(rt, tools[2], {
        "settlement_date": "2026-10-31", "sku_id": 1,
    }).structuredContent
    assert settlements["page"]["total"] == 1
    settlement = settlements["data"][0]
    assert settlement["qty"] == "2.000"
    assert "cost_price" not in settlement and "total_cost" not in settlement
    filtered_settlements = call(rt, tools[2], {
        "settlement_date": "2026-10-31", "product_id": 1,
        "keyword": "MCP-SETTLE-P",
    }).structuredContent
    assert filtered_settlements["page"]["total"] == 2
    assert call(rt, tools[2], {"settlement_date": "2026-99-99"}).structuredContent[
        "code"
    ] == "INVALID_ARGUMENT"

    logs = call(rt, tools[3], {
        "batch_no": "MCP-REWRITE-BATCH",
        "start_time": "2026-10-12T12:00:00+08:00",
        "end_time": "2026-10-12T12:00:00+08:00",
    }).structuredContent
    assert logs["page"]["total"] == 1
    assert logs["data"][0]["target_type"] == "STOCK_FLOW"
    assert not {
        "old_cost_price", "new_cost_price", "old_total_cost", "new_total_cost",
        "created_by", "created_by_name",
    } & set(logs["data"][0])
    filtered_logs = call(rt, tools[3], {
        "product_id": 1, "sku_id": 1, "target_type": "STOCK_FLOW",
        "keyword": "MCP-REWRITE-BATCH",
    }).structuredContent
    assert filtered_logs["page"]["total"] == 1
    assert call(rt, tools[3], {
        "start_time": "2026-10-13T00:00:00+08:00",
        "end_time": "2026-10-12T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    finance_token = issue(
        factory,
        tool_scopes=tools,
        permission_scopes=["module:stock", "module:finance"],
    )["token"]
    finance_rt = runtime(factory, finance_token)
    finance_periods = call(finance_rt, tools[1], {
        "page_size": 1,
    }).structuredContent["data"][0]
    assert finance_periods["total_cost"] == "27.00"
    finance_settlement = call(finance_rt, tools[2], {
        "settlement_date": "2026-10-31", "sku_id": 1,
    }).structuredContent["data"][0]
    assert finance_settlement["cost_price"] == "3.50"
    assert finance_settlement["total_cost"] == "7.00"
    finance_log = call(finance_rt, tools[3], {
        "batch_no": "MCP-REWRITE-BATCH",
    }).structuredContent["data"][0]
    assert finance_log["old_cost_price"] == "2.50"
    assert finance_log["new_total_cost"] == "7.00"

    # 以同一员工身份对照 REST 与 MCP 的分页、字段和金额权限；REST 不分页的期间列表
    # 作为 MCP 分页结果的基准。日期时间筛选也必须采用相同的上海本地闭区间。
    with TestClient(rest_app) as rest_client:
        rest_periods_response = rest_client.get(
            "/stock-settlements/periods", headers=rest_headers,
        )
        assert rest_periods_response.status_code == 200
        rest_periods = rest_periods_response.json()["data"]
        assert len(rest_periods) == 2
        for page_number, expected_date in ((1, "2026-10-31"), (2, "2026-09-30")):
            mcp_period_page = call(finance_rt, tools[1], {
                "page": page_number, "page_size": 1,
            }).structuredContent
            rest_period = next(
                item for item in rest_periods
                if str(item["settlement_date"])[:10] == expected_date
            )
            mcp_period = mcp_period_page["data"][0]
            assert mcp_period["settlement_date"] == expected_date
            assert mcp_period["item_count"] == rest_period["item_count"]
            assert Decimal(mcp_period["total_qty"]) == Decimal(str(rest_period["total_qty"]))
            assert Decimal(mcp_period["total_cost"]) == Decimal(str(rest_period["total_cost"]))

            mcp_hidden_period = call(rt, tools[1], {
                "page": page_number, "page_size": 1,
            }).structuredContent["data"][0]
            assert "total_cost" not in mcp_hidden_period

        for page_number in (1, 2):
            params = {
                "settlement_date": "2026-10-31",
                "page": page_number,
                "page_size": 1,
            }
            rest_settlements_response = rest_client.get(
                "/stock-settlements", params=params, headers=rest_headers,
            )
            assert rest_settlements_response.status_code == 200
            rest_settlements = rest_settlements_response.json()["data"]
            assert rest_settlements["total"] == 2
            mcp_settlements = call(finance_rt, tools[2], {
                "settlement_date": "2026-10-31",
                "page": page_number,
                "page_size": 1,
            }).structuredContent
            rest_item = rest_settlements["items"][0]
            mcp_item = mcp_settlements["data"][0]
            assert mcp_item["id"] == rest_item["id"]
            assert mcp_item["settlement_date"] == str(rest_item["settlement_date"])[:10]
            assert mcp_item["product_code"] == rest_item["product_code"]
            assert mcp_item["spec_code"] == rest_item["spec_code"]
            assert Decimal(mcp_item["qty"]) == Decimal(str(rest_item["qty"]))
            assert Decimal(mcp_item["cost_price"]) == Decimal(str(rest_item["cost_price"]))
            assert Decimal(mcp_item["total_cost"]) == Decimal(str(rest_item["total_cost"]))

        log_params = {
            "batch_no": "MCP-REWRITE-BATCH",
            "start_time": "2026-10-12T12:00:00+08:00",
            "end_time": "2026-10-12T12:00:00+08:00",
            "page": 1,
            "page_size": 1,
        }
        rest_logs_response = rest_client.get(
            "/stock-settlements/cost-rewrite-logs",
            params=log_params,
            headers=rest_headers,
        )
        assert rest_logs_response.status_code == 200
        rest_logs = rest_logs_response.json()["data"]
        assert rest_logs["total"] == 1
        mcp_finance_log = call(finance_rt, tools[3], log_params).structuredContent["data"][0]
        rest_log = rest_logs["items"][0]
        assert mcp_finance_log["id"] == rest_log["id"]
        assert mcp_finance_log["batch_no"] == rest_log["batch_no"]
        assert mcp_finance_log["target_type"] == rest_log["target_type"]
        assert mcp_finance_log["product_code"] == rest_log["product_code"]
        assert Decimal(mcp_finance_log["qty"]) == Decimal(str(rest_log["qty"]))
        for field in (
            "old_cost_price", "new_cost_price", "old_total_cost", "new_total_cost",
        ):
            assert Decimal(mcp_finance_log[field]) == Decimal(str(rest_log[field]))

        reversed_range_response = rest_client.get(
            "/stock-settlements/cost-rewrite-logs",
            params={
                "start_time": "2026-10-13T00:00:00+08:00",
                "end_time": "2026-10-12T00:00:00+08:00",
            },
            headers=rest_headers,
        )
        assert reversed_range_response.status_code == 400

    with factory() as db:
        assert db.scalar(select(func.count(StockSettlement.id))) == 3
        assert db.scalar(select(func.count(StockCostRewriteLog.id))) == 1


def test_shipment_and_shop_reads_are_single_company_paged_and_redacted(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.order import Order, OrderItem, OrderShipment, OrderShipmentItem
    from app.models.party import Supplier
    from app.models.platform import Platform, Shop, ShopApi
    from app.models.warehouse import Warehouse

    tools = [
        ACCESS_TOOL,
        "erp_ship_list_shipments", "erp_ship_list_order_shipments",
        "erp_ship_get_shipment", "erp_ship_list_shipment_items",
        "erp_shop_list_platforms", "erp_shop_get_platform",
        "erp_shop_list_shops", "erp_shop_get_shop", "erp_shop_get_api_status",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:order"],
    )["token"]
    assert call(runtime(factory, token), "erp_ship_list_shipments", {}).structuredContent[
        "code"
    ] == "FORBIDDEN"
    assert call(runtime(factory, token), "erp_shop_list_platforms", {}).structuredContent[
        "code"
    ] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Platform(id=1, name="测试平台一", code="MCP-PLAT-1"),
            Platform(id=2, name="测试平台二", code="MCP-PLAT-2"),
            Supplier(id=1, code="MCP-SHOP-SUP", name="店铺供应商"),
            Warehouse(id=1, code="MCP-SHIP-WH", name="发货仓"),
            Product(id=1, code="MCP-SHIP-PROD", name="发货商品"),
            ProductSku(
                id=1, product_id=1, spec_name="发货规格", spec_code="MCP-SHIP-SKU",
                cost_price=Decimal("5.00"), sale_price=Decimal("15.00"),
            ),
        ])
        db.add_all([
            Shop(
                id=1, platform_id=1, name="MCP测试店铺一", supplier_id=1,
                login_account="PRIVATE-LOGIN", url="https://private.invalid",
                default_address="PRIVATE-SHOP-ADDRESS", message_match="PRIVATE-MATCH",
                is_locked=False, auto_sync_order=True, auto_sync_stock=False,
                download_mode="ALL",
            ),
            Shop(
                id=2, platform_id=2, name="MCP测试店铺二",
                login_account="PRIVATE-LOGIN-2", is_locked=True,
            ),
        ])
        db.add(ShopApi(
            id=1, shop_id=1, api_code="SECRET-API-CODE",
            verify_code="SECRET-VERIFY", app_key="SECRET-KEY",
            app_secret="SECRET-APP-SECRET",
        ))
        db.add(Order(
            id=1, order_no="MCP-SHIP-ORDER", shop_id=1, warehouse_id=1,
            receiver_name="PRIVATE-RECEIVER", receiver_phone="13800138000",
            receiver_address="PRIVATE-ADDRESS",
        ))
        db.add(OrderItem(
            id=1, order_id=1, product_id=1, sku_id=1,
            qty=Decimal("2.000"), price=Decimal("15.00"),
            amount=Decimal("30.00"),
        ))
        db.add(OrderShipment(
            id=1, shipment_no="MCP-SHIPMENT-1", order_id=1, warehouse_id=1,
            package_no=1, tracking_no="TRACK-SECRET-5678", status="SHIPPED",
            platform_sync_status="FAILED", platform_sync_message="SECRET-SYNC-MESSAGE",
            shipped_by=1, remark="PRIVATE-SHIP-REMARK",
        ))
        db.add(OrderShipmentItem(
            id=1, shipment_id=1, order_item_id=1, product_id=1, sku_id=1,
            qty=Decimal("2.000"), cost_price=Decimal("5.00"),
            barcode="PRIVATE-BARCODE",
        ))
        db.commit()
        before = (
            db.scalar(select(func.count(Shop.id))),
            db.scalar(select(func.count(ShopApi.id))),
            db.scalar(select(func.count(OrderShipment.id))),
            db.scalar(select(func.count(OrderShipmentItem.id))),
        )

    rt = runtime(factory, token)
    platforms = call(rt, "erp_shop_list_platforms", {"page_size": 1}).structuredContent
    assert platforms["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert platforms["data"][0] == {
        "id": 1, "name": "测试平台一", "code": "MCP-PLAT-1",
    }
    assert call(rt, "erp_shop_get_platform", {"platform_id": 1}).structuredContent[
        "data"
    ]["name"] == "测试平台一"
    assert call(rt, "erp_shop_get_platform", {
        "platform_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_shop_list_platforms", {
        "keyword": "MCP-PLAT-2",
    }).structuredContent["page"]["total"] == 1

    shop_page = call(rt, "erp_shop_list_shops", {"page_size": 1}).structuredContent
    assert shop_page["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    shop = shop_page["data"][0]
    assert shop["name"] == "MCP测试店铺二"
    detail = call(rt, "erp_shop_get_shop", {"shop_id": 1}).structuredContent["data"]
    assert detail["api_configured"] is True
    assert call(rt, "erp_shop_get_shop", {
        "shop_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert not {
        "login_account", "url", "default_address", "message_match",
        "api_code", "verify_code", "app_key", "app_secret",
    } & set(detail)
    assert call(rt, "erp_shop_get_api_status", {"shop_id": 1}).structuredContent[
        "data"
    ] == {"shop_id": 1, "api_configured": True}
    assert call(rt, "erp_shop_get_api_status", {"shop_id": 2}).structuredContent[
        "data"
    ]["api_configured"] is False
    assert call(rt, "erp_shop_get_api_status", {
        "shop_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_shop_list_shops", {
        "keyword": "PRIVATE-LOGIN",
    }).structuredContent["page"]["total"] == 0
    filtered_shops = call(rt, "erp_shop_list_shops", {
        "platform_id": 1, "is_locked": False,
        "auto_sync_order": True, "auto_sync_stock": False,
    }).structuredContent
    assert filtered_shops["page"]["total"] == 1
    assert filtered_shops["data"][0]["name"] == "MCP测试店铺一"

    shipment_page = call(rt, "erp_ship_list_shipments", {
        "status": "SHIPPED", "tracking_no": "TRACK-SECRET-5678",
    }).structuredContent
    assert shipment_page["page"]["total"] == 1
    shipment = shipment_page["data"][0]
    assert shipment["tracking_no_masked"] == "****5678"
    assert shipment_page["effective_filters"]["tracking_no"] == "[用于检索，已脱敏]"
    assert not {
        "tracking_no", "platform_sync_message", "shipped_by", "shipped_by_name",
        "remark", "items",
    } & set(shipment)
    shipment_detail = call(rt, "erp_ship_get_shipment", {
        "shipment_id": 1,
    }).structuredContent["data"]
    assert shipment_detail["order_no"] == "MCP-SHIP-ORDER"
    assert call(rt, "erp_ship_list_order_shipments", {
        "order_id": 1,
    }).structuredContent["page"]["total"] == 1
    filtered_shipments = call(rt, "erp_ship_list_shipments", {
        "order_id": 1, "platform_sync_status": "FAILED",
        "start_time": "2000-01-01T00:00:00+08:00",
        "end_time": "2100-01-01T00:00:00+08:00",
    }).structuredContent
    assert filtered_shipments["page"]["total"] == 1
    assert call(rt, "erp_ship_list_shipments", {
        "order_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_ship_list_shipments", {
        "start_time": "2026-10-13T00:00:00+08:00",
        "end_time": "2026-10-12T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    shipment_items = call(rt, "erp_ship_list_shipment_items", {
        "shipment_id": 1, "page_size": 1,
    }).structuredContent
    assert shipment_items["data"][0]["product_name"] == "发货商品"
    assert shipment_items["data"][0]["qty"] == "2.000"
    assert call(rt, "erp_ship_list_shipment_items", {
        "shipment_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert not {"cost_price", "barcode", "image"} & set(shipment_items["data"][0])
    assert call(rt, "erp_ship_get_shipment", {
        "shipment_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"

    from app.api.ship import router as ship_router
    from app.api.shop import router as shop_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(ship_router)
    rest_app.include_router(shop_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "仓库", [])}
    with TestClient(rest_app) as rest_client:
        rest_list = rest_client.get("/shipments", params={
            "status": "SHIPPED", "tracking_no": "TRACK-SECRET-5678",
            "page": 1, "page_size": 20,
        }, headers=rest_headers)
        assert rest_list.status_code == 200
        rest_list_data = rest_list.json()["data"]
        assert shipment_page["page"]["total"] == rest_list_data["total"] == 1
        rest_shipment_row = rest_list_data["items"][0]
        assert shipment["id"] == rest_shipment_row["id"]
        assert shipment["shipment_no"] == rest_shipment_row["shipment_no"]
        assert shipment["tracking_no_masked"] == "****" + rest_shipment_row["tracking_no"][-4:]

        rest_detail_response = rest_client.get("/shipments/1", headers=rest_headers)
        assert rest_detail_response.status_code == 200
        rest_detail_data = rest_detail_response.json()["data"]
        for field in (
            "id", "shipment_no", "order_id", "order_no", "warehouse_id", "package_no",
            "shipping_type", "source", "status", "expected_weight_g", "actual_weight_g",
            "weight_out_of_range", "weight_overridden", "platform_sync_status",
            "shipped_at", "created_at", "updated_at",
        ):
            if field in shipment_detail:
                assert shipment_detail[field] == rest_detail_data[field], field
        assert shipment_detail["tracking_no_masked"] == "****5678"
        assert "platform_sync_message" not in shipment_detail and "remark" not in shipment_detail

        rest_order_shipments = rest_client.get(
            "/orders/1/shipments", headers=rest_headers,
        )
        assert rest_order_shipments.status_code == 200
        rest_order_rows = rest_order_shipments.json()["data"]
        mcp_order_shipments = call(rt, "erp_ship_list_order_shipments", {
            "order_id": 1, "page_size": 20,
        }).structuredContent
        assert mcp_order_shipments["page"]["total"] == len(rest_order_rows) == 1
        assert mcp_order_shipments["data"][0]["id"] == rest_order_rows[0]["id"]
        assert mcp_order_shipments["data"][0]["tracking_no_masked"] == "****5678"

        rest_items = rest_detail_data["items"]
        assert shipment_items["page"]["total"] == len(rest_items) == 1
        for field in ("id", "product_id", "product_name", "product_code", "sku_id", "spec_code", "spec_name"):
            if field in rest_items[0]:
                assert shipment_items["data"][0][field] == rest_items[0][field], field
        assert Decimal(shipment_items["data"][0]["qty"]) == Decimal(str(rest_items[0]["qty"]))

        # Compare platform/shop catalog reads under the same identity while
        # keeping REST-only secrets out of assertions and serialized output.
        rest_platforms_response = rest_client.get(
            "/platforms", params={"keyword": "MCP-PLAT"}, headers=rest_headers,
        )
        assert rest_platforms_response.status_code == 200
        rest_platforms = rest_platforms_response.json()["data"]
        mcp_platform_rows = []
        for page_number in (1, 2):
            page = call(rt, "erp_shop_list_platforms", {
                "keyword": "MCP-PLAT", "page": page_number, "page_size": 1,
            }).structuredContent
            mcp_platform_rows.extend(page["data"])
        assert [row["id"] for row in mcp_platform_rows] == [
            row["id"] for row in rest_platforms
        ]
        assert [row["id"] for row in mcp_platform_rows] == [1, 2]
        rest_platform_detail = rest_client.get(
            "/platforms/1", headers=rest_headers,
        )
        assert rest_platform_detail.status_code == 200
        assert call(rt, "erp_shop_get_platform", {
            "platform_id": 1,
        }).structuredContent["data"] == rest_platform_detail.json()["data"]

        rest_shops_response = rest_client.get(
            "/shops", params={"keyword": "MCP测试店铺", "page": 1, "page_size": 20},
            headers=rest_headers,
        )
        assert rest_shops_response.status_code == 200
        rest_shops = rest_shops_response.json()["data"]
        mcp_shop_rows = []
        for page_number in (1, 2):
            page = call(rt, "erp_shop_list_shops", {
                "keyword": "MCP测试店铺", "page": page_number, "page_size": 1,
            }).structuredContent
            assert page["page"]["total"] == rest_shops["total"] == 2
            mcp_shop_rows.extend(page["data"])
        assert [row["id"] for row in mcp_shop_rows] == [
            row["id"] for row in rest_shops["items"]
        ]
        filtered_shop_args = {
            "platform_id": 1, "is_locked": False,
            "auto_sync_order": True, "auto_sync_stock": False,
        }
        rest_filtered_shops = rest_client.get(
            "/shops", params=filtered_shop_args, headers=rest_headers,
        )
        assert rest_filtered_shops.status_code == 200
        mcp_filtered_shops = call(
            rt, "erp_shop_list_shops", filtered_shop_args,
        ).structuredContent
        rest_filtered_data = rest_filtered_shops.json()["data"]
        assert mcp_filtered_shops["page"]["total"] == rest_filtered_data["total"] == 1
        assert [row["id"] for row in mcp_filtered_shops["data"]] == [
            row["id"] for row in rest_filtered_data["items"]
        ]
        shop_common_fields = (
            "id", "platform_id", "platform_name", "name", "supplier_id",
            "supplier_name", "is_locked", "auto_sync_order", "auto_sync_stock",
            "download_mode", "enable_logistics_params", "api_configured",
        )
        rest_shop_by_id = {row["id"]: row for row in rest_shops["items"]}
        for row in mcp_shop_rows:
            for field in shop_common_fields:
                assert row[field] == rest_shop_by_id[row["id"]][field], field

        rest_shop_detail = rest_client.get("/shops/1", headers=rest_headers)
        assert rest_shop_detail.status_code == 200
        rest_shop_data = rest_shop_detail.json()["data"]
        mcp_shop_data = call(rt, "erp_shop_get_shop", {
            "shop_id": 1,
        }).structuredContent["data"]
        for field in shop_common_fields:
            assert mcp_shop_data[field] == rest_shop_data[field], field
        rest_shop_api = rest_client.get("/shops/1/api", headers=rest_headers)
        assert rest_shop_api.status_code == 200
        assert rest_shop_api.json()["data"] is not None
        assert call(rt, "erp_shop_get_api_status", {
            "shop_id": 1,
        }).structuredContent["data"]["api_configured"] is True
        rest_shop_without_api = rest_client.get("/shops/2", headers=rest_headers)
        assert rest_shop_without_api.status_code == 200
        assert rest_shop_without_api.json()["data"]["api_configured"] is False
        assert call(rt, "erp_shop_get_api_status", {
            "shop_id": 2,
        }).structuredContent["data"]["api_configured"] is False

        # REST accepts sensitive-account matches; MCP deliberately restricts
        # shop search to the public shop name and must not reveal that match.
        rest_private_match = rest_client.get(
            "/shops", params={"keyword": "PRIVATE-LOGIN"}, headers=rest_headers,
        )
        assert rest_private_match.status_code == 200
        assert rest_private_match.json()["data"]["total"] == 2
        mcp_private_match = call(rt, "erp_shop_list_shops", {
            "keyword": "PRIVATE-LOGIN",
        }).structuredContent
        assert mcp_private_match["page"]["total"] == 0

    result_text = json.dumps([
        shop_page, shipment_page, shipment_items, detail, shipment_detail,
    ], ensure_ascii=False)
    for secret in (
        "PRIVATE-LOGIN", "private.invalid", "PRIVATE-SHOP-ADDRESS",
        "PRIVATE-MATCH", "SECRET-API-CODE", "SECRET-VERIFY", "SECRET-KEY",
        "SECRET-APP-SECRET", "SECRET-SYNC-MESSAGE", "PRIVATE-SHIP-REMARK",
        "PRIVATE-BARCODE", "PRIVATE-RECEIVER", "13800138000", "PRIVATE-ADDRESS",
    ):
        assert secret not in result_text
    with factory() as db:
        after = (
            db.scalar(select(func.count(Shop.id))),
            db.scalar(select(func.count(ShopApi.id))),
            db.scalar(select(func.count(OrderShipment.id))),
            db.scalar(select(func.count(OrderShipmentItem.id))),
        )
    assert before == after


def test_sales_order_logs_are_sql_paged_and_hide_operator_and_free_text(factory):
    from datetime import datetime
    from sqlalchemy import delete, func
    from app.models.approval import ApprovalRecord
    from app.models.order import Order
    from app.models.platform import Platform, Shop
    from app.models.rbac import StaffRole

    tool_names = [ACCESS_TOOL, "erp_order_list_order_logs"]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    assert call(runtime(factory, token), "erp_order_list_order_logs", {
        "order_id": 1,
    }).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add(Platform(id=1, name="测试平台", code="MCP-LOG-PLATFORM"))
        db.add(Shop(id=1, platform_id=1, name="日志测试店铺"))
        db.add(Order(id=1, order_no="MCP-LOG-ORDER", shop_id=1))
        db.add_all([
            AuditLog(
                actor_id=1, actor_name="PRIVATE-OPERATOR",
                action="CREATE", target_type="order", target_id="1",
                summary="PRIVATE-SUMMARY 13800138000",
                reason="PRIVATE-REASON", detail='{"account":"PRIVATE-DETAIL"}',
                created_at=datetime(2026, 10, 1, 9),
            ),
            AuditLog(
                actor_id=1, actor_name="PRIVATE-OPERATOR",
                action="UNRECOGNIZED-PRIVATE-ACTION", target_type="order",
                target_id="1", summary="PRIVATE-SECOND-SUMMARY",
                created_at=datetime(2026, 10, 2, 9),
            ),
            ApprovalRecord(
                doc_type="order", doc_id=1, doc_no="MCP-LOG-ORDER",
                step=2, approver_id=1, action="APPROVE", status="APPROVED",
                comment="PRIVATE-COMMENT", reason="PRIVATE-APPROVAL-REASON",
                audit_time=datetime(2026, 10, 3, 9),
                created_at=datetime(2026, 10, 3, 8),
                updated_at=datetime(2026, 10, 3, 9),
            ),
        ])
        db.commit()
        before = (
            db.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.target_type == "order", AuditLog.target_id == "1",
            )),
            db.scalar(select(func.count(ApprovalRecord.id))),
        )

    rt = runtime(factory, token)
    page = call(rt, "erp_order_list_order_logs", {
        "order_id": 1, "page_size": 2,
    })
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 2, "total": 3, "has_more": True,
    }
    events = page.structuredContent["data"]
    assert [row["source"] for row in events] == ["APPROVAL", "AUDIT"]
    assert events[0]["status"] == "APPROVED" and events[0]["step"] == 2
    assert events[1]["action"] == "OTHER"
    second_page = call(rt, "erp_order_list_order_logs", {
        "order_id": 1, "page": 2, "page_size": 2,
    })
    assert second_page.structuredContent["data"][0]["action"] == "CREATE"
    from app.api.order import router as order_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(order_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get(
            "/orders/1/logs",
            headers={"Authorization": "Bearer " + create_access_token(1, "仓库", [])},
        )
    assert rest_response.status_code == 200
    rest_events = rest_response.json()["data"]
    mcp_events = events + second_page.structuredContent["data"]
    assert len(rest_events) == len(mcp_events) == 3
    for mcp_event, rest_event in zip(mcp_events, rest_events):
        assert mcp_event["source"] == rest_event["source"]
        assert mcp_event["created_at"] == rest_event["created_at"]
        if rest_event["source"] == "APPROVAL":
            assert mcp_event["action"] == rest_event["action"]
            assert f"第 {mcp_event['step']} 环节" in rest_event["summary"]
            assert mcp_event["status"] in rest_event["summary"]
        else:
            expected_action = (
                rest_event["action"]
                if rest_event["action"] in {"CREATE", "UPDATE", "DELETE"}
                else "OTHER"
            )
            assert mcp_event["action"] == expected_action
    assert set(events[0]) == {
        "event_id", "source", "action", "step", "status", "created_at",
    }
    combined = json.dumps(page.structuredContent) + json.dumps(second_page.structuredContent)
    for secret in (
        "PRIVATE-OPERATOR", "PRIVATE-SUMMARY", "PRIVATE-REASON",
        "PRIVATE-DETAIL", "PRIVATE-COMMENT", "13800138000",
    ):
        assert secret not in combined
    assert call(rt, "erp_order_list_order_logs", {
        "order_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    with factory() as db:
        after = (
            db.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.target_type == "order", AuditLog.target_id == "1",
            )),
            db.scalar(select(func.count(ApprovalRecord.id))),
        )
    assert before == after


def test_order_flag_dictionary_requires_order_module_and_is_read_only(factory):
    from app.services.order_service import list_order_flags

    tool_names = [ACCESS_TOOL, "erp_order_list_order_flags"]
    authorized = issue(
        factory, staff_id=2, tool_scopes=tool_names,
        permission_scopes=["module:order"],
    )["token"]
    result = call(runtime(factory, authorized), "erp_order_list_order_flags", {})
    assert result.structuredContent["data"] == list_order_flags()
    assert len(result.structuredContent["data"]) == len(list_order_flags())

    from app.api.order import router as order_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(order_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(2, "乙", [])}
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get("/orders/flags", headers=rest_headers)
    assert rest_response.status_code == 200
    rest_flags = rest_response.json()["data"]
    assert result.structuredContent["data"] == rest_flags
    assert all(set(row) == {"code", "label"} for row in rest_flags)

    unauthorized = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:purchase"],
    )["token"]
    denied = call(runtime(factory, unauthorized), "erp_order_list_order_flags", {})
    assert denied.structuredContent["code"] == "FORBIDDEN"


def test_sales_order_search_uses_company_scope_and_never_searches_contact_data(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.order import Order, OrderItem
    from app.models.platform import Logistics, Platform, Shop
    from app.models.product import Product
    from app.models.rbac import StaffRole
    from app.models.warehouse import Warehouse

    tool_names = [
        ACCESS_TOOL, "erp_order_list_orders", "erp_order_get_order",
        "erp_order_list_order_items",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    token = issue(
        factory, tool_scopes=tool_names, permission_scopes=["module:order"],
    )["token"]
    multi_company = call(runtime(factory, token), "erp_order_list_orders", {})
    assert not multi_company.isError
    assert multi_company.structuredContent["page"]["total"] == 0
    assert call(runtime(factory, token), "erp_order_get_order", {
        "order_id": 1,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(runtime(factory, token), "erp_order_list_order_items", {
        "order_id": 1,
    }).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Platform(id=1, name="测试平台", code="MCP-PLATFORM"),
            Shop(id=1, platform_id=1, name="测试店铺"),
            Warehouse(id=1, code="MCP-ORDER-WH", name="订单测试仓"),
            Logistics(id=1, name="测试物流", is_default=True, is_enabled=True),
            Product(
                id=1, name="订单测试商品", code="MCP-ORDER-PROD",
                image="/private/order-product.png",
            ),
        ])
        db.add_all([
            Order(
                id=1, order_no="MCP-SALES-ORD-001", shop_id=1,
                warehouse_id=1, logistics_id=1, status="NEW",
                audit_status="APPROVED", pay_status="PAID",
                buyer_nick="PRIVATE-BUYER", receiver_name="PRIVATE-NAME",
                receiver_phone="13800138000", receiver_address="PRIVATE-ADDRESS",
                tracking_no="PRIVATE-TRACKING", buyer_message="PRIVATE-MESSAGE",
                remark="PRIVATE-REMARK", flags="RETURN,UNKNOWN",
                order_amount=Decimal("130.00"), paid_amount=Decimal("50.00"),
                freight=Decimal("8.00"), order_time=datetime(2026, 10, 1, 9, 0),
            ),
            Order(
                id=2, order_no="MCP-SALES-ORD-002", shop_id=1,
                warehouse_id=1, status="SHIPPED", audit_status="APPROVED",
                pay_status="PAID", order_amount=Decimal("200.00"),
                order_time=datetime(2026, 10, 2, 9, 0),
            ),
            OrderItem(
                id=1, order_id=1, product_id=1, qty=Decimal("2.500"),
                price=Decimal("40.00"), amount=Decimal("100.00"),
            ),
            OrderItem(
                id=2, order_id=1, product_id=1, qty=Decimal("1.500"),
                price=Decimal("20.00"), amount=Decimal("30.00"),
            ),
        ])
        db.commit()
        before = [
            (row.id, row.order_no, row.order_amount, row.pay_status, row.flags)
            for row in db.scalars(select(Order).order_by(Order.id)).all()
        ]
        before_items = db.scalar(select(func.count(OrderItem.id)))

    rt = runtime(factory, token)
    page = call(rt, "erp_order_list_orders", {"page_size": 1})
    assert not page.isError, page.structuredContent
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert page.structuredContent["data"][0]["id"] == 2
    page_2 = call(rt, "erp_order_list_orders", {"page": 2, "page_size": 1})
    order = page_2.structuredContent["data"][0]
    assert order["order_no"] == "MCP-SALES-ORD-001"
    assert Decimal(order["total_qty"]) == Decimal("4.000")
    assert Decimal(order["order_amount"]) == Decimal("130.00")
    assert order["flags"] == ["RETURN"]
    assert not {
        "buyer_nick", "buyer_message", "seller_remark", "receiver_name",
        "receiver_phone", "receiver_address", "tracking_no", "remark",
        "logistics_company",
    } & set(order)
    detail = call(rt, "erp_order_get_order", {"order_id": 1})
    assert not detail.isError, detail.structuredContent
    assert detail.structuredContent["data"]["order_no"] == "MCP-SALES-ORD-001"
    assert Decimal(detail.structuredContent["data"]["total_qty"]) == Decimal("4.000")
    assert not {
        "receiver_name", "receiver_phone", "receiver_address", "tracking_no",
        "buyer_message", "remark", "items", "approval",
    } & set(detail.structuredContent["data"])
    items = call(rt, "erp_order_list_order_items", {
        "order_id": 1, "page_size": 1,
    })
    assert items.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    item = items.structuredContent["data"][0]
    assert item["product_name"] == "订单测试商品"
    assert Decimal(item["qty"]) == Decimal("2.500")
    assert Decimal(item["amount"]) == Decimal("100.00")
    assert "product_image" not in item and "cost_price" not in item
    items_page_2 = call(rt, "erp_order_list_order_items", {
        "order_id": 1, "page": 2, "page_size": 1,
    })
    assert items_page_2.structuredContent["page"] == {
        "page": 2, "page_size": 1, "total": 2, "has_more": False,
    }
    assert items_page_2.structuredContent["data"][0]["id"] == 2
    assert call(rt, "erp_order_list_order_items", {
        "order_id": 2,
    }).structuredContent["data"] == []
    assert call(rt, "erp_order_get_order", {
        "order_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_order_get_order", {
        "order_id": 999,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(rt, "erp_order_list_orders", {
        "keyword": "MCP-SALES-ORD-001",
    }).structuredContent["page"]["total"] == 1
    for private_search in (
        "PRIVATE-BUYER", "PRIVATE-NAME", "13800138000", "PRIVATE-TRACKING",
    ):
        assert call(rt, "erp_order_list_orders", {
            "keyword": private_search,
        }).structuredContent["page"]["total"] == 0
    assert call(rt, "erp_order_list_orders", {
        "flag": "RETURN",
    }).structuredContent["data"][0]["id"] == 1
    assert call(rt, "erp_order_list_orders", {
        "status": "SHIPPED",
    }).structuredContent["data"][0]["id"] == 2
    assert call(rt, "erp_order_list_orders", {
        "shop_id": 1, "warehouse_id": 1, "audit_status": "APPROVED",
        "pay_status": " paid ",
    }).structuredContent["page"]["total"] == 2
    assert call(rt, "erp_order_list_orders", {
        "start_time": "2026-10-01T00:00:00+08:00",
        "end_time": "2026-10-01T23:59:59+08:00",
    }).structuredContent["page"]["total"] == 1
    assert call(rt, "erp_order_list_orders", {
        "start_time": "2026-10-02T00:00:00+08:00",
        "end_time": "2026-10-01T00:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_order_list_orders", {
        "status": "INVALID",
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    from app.api.order import router as order_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(order_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "仓库", [])}
    with TestClient(rest_app) as rest_client:
        rest_filtered = rest_client.get("/orders", params={
            "shop_id": 1, "warehouse_id": 1, "audit_status": "APPROVED",
            "pay_status": " paid ", "page": 1, "page_size": 1,
        }, headers=rest_headers)
        assert rest_filtered.status_code == 200
        rest_filtered_data = rest_filtered.json()["data"]
        mcp_filtered = call(rt, "erp_order_list_orders", {
            "shop_id": 1, "warehouse_id": 1, "audit_status": "APPROVED",
            "pay_status": " paid ", "page": 1, "page_size": 1,
        }).structuredContent
        assert mcp_filtered["page"]["total"] == rest_filtered_data["total"] == 2
        assert mcp_filtered["data"][0]["id"] == rest_filtered_data["items"][0]["id"]
        assert mcp_filtered["page"]["has_more"] is True

        rest_flag = rest_client.get("/orders", params={
            "flag": "RETURN", "page": 1, "page_size": 20,
        }, headers=rest_headers)
        assert rest_flag.status_code == 200
        mcp_flag = call(rt, "erp_order_list_orders", {
            "flag": "RETURN", "page": 1, "page_size": 20,
        }).structuredContent
        assert mcp_flag["page"]["total"] == rest_flag.json()["data"]["total"] == 1
        assert mcp_flag["data"][0]["id"] == rest_flag.json()["data"]["items"][0]["id"] == 1

        rest_time = rest_client.get("/orders", params={
            "start_time": "2026-10-01T00:00:00+08:00",
            "end_time": "2026-10-01T23:59:59+08:00",
        }, headers=rest_headers)
        mcp_time = call(rt, "erp_order_list_orders", {
            "start_time": "2026-10-01T00:00:00+08:00",
            "end_time": "2026-10-01T23:59:59+08:00",
        }).structuredContent
        assert rest_time.status_code == 200
        assert mcp_time["page"]["total"] == rest_time.json()["data"]["total"] == 1
        assert mcp_time["data"][0]["id"] == rest_time.json()["data"]["items"][0]["id"] == 1

        rest_order = rest_client.get("/orders/1", headers=rest_headers)
        assert rest_order.status_code == 200
        rest_order_data = rest_order.json()["data"]
        mcp_order = detail.structuredContent["data"]
        for field in (
            "id", "order_no", "shop_id", "shop_name", "platform_name", "warehouse_id",
            "warehouse_name", "logistics_id", "logistics_name", "status", "status_label",
            "audit_status", "pay_status", "total_qty", "order_amount", "paid_amount",
            "freight", "platform_discount", "seller_discount", "weight", "order_time",
            "pay_time", "ship_time",
        ):
            if field in rest_order_data:
                if field in {
                    "total_qty", "order_amount", "paid_amount", "freight",
                    "platform_discount", "seller_discount", "weight",
                } and rest_order_data[field] is not None:
                    assert Decimal(str(mcp_order[field])) == Decimal(str(rest_order_data[field]))
                else:
                    assert mcp_order[field] == rest_order_data[field], field
        assert mcp_order["flags"] == ["RETURN"]
        assert rest_order_data["flags"] == ["RETURN", "UNKNOWN"]

        rest_item_rows = rest_order_data["items"]
        mcp_item_rows = items.structuredContent["data"] + items_page_2.structuredContent["data"]
        assert items.structuredContent["page"]["total"] == len(rest_item_rows) == len(mcp_item_rows) == 2
        rest_items_by_id = {row["id"]: row for row in rest_item_rows}
        assert set(rest_items_by_id) == {row["id"] for row in mcp_item_rows} == {1, 2}
        for mcp_item in mcp_item_rows:
            rest_item = rest_items_by_id[mcp_item["id"]]
            for field in (
                "id", "order_id", "product_id", "product_code", "product_name",
                "sku_id", "sku_name", "qty", "is_gift", "price", "amount",
            ):
                if field == "order_id":
                    assert mcp_item[field] == 1
                elif field in {"qty", "price", "amount"} and rest_item[field] is not None:
                    assert Decimal(str(mcp_item[field])) == Decimal(str(rest_item[field]))
                else:
                    assert mcp_item[field] == rest_item[field], field
        assert all("product_image" in row for row in rest_item_rows)
        assert all("product_image" not in row for row in mcp_item_rows)
        assert "/private/order-product.png" not in json.dumps(mcp_item_rows)
        assert "approval" not in mcp_order and "items" not in mcp_order

    insufficient_scope = issue(
        factory, tool_scopes=tool_names,
        permission_scopes=["module:purchase"],
    )["token"]
    denied = call(runtime(factory, insufficient_scope), "erp_order_list_orders", {})
    assert denied.structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        after = [
            (row.id, row.order_no, row.order_amount, row.pay_status, row.flags)
            for row in db.scalars(select(Order).order_by(Order.id)).all()
        ]
        assert db.scalar(select(func.count(OrderItem.id))) == before_items
    assert before == after


def test_customer_search_requires_aftersale_role_and_company_scope(factory):
    unauthorized = issue(factory, staff_id=2,
                         tool_scopes=[ACCESS_TOOL, "erp_search_customers"],
                         permission_scopes=["module:order"])["token"]
    result = call(runtime(factory, unauthorized), "erp_search_customers", {})
    assert result.isError and result.structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after_sale = Role(id=3, company_id=1, code="AFTER_SALE", name="售后")
        db.add(after_sale)
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=after_sale.id))
        db.commit()
    authorized_role = issue(factory, tool_scopes=[ACCESS_TOOL, "erp_search_customers"],
                            permission_scopes=["module:order"])["token"]
    result = call(runtime(factory, authorized_role), "erp_search_customers", {})
    assert not result.isError
    assert result.structuredContent["page"]["total"] == 0


def test_customer_queries_match_rest_for_same_aftersale_identity_and_pages(factory):
    from sqlalchemy import delete, func
    from app.api.party import router as party_router
    from app.core.data_scope import clear_scope_policy
    from app.models.party import Customer

    tools = [ACCESS_TOOL, "erp_search_customers", "erp_get_customer"]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="AFTER_SALE", name="售后"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Customer(
                id=1, code="MCP-CUSTOMER-A", name="客户甲",
                contact="私有联系人", phone="13812345678",
                address="私有客户详细地址", email="private@example.test",
                remark="私有客户备注", is_active=False,
            ),
            Customer(
                id=2, code="MCP-CUSTOMER-B", name="客户乙",
                phone="13987654321", address=None, is_active=True,
            ),
        ])
        db.commit()
        before = db.scalar(select(func.count(Customer.id)))

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:order"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(party_router)

    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app.dependency_overrides[get_db] = sessions
    headers = {"Authorization": "Bearer " + create_access_token(1, "售后员", [])}
    with TestClient(app) as client:
        rest_pages = []
        mcp_pages = []
        for page_number in (1, 2):
            response = client.get("/customers", params={
                "keyword": "MCP-CUSTOMER", "page": page_number, "page_size": 1,
            }, headers=headers)
            assert response.status_code == 200, response.text
            rest_page = response.json()["data"]
            rest_pages.extend(rest_page["items"])
            result = call(rt, "erp_search_customers", {
                "keyword": "MCP-CUSTOMER", "page": page_number, "page_size": 1,
            })
            assert not result.isError, result.structuredContent
            mcp_pages.extend(result.structuredContent["data"])
            assert result.structuredContent["page"]["total"] == rest_page["total"]
            assert result.structuredContent["page"]["has_more"] == (page_number == 1)

        assert [row["id"] for row in mcp_pages] == [row["id"] for row in rest_pages]
        for mcp_row, rest_row in zip(mcp_pages, rest_pages):
            for field in ("id", "code", "name", "is_active"):
                assert mcp_row[field] == rest_row[field], field
            assert mcp_row["phone"] == (
                "****" + rest_row["phone"][-4:] if rest_row["phone"] else None
            )
            assert mcp_row["address"] == (
                "已脱敏" if rest_row["address"] else rest_row["address"]
            )
            assert not {"contact", "fax", "email", "settlement_type", "remark", "accounts"} & set(mcp_row)

        rest_filtered = client.get("/customers", params={
            "keyword": "MCP-CUSTOMER", "is_active": "false",
        }, headers=headers)
        assert rest_filtered.status_code == 200
        mcp_filtered = call(rt, "erp_search_customers", {
            "keyword": "MCP-CUSTOMER", "is_active": False,
        }).structuredContent
        assert mcp_filtered["page"]["total"] == rest_filtered.json()["data"]["total"] == 1
        assert mcp_filtered["data"][0]["id"] == rest_filtered.json()["data"]["items"][0]["id"] == 1

        rest_detail_response = client.get("/customers/1", headers=headers)
        assert rest_detail_response.status_code == 200
        rest_detail = rest_detail_response.json()["data"]
        mcp_detail = call(rt, "erp_get_customer", {
            "customer_id": 1,
        }).structuredContent["data"]
        for field in ("id", "code", "name", "is_active"):
            assert mcp_detail[field] == rest_detail[field], field
        assert mcp_detail["phone"] == "****" + rest_detail["phone"][-4:]
        assert mcp_detail["address"] == "已脱敏"

    with factory() as db:
        assert db.scalar(select(func.count(Customer.id))) == before


def test_credential_rest_auth_and_ownership(factory):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + create_access_token(1, "甲", [])}
    assert client.get("/mcp/credentials").status_code == 401
    created = client.post("/mcp/credentials", headers=headers,
                          json={"name": "测试", "permission_scopes": ["module:purchase"]})
    assert created.status_code == 200
    assert created.headers["Cache-Control"] == "no-store"
    data = created.json()["data"]
    assert "token" in data
    assert "token" not in client.get("/mcp/credentials", headers=headers).json()["data"][0]
    other = {"Authorization": "Bearer " + create_access_token(2, "乙", [])}
    assert client.post(f"/mcp/credentials/{data['id']}/revoke", headers=other).status_code == 404
    assert client.post(f"/mcp/credentials/{data['id']}/revoke", headers=headers).status_code == 200


def test_approval_record_tool_is_paged_projected_and_parent_scoped(factory):
    from datetime import datetime
    from sqlalchemy import func
    from app.models.approval import ApprovalRecord
    from app.models.party import Supplier
    from app.models.purchase import PurchaseOrder

    with factory() as db:
        # 保留第二家公司；审批记录及其父单仍只按当前凭据公司和单据范围可见。
        db.add(Staff(id=3, company_id=1, name="其他采购员"))
        db.add(Supplier(id=1, code="APP-SUP", name="审批测试供应商"))
        db.add_all([
            PurchaseOrder(id=1, order_no="APP-OWN", supplier_id=1,
                          purchaser_id=1, created_by=1, total_qty=2,
                          total_amount=10, arrived_qty=0),
            PurchaseOrder(id=2, order_no="APP-OTHER", supplier_id=1,
                          purchaser_id=3, created_by=3, total_qty=1,
                          total_amount=5, arrived_qty=0),
        ])
        db.add_all([
            ApprovalRecord(id=1, doc_type="purchase_order", doc_id=1,
                           step=1, action="SUBMIT", status="APPROVED",
                           approver_id=1, comment="秘密审批意见", reason="秘密原因",
                           audit_time=datetime(2025, 1, 1, 4, 0)),
            ApprovalRecord(id=2, doc_type="purchase_order", doc_id=1,
                           step=2, action="APPROVE", status="APPROVED",
                           approver_id=1, comment="第二条意见", reason="第二条原因"),
            ApprovalRecord(id=3, doc_type="purchase_order", doc_id=2,
                           step=1, action="SUBMIT", status="APPROVED",
                           approver_id=3, comment="他人单据意见"),
        ])
        db.commit()

    tool = "erp_get_approval_records"
    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, tool],
        permission_scopes=["module:approval"],
    )["token"]
    rt = runtime(factory, token)
    with factory() as db:
        before_counts = (
            db.scalar(select(func.count(ApprovalRecord.id))),
            db.scalar(select(func.count(PurchaseOrder.id))),
        )

    first = call(rt, tool, {
        "doc_type": "purchase_order", "doc_id": 1, "page_size": 1,
    })
    assert first.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert first.structuredContent["data"]["records"][0]["id"] == 1
    assert first.structuredContent["data"]["records"][0]["audit_time"].endswith("+08:00")
    assert first.structuredContent["data"]["records"][0]["audit_time"].startswith("2025-01-01T12:00:00")
    assert not {"approver_id", "comment", "reason"} & set(
        first.structuredContent["data"]["records"][0]
    )
    assert "秘密审批意见" not in first.model_dump_json()
    second = call(rt, tool, {
        "doc_type": "purchase_order", "doc_id": 1, "page": 2, "page_size": 1,
    })
    assert second.structuredContent["data"]["records"][0]["id"] == 2
    assert second.structuredContent["page"]["has_more"] is False

    # 合法且可见的单据可返回空留痕；不可见单据不能凭审批记录探测。
    empty = call(rt, tool, {"doc_type": "purchase_order", "doc_id": 1, "page": 3})
    assert empty.structuredContent["data"]["records"] == []
    hidden = call(rt, tool, {"doc_type": "purchase_order", "doc_id": 2})
    assert hidden.isError
    assert call(rt, tool, {"doc_type": "unsupported", "doc_id": 1}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, tool, {"doc_type": "purchase_order", "doc_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, tool, {"doc_type": "purchase_order", "doc_id": 1, "page_size": 101}).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        after_counts = (
            db.scalar(select(func.count(ApprovalRecord.id))),
            db.scalar(select(func.count(PurchaseOrder.id))),
        )
    assert after_counts == before_counts


def test_approval_record_tool_scope_matrix_matches_rest_and_approval_exception(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.approval import router as approval_router
    from app.core.data_scope import clear_scope_policy
    from app.models.approval import ApprovalRecord
    from app.models.org import Department
    from app.models.party import Supplier
    from app.models.purchase import PurchaseOrder
    from app.models.rbac import DataScope

    tool = "erp_get_approval_records"
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Department(id=1, company_id=1, name="审批范围一部"),
            Department(id=2, company_id=1, name="审批范围二部"),
            Staff(id=3, company_id=1, department_id=2, name="跨部门制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门制单人"),
            Supplier(id=1, code="APP-SCOPE-SUP", name="审批范围供应商"),
            PurchaseOrder(
                id=1, order_no="APP-SCOPE-SELF", supplier_id=1,
                purchaser_id=1, created_by=1, total_qty=Decimal("1"),
                total_amount=Decimal("10"), arrived_qty=Decimal("0"),
            ),
            PurchaseOrder(
                id=2, order_no="APP-SCOPE-OTHER-DEPT", supplier_id=1,
                purchaser_id=3, created_by=3, total_qty=Decimal("1"),
                total_amount=Decimal("10"), arrived_qty=Decimal("0"),
            ),
            PurchaseOrder(
                id=3, order_no="APP-SCOPE-SAME-DEPT", supplier_id=1,
                purchaser_id=4, created_by=4, total_qty=Decimal("1"),
                total_amount=Decimal("10"), arrived_qty=Decimal("0"),
            ),
            PurchaseOrder(
                id=4, order_no="APP-SCOPE-APPROVER-EXCEPTION", supplier_id=1,
                purchaser_id=3, created_by=3, total_qty=Decimal("1"),
                total_amount=Decimal("10"), arrived_qty=Decimal("0"),
            ),
            DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1),
        ])
        db.get(Staff, 1).department_id = 1
        db.add_all([
            ApprovalRecord(
                id=1, doc_type="purchase_order", doc_id=1, step=1,
                action="SUBMIT", status="APPROVED", approver_id=1,
            ),
            ApprovalRecord(
                id=2, doc_type="purchase_order", doc_id=1, step=2,
                action="APPROVE", status="APPROVED", approver_id=1,
            ),
            ApprovalRecord(
                id=3, doc_type="purchase_order", doc_id=2, step=1,
                action="APPROVE", status="APPROVED", approver_id=3,
            ),
            ApprovalRecord(
                id=4, doc_type="purchase_order", doc_id=3, step=1,
                action="APPROVE", status="APPROVED", approver_id=4,
            ),
            ApprovalRecord(
                id=5, doc_type="purchase_order", doc_id=4, step=1,
                action="APPROVE", status="APPROVED", approver_id=1,
            ),
        ])
        db.commit()
        before = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(ApprovalRecord.id))),
        )

    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, tool],
        permission_scopes=["module:approval"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(approval_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    headers = {
        "Authorization": "Bearer " + create_access_token(1, "审批范围对照", [])
    }
    expected_by_scope = {
        "SELF": {1, 4},
        "DEPARTMENT": {1, 3, 4},
        "COMPANY": {1, 2, 3, 4},
    }

    with TestClient(rest_app) as rest_client:
        for scope, visible_ids in expected_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            for order_id in (1, 2, 3, 4):
                rest_response = rest_client.get(
                    f"/approval/records/purchase_order/{order_id}",
                    headers=headers,
                )
                mcp_response = call(rt, tool, {
                    "doc_type": "purchase_order",
                    "doc_id": order_id,
                    "page_size": 1,
                })
                expected_visible = order_id in visible_ids
                assert (rest_response.status_code == 200) is expected_visible, (
                    scope, order_id, rest_response.status_code,
                )
                assert (not mcp_response.isError) is expected_visible, (
                    scope, order_id, mcp_response.structuredContent,
                )
                if not expected_visible:
                    continue

                rest_record_ids = {
                    row["id"] for row in rest_response.json()["data"]["records"]
                }
                mcp_record_ids = set()
                for page_number in range(1, 4):
                    page = call(rt, tool, {
                        "doc_type": "purchase_order",
                        "doc_id": order_id,
                        "page": page_number,
                        "page_size": 1,
                    }).structuredContent
                    mcp_record_ids.update(
                        row["id"] for row in page["data"]["records"]
                    )
                    if not page["page"]["has_more"]:
                        break
                assert mcp_record_ids == rest_record_ids, (scope, order_id)

    with factory() as db:
        after = (
            db.scalar(select(func.count(PurchaseOrder.id))),
            db.scalar(select(func.count(ApprovalRecord.id))),
        )
    assert after == before


def test_purchase_return_tools_cover_search_detail_children_and_money_scope(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.finance import Income
    from app.models.org import Department
    from app.models.party import Supplier
    from app.models.product import Product, ProductSku
    from app.models.purchase import PurchaseReturn, PurchaseReturnItem
    from app.models.rbac import DataScope
    from app.models.stock import StockOut
    from app.models.warehouse import Warehouse

    tools = [
        ACCESS_TOOL, "erp_search_purchase_returns", "erp_get_purchase_return",
        "erp_list_purchase_return_items", "erp_get_purchase_return_stock_out",
        "erp_get_purchase_return_refund",
    ]
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Department(id=1, company_id=1, name="退货范围一部"),
            Department(id=2, company_id=1, name="退货范围二部"),
            Staff(id=3, company_id=1, department_id=2, name="跨部门采购员"),
            Staff(id=4, company_id=1, department_id=1, name="同部门采购员"),
        ])
        db.add_all([
            Supplier(id=1, code="RET-SUP", name="退货供应商", phone="13812345678",
                     address="不外发的详细地址", remark="不外发备注"),
            Warehouse(id=1, code="RET-WH", name="退货仓", warehouse_type="FINISHED"),
            Product(id=1, code="RET-PROD", name="退货商品", cost_price=Decimal("99.00")),
            ProductSku(id=1, product_id=1, spec_name="标准规格", spec_code="RET-SKU",
                       cost_price=Decimal("88.00")),
            PurchaseReturn(id=1, return_no="RET-OWN", supplier_id=1, warehouse_id=1,
                           created_by=1, return_reason="不外发原因", description="不外发描述",
                           total_qty=Decimal("2"), returned_qty=Decimal("1"),
                           refund_amount=Decimal("20"), status="APPROVED",
                           audit_status="APPROVED"),
            PurchaseReturn(id=2, return_no="RET-OTHER", supplier_id=1, warehouse_id=1,
                           created_by=3, total_qty=Decimal("1"), refund_amount=Decimal("7")),
            PurchaseReturn(id=3, return_no="NO-LINK", supplier_id=1, warehouse_id=1,
                           created_by=1, total_qty=Decimal("1"), refund_amount=Decimal("0")),
            PurchaseReturn(id=4, return_no="DEPT-RET-4", supplier_id=1, warehouse_id=1,
                           created_by=4, total_qty=Decimal("1"), refund_amount=Decimal("4")),
            PurchaseReturnItem(id=1, return_id=1, product_id=1, sku_id=1,
                               return_qty=Decimal("2"), unit_price=Decimal("10"),
                               amount=Decimal("20")),
            PurchaseReturnItem(id=2, return_id=4, product_id=1, sku_id=1,
                               return_qty=Decimal("1"), unit_price=Decimal("4"),
                               amount=Decimal("4")),
            StockOut(id=1, stock_out_no="RET-OUT", warehouse_id=1,
                     stock_out_type="PURCHASE_RETURN", purchase_return_id=1,
                     total_qty=Decimal("2"), remark="出库备注不外发"),
            Income(id=1, income_no="RET-REFUND", supplier_id=1,
                   income_type="PURCHASE_RETURN_REFUND", amount=Decimal("20"),
                   description="采购退货退款:RET-OWN", voucher_no="凭证不外发"),
        ])
        db.get(Staff, 1).department_id = 1
        db.add(DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1))
        db.commit()

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    with factory() as db:
        before = tuple(db.scalar(select(func.count(model.id))) for model in (
            PurchaseReturn, PurchaseReturnItem, StockOut, Income,
        ))

    search = call(rt, "erp_search_purchase_returns", {"keyword": "RET-"})
    assert search.structuredContent["page"]["total"] == 1
    assert search.structuredContent["data"][0]["return_no"] == "RET-OWN"
    assert search.structuredContent["data"][0]["refund_amount"] == "20.00"
    detail = call(rt, "erp_get_purchase_return", {"return_id": 1})
    assert detail.structuredContent["data"]["return_no"] == "RET-OWN"
    assert "return_reason" not in detail.structuredContent["data"]
    assert "stock_out" not in detail.structuredContent["data"]
    items = call(rt, "erp_list_purchase_return_items", {"return_id": 1, "page_size": 1})
    assert items.structuredContent["data"][0]["product_name"] == "退货商品"
    assert items.structuredContent["data"][0]["return_qty"] == "2.000"
    assert items.structuredContent["data"][0]["amount"] == "20.00"
    stock_out = call(rt, "erp_get_purchase_return_stock_out", {"return_id": 1})
    assert stock_out.structuredContent["data"]["stock_out_no"] == "RET-OUT"
    assert "remark" not in stock_out.structuredContent["data"]
    refund = call(rt, "erp_get_purchase_return_refund", {"return_id": 1})
    assert refund.structuredContent["data"]["income_no"] == "RET-REFUND"
    assert "description" not in refund.structuredContent["data"]
    assert "voucher_no" not in refund.structuredContent["data"]
    assert call(rt, "erp_get_purchase_return_stock_out", {"return_id": 3}).structuredContent["data"] is None
    assert call(rt, "erp_get_purchase_return_refund", {"return_id": 3}).structuredContent["data"] is None

    from app.api.purchase_return import router as purchase_return_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(purchase_return_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "采购员", [])}
    with TestClient(rest_app) as rest_client:
        rest_search = rest_client.get("/purchase-returns", params={
            "keyword": "RET-", "page": 1, "page_size": 20,
        }, headers=rest_headers)
        assert rest_search.status_code == 200
        rest_search_data = rest_search.json()["data"]
        assert search.structuredContent["page"]["total"] == rest_search_data["total"] == 1
        assert search.structuredContent["data"][0]["id"] == rest_search_data["items"][0]["id"]
        assert search.structuredContent["data"][0]["return_no"] == rest_search_data["items"][0]["return_no"]
        assert Decimal(search.structuredContent["data"][0]["refund_amount"]) == Decimal(
            str(rest_search_data["items"][0]["refund_amount"])
        )

        rest_detail_response = rest_client.get("/purchase-returns/1", headers=rest_headers)
        assert rest_detail_response.status_code == 200
        rest_detail = rest_detail_response.json()["data"]
        mcp_detail = detail.structuredContent["data"]
        for field in (
            "id", "return_no", "supplier_id", "supplier_name", "order_id", "order_no",
            "warehouse_id", "warehouse_name", "auto_out", "refund_status", "status",
            "audit_status", "created_at", "updated_at",
        ):
            if field in rest_detail:
                assert mcp_detail[field] == rest_detail[field], field
        assert Decimal(mcp_detail["total_qty"]) == Decimal(str(rest_detail["total_qty"]))
        assert Decimal(mcp_detail["returned_qty"]) == Decimal(str(rest_detail["returned_qty"]))
        assert Decimal(mcp_detail["refund_amount"]) == Decimal(str(rest_detail["refund_amount"]))
        assert "return_reason" not in mcp_detail and "description" not in mcp_detail
        assert "items" not in mcp_detail and "approval" not in mcp_detail

        rest_items = rest_detail["items"]
        assert items.structuredContent["page"]["total"] == len(rest_items) == 1
        for mcp_item, rest_item in zip(items.structuredContent["data"], rest_items):
            for field in ("id", "product_id", "product_name", "product_code", "art_no", "sku_id", "spec_name", "spec_code", "unit_id", "unit_name"):
                if field in rest_item:
                    assert mcp_item[field] == rest_item[field], field
            for field in ("return_qty", "unit_price", "amount"):
                if field in rest_item and rest_item[field] is not None:
                    assert Decimal(mcp_item[field]) == Decimal(str(rest_item[field])), field

        rest_stock_out = rest_client.get("/purchase-returns/1/stock-out", headers=rest_headers)
        rest_refund = rest_client.get("/purchase-returns/1/refund", headers=rest_headers)
        assert rest_stock_out.status_code == rest_refund.status_code == 200
        rest_stock_out_data = rest_stock_out.json()["data"]
        rest_refund_data = rest_refund.json()["data"]
        for field in (
            "id", "stock_out_no", "warehouse_id", "stock_out_type", "status",
            "audit_status", "off_shelf_status", "audit_time", "created_at",
        ):
            if field in rest_stock_out_data:
                assert stock_out.structuredContent["data"][field] == rest_stock_out_data[field], field
        for field in ("id", "income_no", "supplier_id", "income_type", "status", "audit_status", "created_at"):
            if field in rest_refund_data:
                assert refund.structuredContent["data"][field] == rest_refund_data[field], field
        assert Decimal(refund.structuredContent["data"]["amount"]) == Decimal(
            str(rest_refund_data["amount"])
        )

    expected_ids_by_scope = {
        "SELF": {1, 3},
        "DEPARTMENT": {1, 3, 4},
        "COMPANY": {1, 2, 3, 4},
    }
    with TestClient(rest_app) as scoped_rest_client:
        for scope, expected_ids in expected_ids_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            mcp_list = call(rt, "erp_search_purchase_returns", {
                "page_size": 20,
            }).structuredContent
            rest_list = scoped_rest_client.get(
                "/purchase-returns", params={"page": 1, "page_size": 20},
                headers=rest_headers,
            )
            assert rest_list.status_code == 200
            rest_list_data = rest_list.json()["data"]
            assert {row["id"] for row in mcp_list["data"]} == {
                row["id"] for row in rest_list_data["items"]
            } == expected_ids
            assert mcp_list["page"]["total"] == rest_list_data["total"] == len(expected_ids)

            for return_id in (1, 2, 3, 4):
                should_be_visible = (
                    return_id in {1, 3}
                    or scope == "COMPANY"
                    or (scope == "DEPARTMENT" and return_id == 4)
                )
                mcp_detail = call(rt, "erp_get_purchase_return", {
                    "return_id": return_id,
                })
                rest_detail = scoped_rest_client.get(
                    f"/purchase-returns/{return_id}", headers=rest_headers,
                )
                mcp_items = call(rt, "erp_list_purchase_return_items", {
                    "return_id": return_id, "page_size": 20,
                })
                if should_be_visible:
                    assert not mcp_detail.isError, (scope, return_id)
                    assert rest_detail.status_code == 200, (scope, return_id)
                    assert not mcp_items.isError, (scope, return_id)
                    rest_item_ids = {
                        item["id"] for item in rest_detail.json()["data"]["items"]
                    }
                    assert {
                        item["id"] for item in mcp_items.structuredContent["data"]
                    } == rest_item_ids, (scope, return_id)
                else:
                    assert mcp_detail.structuredContent["code"] == "FORBIDDEN", (
                        scope, return_id,
                    )
                    assert rest_detail.status_code == 404, (scope, return_id)
                    assert mcp_items.structuredContent["code"] == "FORBIDDEN", (
                        scope, return_id,
                    )

    with factory() as db:
        db.get(DataScope, 1).scope_type = "SELF"
        db.commit()

    assert "不外发" not in search.model_dump_json() + detail.model_dump_json()
    assert call(rt, "erp_get_purchase_return", {"return_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_search_purchase_returns", {"status": "UNKNOWN"}).structuredContent["code"] == "INVALID_ARGUMENT"
    hidden = call(rt, "erp_get_purchase_return", {"return_id": 2})
    assert hidden.isError
    assert call(rt, "erp_get_purchase_return", {"return_id": 999}).isError

    # 采购员的只读凭据只有仓库范围时，不能把采购金额权限带入结果。
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.commit()
    stock_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:stock"],
    )["token"]
    stock_result = call(runtime(factory, stock_token), "erp_search_purchase_returns", {})
    assert stock_result.structuredContent["data"][0].get("refund_amount") is None
    stock_item = call(runtime(factory, stock_token), "erp_list_purchase_return_items", {"return_id": 1})
    assert "unit_price" not in stock_item.structuredContent["data"][0]
    assert "amount" not in stock_item.structuredContent["data"][0]

    with factory() as db:
        after = tuple(db.scalar(select(func.count(model.id))) for model in (
            PurchaseReturn, PurchaseReturnItem, StockOut, Income,
        ))
    assert after == before


def test_assistant_admin_and_self_usage_are_scoped_paged_and_redacted(factory, monkeypatch):
    from datetime import datetime, timezone
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.api.assistant import router as assistant_router
    from app.core.data_scope import clear_scope_policy
    from app.core.config import get_settings
    from app.models.assistant_governance import AssistantConfig, AssistantUsage
    from app.services import assistant_governance
    from cryptography.fernet import Fernet

    encryption_key = Fernet.generate_key()
    fernet = Fernet(encryption_key)
    monkeypatch.setattr(
        assistant_governance, "get_settings",
        lambda: get_settings().model_copy(update={
            "assistant_config_encryption_key": encryption_key.decode("ascii"),
            "assistant_enabled": True,
            "assistant_provider": "deepseek",
        }),
    )

    tools = [
        ACCESS_TOOL, "erp_assistant_get_admin_config",
        "erp_assistant_get_admin_usage", "erp_assistant_get_my_usage",
    ]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="ADMIN", name="管理员"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add(AssistantConfig(
            id=1, company_id=1, enabled=True, provider="deepseek",
            model="PRIVATE-MODEL",
            api_key_ciphertext=fernet.encrypt(b"PRIVATE-API-KEY").decode("ascii"),
            api_key_overrides_environment=True, requests_per_minute=17,
            concurrent_requests_per_user=2, daily_requests_per_user=30,
            monthly_budget=Decimal("99.50"),
        ))
        db.add_all([
            AssistantUsage(
                company_id=1, staff_id=1, request_id="usage-admin-1",
                provider="deepseek", model="PRIVATE-MODEL",
                status="completed", failure_category="PRIVATE-FAILURE",
                prompt_tokens=11, completion_tokens=7, total_tokens=18,
                latency_ms=321, tool_names=["PRIVATE-TOOL"], cost_status="known",
                reserved_cost=Decimal("0.01"), estimated_cost=Decimal("0.02"),
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            AssistantUsage(
                company_id=2, staff_id=2, request_id="usage-other-company",
                provider="deepseek", model="PRIVATE-MODEL", status="completed",
                prompt_tokens=999, total_tokens=999, latency_ms=999,
                tool_names=["PRIVATE-TOOL"], cost_status="unknown",
                reserved_cost=Decimal("1.00"),
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:settings"],
    )["token"]
    rt = runtime(factory, token)
    config_result = call(rt, "erp_assistant_get_admin_config", {})
    assert not config_result.isError, config_result.structuredContent
    config = config_result.structuredContent["data"]
    assert config == {
        "enabled": True, "api_key_configured": True,
        "requests_per_minute": 17, "concurrent_requests_per_user": 2,
        "daily_requests_per_user": 30, "monthly_budget": "99.50000000",
    }
    admin_usage = call(rt, "erp_assistant_get_admin_usage", {
        "days": 90, "page_size": 1,
    })
    assert admin_usage.structuredContent["page"]["total"] == 1
    assert admin_usage.structuredContent["data"]["summary"]["request_count"] == 1
    row = admin_usage.structuredContent["data"]["items"][0]
    assert row["staff_id"] == 1 and row["staff_name"] == "甲"
    assert row["total_tokens"] == 18 and row["estimated_cost"] == "0.02000000"
    combined = config | admin_usage.structuredContent["data"]
    serialized = json.dumps(combined, ensure_ascii=False)
    for secret in ("PRIVATE-CIPHERTEXT", "PRIVATE-API-KEY", "PRIVATE-MODEL", "PRIVATE-TOOL", "PRIVATE-FAILURE", "usage-other-company"):
        assert secret not in serialized

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(assistant_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    admin_headers = {"Authorization": "Bearer " + create_access_token(1, "管理员", [])}
    with TestClient(rest_app) as rest_client:
        rest_config_response = rest_client.get(
            "/assistant/admin/config", headers=admin_headers,
        )
        assert rest_config_response.status_code == 200
        rest_config = rest_config_response.json()["data"]
        for field, value in config.items():
            assert rest_config[field] == value

        rest_admin_usage_response = rest_client.get(
            "/assistant/admin/usage", params={"days": 90}, headers=admin_headers,
        )
        assert rest_admin_usage_response.status_code == 200
        rest_admin_usage = rest_admin_usage_response.json()["data"]
        mcp_admin_data = admin_usage.structuredContent["data"]
        rest_summary = rest_admin_usage["summary"]
        for field in ("request_count", "known_prompt_tokens", "known_completion_tokens",
                      "unknown_token_count", "unknown_cost_count"):
            assert mcp_admin_data["summary"][field] == rest_summary[field]
        for field in ("known_cost", "monthly_budget", "monthly_budget_committed"):
            assert Decimal(mcp_admin_data["summary"][field]) == Decimal(str(rest_summary[field]))
        rest_usage_rows = {row["request_id"]: row for row in rest_admin_usage["items"]}
        mcp_usage_row = mcp_admin_data["items"][0]
        rest_usage_row = rest_usage_rows[mcp_usage_row["request_id"]]
        for field in ("request_id", "staff_id", "staff_name", "status", "prompt_tokens",
                      "completion_tokens", "total_tokens", "latency_ms", "cost_status",
                      "started_at", "completed_at"):
            assert mcp_usage_row[field] == rest_usage_row[field]
        assert Decimal(mcp_usage_row["estimated_cost"]) == Decimal(
            str(rest_usage_row["estimated_cost"])
        )

    with factory() as db:
        db.delete(db.get(AssistantConfig, 1))
        db.commit()
    fallback_config = call(rt, "erp_assistant_get_admin_config", {}).structuredContent["data"]
    assert fallback_config["requests_per_minute"] == 10
    assert fallback_config["concurrent_requests_per_user"] == 1
    assert fallback_config["daily_requests_per_user"] is None
    assert fallback_config["monthly_budget"] is None
    assert isinstance(fallback_config["api_key_configured"], bool)
    own_token = issue(
        factory, staff_id=2,
        tool_scopes=[ACCESS_TOOL, "erp_assistant_get_my_usage"],
        permission_scopes=["module:stock"],
    )["token"]
    own = call(runtime(factory, own_token), "erp_assistant_get_my_usage", {
        "days": 90, "page_size": 1,
    })
    assert own.structuredContent["page"]["total"] == 1
    assert own.structuredContent["data"]["items"][0]["request_id"] == "usage-other-company"
    assert "staff_id" not in own.structuredContent["data"]["items"][0]
    own_headers = {"Authorization": "Bearer " + create_access_token(2, "仓库", [])}
    with TestClient(rest_app) as rest_client:
        rest_own_response = rest_client.get(
            "/assistant/usage/me", params={"days": 90}, headers=own_headers,
        )
        assert rest_own_response.status_code == 200
        rest_own = rest_own_response.json()["data"]
        mcp_own = own.structuredContent["data"]
        for field in ("request_count", "known_prompt_tokens", "known_completion_tokens",
                      "unknown_token_count", "known_cost", "unknown_cost_count"):
            if field == "known_cost":
                assert Decimal(mcp_own["summary"][field]) == Decimal(
                    str(rest_own["summary"][field])
                )
            else:
                assert mcp_own["summary"][field] == rest_own["summary"][field]
        assert mcp_own["limits"] == rest_own["limits"]
        mcp_own_row = mcp_own["items"][0]
        rest_own_row = rest_own["items"][0]
        for field in ("request_id", "status", "prompt_tokens", "completion_tokens",
                      "total_tokens", "latency_ms", "cost_status", "started_at",
                      "completed_at"):
            assert mcp_own_row[field] == rest_own_row[field]
        if mcp_own_row["estimated_cost"] is None:
            assert rest_own_row["estimated_cost"] is None
        else:
            assert Decimal(mcp_own_row["estimated_cost"]) == Decimal(
                str(rest_own_row["estimated_cost"])
            )
    assert call(rt, "erp_assistant_get_admin_usage", {
        "days": 91,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        db.execute(delete(StaffRole).where(
            StaffRole.staff_id == 1, StaffRole.role_id == 3,
        ))
        db.commit()
    assert call(rt, "erp_assistant_get_admin_config", {}).structuredContent["code"] == "FORBIDDEN"


def test_ip_rule_queries_are_admin_company_scoped_and_hide_remarks(factory):
    from sqlalchemy import delete
    from app.api.company import router as company_router
    from app.core.data_scope import clear_scope_policy
    from app.models.rbac import IpRule

    tools = [ACCESS_TOOL, "erp_company_list_ip_rules", "erp_company_get_ip_rule"]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="ADMIN", name="管理员"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add_all([
            IpRule(id=1, company_id=1, rule_type="ALLOW", ip_address="10.0.0.1", remark="PRIVATE-REMARK"),
            IpRule(id=2, company_id=1, rule_type="DENY", ip_address="10.0.0.2", remark="OTHER-PRIVATE"),
            IpRule(id=3, company_id=2, rule_type="ALLOW", ip_address="192.0.2.5", remark="COMPANY-2"),
        ])
        db.commit()
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:settings"],
    )["token"]
    rt = runtime(factory, token)

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(company_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {
        "Authorization": "Bearer " + create_access_token(1, "管理员", []),
    }
    with TestClient(rest_app) as rest_client:
        rest_list = rest_client.get("/ip-rules", headers=rest_headers)
        assert rest_list.status_code == 200
        rest_rows = {row["id"]: row for row in rest_list.json()["data"]}
        mcp_rows = {}
        for page_number in (1, 2):
            mcp_page = call(rt, "erp_company_list_ip_rules", {
                "page": page_number, "page_size": 1,
            }).structuredContent
            assert mcp_page["page"]["total"] == len(rest_rows) == 2
            mcp_rows.update({row["id"]: row for row in mcp_page["data"]})
        assert set(mcp_rows) == set(rest_rows)
        for rule_id, mcp_row in mcp_rows.items():
            for field in ("id", "rule_type", "ip_address"):
                assert mcp_row[field] == rest_rows[rule_id][field]
            assert "remark" not in mcp_row

        rest_detail = rest_client.get("/ip-rules/1", headers=rest_headers)
        assert rest_detail.status_code == 200
        mcp_detail = call(rt, "erp_company_get_ip_rule", {
            "rule_id": 1,
        }).structuredContent["data"]
        for field in ("id", "rule_type", "ip_address"):
            assert mcp_detail[field] == rest_detail.json()["data"][field]
        assert "remark" not in mcp_detail
        assert rest_client.get("/ip-rules/3", headers=rest_headers).status_code == 404
        assert call(rt, "erp_company_get_ip_rule", {
            "rule_id": 3,
        }).structuredContent["code"] == "FORBIDDEN"

    page = call(rt, "erp_company_list_ip_rules", {"page_size": 1})
    assert page.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert page.structuredContent["data"][0]["ip_address"] == "10.0.0.2"
    detail = call(rt, "erp_company_get_ip_rule", {"rule_id": 1})
    assert detail.structuredContent["data"]["rule_type"] == "ALLOW"
    assert "remark" not in detail.structuredContent["data"]
    assert call(rt, "erp_company_get_ip_rule", {"rule_id": 3}).structuredContent["code"] == "FORBIDDEN"
    assert "PRIVATE" not in page.model_dump_json() + detail.model_dump_json()

    with factory() as db:
        db.execute(delete(StaffRole).where(
            StaffRole.staff_id == 1, StaffRole.role_id == 3,
        ))
        db.commit()
    assert call(rt, "erp_company_list_ip_rules", {}).structuredContent["code"] == "FORBIDDEN"


def test_dashboard_tool_returns_only_company_scoped_aggregates(factory):
    from sqlalchemy import delete, func
    from app.models.stock import StockWarning

    tools = [ACCESS_TOOL, "erp_dashboard_get_dashboard_overview"]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:dashboard"],
    )["token"]
    rt = runtime(factory, token)
    initial = call(rt, "erp_dashboard_get_dashboard_overview", {})
    assert not initial.isError
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        before = db.scalar(select(func.count(StockWarning.id)))
        db.commit()

    result = call(rt, "erp_dashboard_get_dashboard_overview", {
        "trend_period": "week",
    })
    data = result.structuredContent["data"]
    assert set(data) == {"as_of", "metrics", "sales_trend"}
    assert data["sales_trend"]["period"] == "week"
    assert len(data["sales_trend"]["items"]) <= 12
    assert {row["key"] for row in data["metrics"]} == {
        "today_order_amount", "today_order_count", "stock_warning_count", "customer_growth",
    }
    serialized = result.model_dump_json()
    for forbidden in ("recent_orders", "hot_products", "receiver_name", "buyer_nick", "shop_id"):
        assert forbidden not in serialized
    with factory() as db:
        assert db.scalar(select(func.count(StockWarning.id))) == before
    assert call(rt, "erp_dashboard_get_dashboard_overview", {
        "trend_period": "year",
    }).structuredContent["code"] == "INVALID_ARGUMENT"


def test_dashboard_tool_matches_rest_metrics_and_trend(factory, monkeypatch):
    from datetime import timedelta
    from decimal import Decimal
    from sqlalchemy import delete
    from app.api import dashboard as dashboard_api
    from app.api.dashboard import router as dashboard_router
    from app.core.data_scope import clear_scope_policy
    from app.models.order import Order
    from app.models.party import Customer
    from app.models.platform import Platform, Shop
    from app.models.product import Product
    from app.models.warehouse import Location, StockLocation, Warehouse
    from app.services import metrics_service

    class EmptyCache:
        def get(self, _key):
            return None

        def set(self, _key, _value, ex):
            assert ex == 15

    monkeypatch.setattr(dashboard_api, "get_redis", lambda: EmptyCache())
    today = metrics_service._business_today()
    day_start, _day_end = metrics_service._local_day_bounds(today)
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Platform(id=1, name="看板平台", code="DASHBOARD"),
            Product(
                id=1, name="看板商品", code="DASHBOARD-PRODUCT", warn_stock=1,
            ),
            Product(
                id=2, name="阈值边界商品", code="DASHBOARD-BOUNDARY", warn_stock=1,
            ),
            Product(
                id=3, name="零阈值商品", code="DASHBOARD-ZERO", warn_stock=0,
            ),
            Customer(id=1, code="DASHBOARD-CUSTOMER", name="客户测试"),
            Warehouse(id=1, code="DASHBOARD-WH", name="看板仓库"),
        ])
        db.flush()
        db.add_all([
            Shop(id=1, platform_id=1, name="看板店铺"),
            ProductSku(
                id=1, product_id=1, spec_name="规格一", spec_code="DASHBOARD-SKU",
            ),
            ProductSku(
                id=2, product_id=2, spec_name="边界规格", spec_code="DASHBOARD-SKU-2",
            ),
            ProductSku(
                id=3, product_id=3, spec_name="零阈值规格", spec_code="DASHBOARD-SKU-3",
            ),
            Location(id=1, warehouse_id=1, code="DASHBOARD-LOC-1"),
            Location(id=2, warehouse_id=1, code="DASHBOARD-LOC-2"),
        ])
        db.flush()
        db.add_all([
            StockLocation(
                id=1, location_id=1, product_sku_id=2, qty=Decimal("0.500"),
            ),
            StockLocation(
                id=2, location_id=2, product_sku_id=2, qty=Decimal("0.500"),
            ),
            Order(
                id=1, order_no="DASHBOARD-PAID", shop_id=1,
                status="NEW", pay_status="PAID", order_amount=Decimal("41.25"),
                paid_amount=Decimal("41.25"), order_time=day_start + timedelta(minutes=5),
            ),
            Order(
                id=2, order_no="DASHBOARD-CANCELLED", shop_id=1,
                status="CANCELLED", pay_status="PAID", order_amount=Decimal("900.00"),
                paid_amount=Decimal("900.00"), order_time=day_start + timedelta(minutes=10),
            ),
        ])
        db.commit()

    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_dashboard_get_dashboard_overview"],
        permission_scopes=["module:dashboard"],
    )["token"]
    mcp_data = call(runtime(factory, token), "erp_dashboard_get_dashboard_overview", {
        "trend_period": "week",
    }).structuredContent["data"]

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(dashboard_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    with TestClient(rest_app) as rest_client:
        response = rest_client.get(
            "/dashboard/overview", params={"trend_period": "week"},
            headers={"Authorization": "Bearer " + create_access_token(1, "甲", [])},
        )
    assert response.status_code == 200
    rest_data = response.json()["data"]
    assert mcp_data["as_of"] == rest_data["as_of"]
    assert mcp_data["metrics"] == rest_data["metrics"]
    assert mcp_data["sales_trend"] == rest_data["sales_trend"]
    values = {row["key"]: row["value"] for row in mcp_data["metrics"]}
    assert Decimal(str(values["today_order_amount"])) == Decimal("41.25")
    assert values["today_order_count"] == 1
    assert values["stock_warning_count"] == 1


def test_sales_trend_aggregates_many_orders_in_sql_with_fixed_bucket_count(factory):
    from datetime import timedelta
    from decimal import Decimal
    from sqlalchemy import event
    from app.models.order import Order
    from app.models.platform import Platform, Shop
    from app.services import metrics_service

    today = metrics_service._business_today()
    day_start, _ = metrics_service._local_day_bounds(today)
    with factory() as db:
        db.add(Platform(id=1, name="趋势平台", code="TREND"))
        db.flush()
        db.add(Shop(id=1, platform_id=1, name="趋势店铺"))
        db.flush()
        db.add_all([
            Order(
                id=1000 + index,
                order_no=f"TREND-{index}",
                shop_id=1,
                status="NEW",
                pay_status="PAID",
                order_amount=Decimal("10.25"),
                order_time=day_start - timedelta(days=index % 30) + timedelta(hours=10),
            )
            for index in range(1200)
        ])
        db.add_all([
            Order(
                id=2300, order_no="TREND-CANCELLED", shop_id=1,
                status="CANCELLED", pay_status="PAID",
                order_amount=Decimal("900.00"), order_time=day_start,
            ),
            Order(
                id=2301, order_no="TREND-UNPAID", shop_id=1,
                status="NEW", pay_status="UNPAID",
                order_amount=Decimal("800.00"), order_time=day_start,
            ),
        ])
        db.commit()

        statements = []
        connection = db.connection()

        def capture_sql(_connection, _cursor, statement, parameters, _context, _many):
            statements.append((statement, parameters))

        event.listen(connection, "before_cursor_execute", capture_sql)
        try:
            trends = {
                period: metrics_service.sales_trend(db, period=period, as_of=today)
                for period in ("day", "week", "month")
            }
        finally:
            event.remove(connection, "before_cursor_execute", capture_sql)
        plans = [
            db.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
            for statement, parameters in statements
        ]

    assert len(trends["day"]["items"]) == 30
    assert all(row["order_count"] == 40 for row in trends["day"]["items"])
    assert all(
        row["amount"] == Decimal("410.00")
        for row in trends["day"]["items"]
    )
    assert len(trends["week"]["items"]) == 12
    assert len(trends["month"]["items"]) == 12
    for trend in trends.values():
        assert sum(row["order_count"] for row in trend["items"]) == 1200
        assert sum(
            (row["amount"] for row in trend["items"]), Decimal("0"),
        ) == Decimal("12300.00")

    aggregate_queries = [
        statement for statement, _parameters in statements
        if "from \"order\"" in statement.lower() and "sum(case" in statement.lower()
    ]
    assert len(aggregate_queries) == 3
    for statement in aggregate_queries:
        sql = statement.lower()
        assert "group by" not in sql
        assert "select order.order_amount, order.pay_time, order.order_time" not in sql
    assert len(plans) == 3
    assert all("ix_order_sales_time" in repr(plan).lower() for plan in plans)


def test_purchase_invoice_cost_detail_pages_in_sql_and_projects_safe_fields(factory):
    from decimal import Decimal
    from sqlalchemy import event
    from app.models.party import Supplier
    from app.models.purchase import PurchaseInvoice
    from app.models.rbac import StaffRole

    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        db.add(Supplier(id=1, code="MCP-COST-SUPPLIER", name="测试供应商"))
        db.add_all([
            PurchaseInvoice(
                id=51, invoice_no="MCP-COST-51", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("4.00"), created_by=1,
                cost_allocation=json.dumps([
                    {
                        "stock_in_id": 10, "stock_in_no": "SI-10",
                        "stock_in_item_id": 101, "product_id": 1,
                        "sku_id": None, "qty": "2.000",
                        "original_amount": "4.00",
                        "original_cost_price": "2.00",
                        "allocated_amount": "1.00", "new_amount": "1.00",
                        "new_cost_price": "0.50",
                        "PRIVATE-JSON": "must-not-leak",
                    },
                    "ignore-non-object-row",
                    {
                        "stock_in_id": 11, "stock_in_no": "SI-11",
                        "stock_in_item_id": 102, "product_id": 2,
                        "sku_id": 22, "qty": "1.000",
                        "allocated_amount": "3.00",
                        "new_cost_price": "3.00",
                        "remark": "PRIVATE-REMARK",
                    },
                ]),
            ),
            PurchaseInvoice(
                id=52, invoice_no="MCP-COST-PRIVATE", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("1.00"), created_by=2,
                cost_allocation='[{"stock_in_no":"MUST-NOT-RETURN"}]',
            ),
            PurchaseInvoice(
                id=53, invoice_no="MCP-COST-BAD-JSON", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("1.00"), created_by=1,
                cost_allocation="not-json",
            ),
            PurchaseInvoice(
                id=54, invoice_no="MCP-COST-TOO-LARGE", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("1.00"), created_by=1,
                cost_allocation=json.dumps([{}] * 10001),
            ),
        ])
        db.commit()
    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_get_purchase_invoice_cost_detail"],
        permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, token)
    statements = []

    def capture_cost_sql(_conn, _cursor, statement, *_args):
        if "json_each" in statement.lower():
            statements.append(statement.lower())

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_cost_sql)
    try:
        first = call(rt, "erp_get_purchase_invoice_cost_detail", {
            "invoice_id": 51, "page_size": 1,
        })
        second = call(rt, "erp_get_purchase_invoice_cost_detail", {
            "invoice_id": 51, "page": 2, "page_size": 1,
        })
        too_large = call(rt, "erp_get_purchase_invoice_cost_detail", {
            "invoice_id": 54,
        })
    finally:
        event.remove(engine, "before_cursor_execute", capture_cost_sql)

    assert first.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert first.structuredContent["data"][0]["stock_in_no"] == "SI-10"
    assert second.structuredContent["data"][0]["stock_in_item_id"] == 102
    assert "PRIVATE" not in first.model_dump_json() + second.model_dump_json()
    assert too_large.structuredContent["code"] == "INVALID_ARGUMENT"
    assert len(statements) == 4  # 两次 count + 两次 SQL 分页；超大数组只做长度预检。
    page_statements = [statement for statement in statements if "limit" in statement]
    assert len(page_statements) == 2
    assert all("offset" in statement for statement in page_statements)
    private_invoice = call(rt, "erp_get_purchase_invoice_cost_detail", {
        "invoice_id": 52,
    }).structuredContent
    assert private_invoice["code"] in {"NOT_FOUND", "FORBIDDEN"}
    invalid_json = call(rt, "erp_get_purchase_invoice_cost_detail", {
        "invoice_id": 53,
    }).structuredContent
    assert invalid_json["data"] == []
    assert invalid_json["page"]["total"] == 0
    assert call(rt, "erp_get_purchase_invoice_cost_detail", {
        "invoice_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"


def test_purchase_invoice_cost_sql_contract_for_mysql_57_and_8():
    from sqlalchemy import text
    from sqlalchemy.dialects.mysql import dialect as mysql_dialect
    from app.services.purchase_invoice_service import (
        _cost_allocation_length_sql, _cost_allocation_page_sql,
    )

    length_sql = _cost_allocation_length_sql("mysql").upper()
    assert "JSON_VALID(INVOICE.COST_ALLOCATION)" in length_sql
    assert "JSON_TYPE(INVOICE.COST_ALLOCATION) = 'ARRAY'" in length_sql
    assert "JSON_LENGTH(INVOICE.COST_ALLOCATION)" in length_sql

    mysql57_count, mysql57_page = _cost_allocation_page_sql("mysql", (5, 7, 44))
    mysql57 = mysql57_page.upper()
    assert "JSON_TABLE" not in mysql57
    assert "JSON_EXTRACT(" in mysql57
    assert "ALLOCATION_INDEX.ORDINAL < JSON_LENGTH(" in mysql57
    assert mysql57.count("CROSS JOIN") == 4  # 4 decimal digits => ordinals 0..9999.
    assert "CAST(" in mysql57 and "AS CHAR CHARACTER SET UTF8MB4)" in mysql57

    mysql8_count, mysql8_page = _cost_allocation_page_sql("mysql", (8, 0, 36))
    mysql8 = mysql8_page.upper()
    assert "JSON_TABLE(" in mysql8
    assert "FOR ORDINALITY" in mysql8
    assert "ROW_VALUE JSON PATH '$' NULL ON EMPTY NULL ON ERROR" in mysql8
    assert "ALLOCATION_INDEX" not in mysql8

    for statement in (mysql57_count, mysql57_page, mysql8_count, mysql8_page):
        compiled = text(statement).compile(dialect=mysql_dialect())
        assert "invoice_id" in compiled.params
    assert set(text(mysql57_page).compile(dialect=mysql_dialect()).params) == {
        "invoice_id", "page_size", "offset",
    }
    assert set(text(mysql8_page).compile(dialect=mysql_dialect()).params) == {
        "invoice_id", "page_size", "offset",
    }


def test_purchase_invoice_cost_detail_scope_matrix_matches_rest(factory):
    from decimal import Decimal
    from app.api.purchase_invoice import router as purchase_invoice_router
    from app.core.data_scope import clear_scope_policy
    from app.models.org import Department
    from app.models.party import Supplier
    from app.models.purchase import PurchaseInvoice
    from app.models.rbac import DataScope

    tool = "erp_get_purchase_invoice_cost_detail"
    with factory() as db:
        db.add_all([
            Department(id=1, company_id=1, name="发票权限一部"),
            Department(id=2, company_id=1, name="发票权限二部"),
            Staff(id=3, company_id=1, department_id=2, name="跨部门发票制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门发票制单人"),
            Role(id=3, company_id=1, code="FINANCE", name="财务"),
            Supplier(id=1, code="COST-SCOPE-SUP", name="成本范围测试供应商"),
            PurchaseInvoice(
                id=61, invoice_no="COST-SCOPE-SELF", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("1.00"), created_by=1,
                cost_allocation=json.dumps([{"stock_in_no": "COST-SELF"}]),
            ),
            PurchaseInvoice(
                id=62, invoice_no="COST-SCOPE-DEPARTMENT", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("1.00"), created_by=4,
                cost_allocation=json.dumps([{"stock_in_no": "COST-DEPARTMENT"}]),
            ),
            PurchaseInvoice(
                id=63, invoice_no="COST-SCOPE-COMPANY", supplier_id=1,
                invoice_type="NORMAL", amount=Decimal("1.00"), created_by=3,
                cost_allocation=json.dumps([{"stock_in_no": "COST-COMPANY"}]),
            ),
            DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1),
        ])
        db.add(StaffRole(staff_id=1, role_id=3))
        db.get(Staff, 1).department_id = 1
        db.commit()

    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, tool],
        permission_scopes=["module:finance"],
    )["token"]
    rt = runtime(factory, token)

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(purchase_invoice_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    headers = {"Authorization": "Bearer " + create_access_token(1, "成本范围测试", [])}
    visible_by_scope = {
        "SELF": {61},
        "DEPARTMENT": {61, 62},
        "COMPANY": {61, 62, 63},
    }
    with TestClient(rest_app) as rest_client:
        for scope, visible_ids in visible_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            for invoice_id in (61, 62, 63):
                mcp = call(rt, tool, {"invoice_id": invoice_id, "page_size": 10})
                rest = rest_client.get(
                    f"/purchase-invoices/{invoice_id}/cost-detail", headers=headers,
                )
                if invoice_id in visible_ids:
                    assert not mcp.isError, (scope, invoice_id, mcp.structuredContent)
                    assert rest.status_code == 200, (scope, invoice_id, rest.text)
                    rest_rows = rest.json()["data"]
                    mcp_data = mcp.structuredContent
                    assert mcp_data["page"]["total"] == len(rest_rows) == 1
                    assert mcp_data["data"][0]["stock_in_no"] == rest_rows[0]["stock_in_no"]
                    assert "PRIVATE" not in mcp.model_dump_json()
                else:
                    assert mcp.structuredContent["code"] in {"NOT_FOUND", "FORBIDDEN"}, (
                        scope, invoice_id, mcp.structuredContent,
                    )
                    assert rest.status_code == 404, (scope, invoice_id)


def test_purchase_invoice_cost_page_mysql_sql_is_version_compatible_and_bounded():
    from sqlalchemy.dialects import mysql
    from sqlalchemy import text
    from app.services.purchase_invoice_service import (
        _cost_allocation_length_sql, _cost_allocation_page_sql,
    )

    count_80, page_80 = _cost_allocation_page_sql("mysql", (8, 0, 36))
    compiled_80 = text(page_80).compile(dialect=mysql.dialect())
    sql_80 = str(compiled_80).lower()
    assert "json_table" in sql_80
    assert "json_type(allocation.row_value) = 'object'" in sql_80
    assert "limit" in sql_80 and "offset" in sql_80
    assert set(compiled_80.params) == {"invoice_id", "page_size", "offset"}
    assert "json_table" in count_80.lower()

    count_57, page_57 = _cost_allocation_page_sql("mysql", (5, 7, 42))
    compiled_57 = text(page_57).compile(dialect=mysql.dialect())
    sql_57 = str(compiled_57).lower()
    assert "json_table" not in sql_57 and "json_extract" in sql_57
    assert "json_type" in sql_57 and "json_length" in sql_57
    assert "cross join" in sql_57 and "digit * 1000" in sql_57
    assert "limit" in sql_57 and "offset" in sql_57
    assert set(compiled_57.params) == {"invoice_id", "page_size", "offset"}
    assert "json_table" not in count_57.lower()

    length_57 = text(_cost_allocation_length_sql("mysql")).compile(dialect=mysql.dialect())
    assert "json_valid" in str(length_57).lower()
    assert "json_length" in str(length_57).lower()


def test_linked_voucher_tools_use_parent_scope_and_safe_voucher_summary(factory, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    from app.api.finance_doc import router as finance_doc_router
    from app.api.pay_receipt import router as pay_receipt_router
    from app.api.prepay import router as prepay_router
    from app.core.data_scope import clear_scope_policy
    from app.models.finance import Voucher
    from app.models.rbac import StaffRole
    from app.services import finance_doc_service, pay_receipt_service, prepay_service, voucher_service

    linked = [
        ("expense", "erp_finance_doc_get_expense_voucher", "expense_id", "finance_expenses", "费用单:EXP-1", finance_doc_service, "get_expense_or_404", "expense_no", "EXP-1"),
        ("income", "erp_finance_doc_get_income_voucher", "income_id", "finance_incomes", "收入单:INC-1", finance_doc_service, "get_income_or_404", "income_no", "INC-1"),
        ("payment", "erp_finance_doc_get_payment_voucher", "payment_id", "finance_payments", "付款单:PAY-1", pay_receipt_service, "get_payment_or_404", "payment_no", "PAY-1"),
        ("receipt", "erp_finance_doc_get_receipt_voucher", "receipt_id", "finance_receipts", "收款单:REC-1", pay_receipt_service, "get_receipt_or_404", "receipt_no", "REC-1"),
        ("settlement", "erp_finance_doc_get_settlement_voucher", "settlement_id", "finance_settlements", "平台结算单:SET-1", finance_doc_service, "get_settlement_or_404", "settlement_no", "SET-1"),
        ("prepay", "erp_get_purchase_prepay_voucher", "prepay_id", "purchase_prepays", "采购预付款申请:PRE-1", prepay_service, "get_prepay_or_404", "prepay_no", "PRE-1"),
    ]
    tools = [ACCESS_TOOL] + [item[1] for item in linked]
    with factory() as db:
        db.add(Role(id=3, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=3))
        for index, item in enumerate(linked, start=1):
            db.add(Voucher(
                id=index, voucher_word="记", voucher_no=f"MCP-VCH-{index}",
                reference=item[4], total_debit=Decimal("100.00"),
                total_credit=Decimal("100.00"), status="APPROVED", created_by=1,
            ))
        db.commit()

    for item in linked:
        _, _, _, _, _, service, getter_name, number_attr, number = item
        def _parent(db, record_id, *, attr=number_attr, value=number):
            return SimpleNamespace(**{attr: value})
        monkeypatch.setattr(service, getter_name, _parent)

    token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:finance", "module:purchase"],
    )["token"]
    rt = runtime(factory, token)
    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    for rest_router in (finance_doc_router, pay_receipt_router, prepay_router):
        rest_app.include_router(rest_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {
        "Authorization": "Bearer " + create_access_token(1, "财务", []),
    }
    rest_paths = {
        "erp_finance_doc_get_expense_voucher": "/expenses/1/voucher",
        "erp_finance_doc_get_income_voucher": "/incomes/1/voucher",
        "erp_finance_doc_get_payment_voucher": "/payments/1/voucher",
        "erp_finance_doc_get_receipt_voucher": "/receipts/1/voucher",
        "erp_finance_doc_get_settlement_voucher": "/settlements/1/voucher",
        "erp_get_purchase_prepay_voucher": "/purchase-prepays/1/voucher",
    }
    shared_fields = (
        "id", "voucher_word", "voucher_no", "business_date", "record_date",
        "total_debit", "total_credit", "status", "created_at", "updated_at",
    )
    with TestClient(rest_app) as rest_client:
        for index, item in enumerate(linked, start=1):
            _, tool, id_key, _, _, _, _, _, _ = item
            result = call(rt, tool, {id_key: 1})
            data = result.structuredContent["data"]
            rest_response = rest_client.get(rest_paths[tool], headers=rest_headers)
            assert rest_response.status_code == 200, (tool, rest_response.text)
            rest_data = rest_response.json()["data"]
            assert data["id"] == rest_data["id"] == index
            assert data["voucher_no"] == rest_data["voucher_no"] == f"MCP-VCH-{index}"
            assert data["item_count"] == len(rest_data["items"]) == 0
            for field in shared_fields:
                assert data[field] == rest_data[field], (tool, field)
            for field in ("total_debit", "total_credit"):
                assert Decimal(data[field]) == Decimal(str(rest_data[field])), (tool, field)
            assert "reference" in rest_data and item[4] == rest_data["reference"]
            assert {"reference", "created_by", "auditor_id"} <= set(rest_data)
            assert not {"reference", "summary", "created_by", "auditor_id", "items"} & set(data)

        monkeypatch.setattr(
            voucher_service, "get_active_voucher_by_reference",
            lambda db, reference: None,
        )
        for item in linked:
            _, tool, id_key, _, _, _, _, _, _ = item
            missing_voucher = call(rt, tool, {id_key: 1})
            rest_empty = rest_client.get(rest_paths[tool], headers=rest_headers)
            assert rest_empty.status_code == 200, (tool, rest_empty.text)
            assert not missing_voucher.isError
            assert missing_voucher.structuredContent["data"] is None
            assert rest_empty.json()["data"] is None

    denied = call(rt, "erp_finance_doc_get_expense_voucher", {"expense_id": 0})
    assert denied.structuredContent["code"] == "INVALID_ARGUMENT"


def test_print_batch_revalidates_every_order_and_returns_bounded_redacted_summary(factory, monkeypatch):
    from sqlalchemy import delete, func
    from app.models.rbac import Role, StaffRole
    from app.services import order_service, print_service

    tools = [ACCESS_TOOL, "erp_print_get_print_batch"]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=4, company_id=1, code="FINANCE", name="财务"),
            Role(id=5, company_id=1, code="ADMIN", name="管理员"),
            Role(id=6, company_id=1, code="AFTER_SALE", name="售后"),
            Staff(id=3, company_id=1, name="售后边界测试员"),
            Staff(id=4, company_id=1, name="财务边界测试员"),
            Staff(id=5, company_id=1, name="管理员边界测试员"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            StaffRole(staff_id=3, role_id=6),
            StaffRole(staff_id=4, role_id=4),
            StaffRole(staff_id=5, role_id=5),
        ])
        db.commit()
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:order"],
    )["token"]
    rt = runtime(factory, token)
    assert call(rt, "erp_print_get_print_batch", {
        "batch_no": "BATCH-1",
    }).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        before = db.scalar(select(func.count(AuditLog.id)))
        db.commit()

    orders = [
        {
            "id": index, "order_no": f"ORDER-{index}",
            "receiver_name": f"PRIVATE-NAME-{index}",
            "receiver_phone": "13800138000",
            "receiver_address": "上海市浦东新区张江路100号",
            "tracking_no": f"PRIVATE-TRACK-{index}",
        }
        for index in range(1, 102)
    ]
    checked = []
    monkeypatch.setattr(print_service, "get_print_batch", lambda db, *, batch_no: {
        "batch_no": batch_no, "print_type": "THERMAL", "order_count": len(orders),
        "created_at": None, "orders": orders,
        "waybills": [{"order_id": row["id"]} for row in orders],
        "delivery_lists": [], "pick_list": None,
        "shipping_slips": [{"receiver_name": "PRIVATE-SLIP"}],
    })
    monkeypatch.setattr(
        order_service, "get_order_or_404",
        lambda db, order_id: checked.append(order_id),
    )
    warehouse_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:order"],
    )["token"]
    result = call(runtime(factory, warehouse_token), "erp_print_get_print_batch", {
        "batch_no": "BATCH-1",
    })
    data = result.structuredContent["data"]
    assert len(checked) == 101 and set(checked) == set(range(1, 102))
    assert data["order_count"] == 101 and len(data["orders"]) == 100
    assert data["orders_truncated"] is True
    assert data["orders"][0]["receiver_phone"] == "****8000"
    assert "receiver_address" in data["orders"][0]
    serialized = result.model_dump_json()
    for private in ("PRIVATE-NAME", "PRIVATE-TRACK", "PRIVATE-SLIP", "13800138000", "上海市浦东新区张江路100号"):
        assert private not in serialized

    from app.api.print import router as print_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(print_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {
        "Authorization": "Bearer " + create_access_token(1, "仓库", []),
    }
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get(
            "/print/batches/BATCH-1", headers=rest_headers,
        )
        assert rest_response.status_code == 200
        rest_data = rest_response.json()["data"]
        for field in ("batch_no", "print_type", "order_count"):
            assert data[field] == rest_data[field]
        assert len(rest_data["orders"]) == 101
        assert len(data["orders"]) == 100 and data["orders_truncated"] is True
        for mcp_order, rest_order in zip(data["orders"], rest_data["orders"]):
            assert mcp_order["id"] == rest_order["id"]
            assert mcp_order["order_no"] == rest_order["order_no"]
        assert data["orders"][0]["receiver_phone"] != rest_data["orders"][0]["receiver_phone"]
        assert data["orders"][0]["receiver_address"] != rest_data["orders"][0]["receiver_address"]

    for staff_id in (4, 5):
        role_token = issue(
            factory, staff_id=staff_id, tool_scopes=tools,
            permission_scopes=["module:order"],
        )["token"]
        role_result = call(
            runtime(factory, role_token), "erp_print_get_print_batch",
            {"batch_no": "BATCH-1"},
        )
        assert not role_result.isError, (staff_id, role_result.structuredContent)
    after_sale_token = issue(
        factory, staff_id=3, tool_scopes=tools,
        permission_scopes=["module:order"],
    )["token"]
    assert call(runtime(factory, after_sale_token), "erp_print_get_print_batch", {
        "batch_no": "BATCH-1",
    }).structuredContent["code"] == "FORBIDDEN"

    from app.core.errors import BizException, ErrorCode
    def deny_last_order(db, order_id):
        if order_id == 101:
            raise BizException(ErrorCode.NOT_FOUND, "订单不可见")
    monkeypatch.setattr(order_service, "get_order_or_404", deny_last_order)
    assert call(runtime(factory, warehouse_token), "erp_print_get_print_batch", {
        "batch_no": "BATCH-1",
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(runtime(factory, warehouse_token), "erp_print_get_print_batch", {
        "batch_no": "",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    with factory() as db:
        assert db.scalar(select(func.count(AuditLog.id))) >= before


def test_after_sale_tools_page_mask_and_scope_legacy_data_by_company(factory):
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.order import (
        ExchangeOrder, ExchangeOrderItem, Order, ReturnOrder, ReturnOrderItem,
        ReturnRegister,
    )
    from app.models.platform import Platform, Shop
    from app.models.warehouse import Warehouse

    tools = [
        ACCESS_TOOL, "erp_search_after_sale_returns", "erp_get_after_sale_return",
        "erp_list_after_sale_return_items", "erp_search_return_registers",
        "erp_search_after_sale_exchanges", "erp_get_after_sale_exchange",
        "erp_list_after_sale_exchange_items",
    ]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="AFTER_SALE", name="售后"),
            Role(id=4, company_id=1, code="FINANCE", name="财务"),
            Role(id=5, company_id=1, code="ADMIN", name="管理员"),
            Role(id=6, company_id=1, code="VP", name="管理层"),
            Role(id=7, company_id=1, code="QUALITY", name="质检"),
            Role(id=8, company_id=1, code="WAREHOUSE", name="仓库"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            Staff(id=3, company_id=1, name="售后兼财务"),
            Staff(id=4, company_id=1, name="仅财务角色"),
            Staff(id=5, company_id=1, name="仅售后角色"),
            Staff(id=6, company_id=1, name="仅管理员角色"),
            Staff(id=7, company_id=1, name="仅采购角色"),
            Staff(id=8, company_id=1, name="仅管理层角色"),
            Staff(id=9, company_id=1, name="仅质检角色"),
            Staff(id=10, company_id=1, name="仅仓库角色"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=3, role_id=3),
            StaffRole(staff_id=3, role_id=4),
            StaffRole(staff_id=4, role_id=4),
            StaffRole(staff_id=5, role_id=3),
            StaffRole(staff_id=6, role_id=5),
            StaffRole(staff_id=7, role_id=1),
            StaffRole(staff_id=8, role_id=6),
            StaffRole(staff_id=9, role_id=7),
            StaffRole(staff_id=10, role_id=8),
        ])
        db.add_all([
            Platform(id=1, name="测试平台"),
            Shop(id=1, platform_id=1, name="售后测试店铺"),
            Platform(id=2, company_id=2, name="另一公司平台"),
            Shop(id=2, company_id=2, platform_id=2, name="另一公司店铺"),
            Warehouse(id=1, code="AS-WH", name="退货测试仓库"),
            Product(id=1, code="AS-P1", name="售后商品一"),
            Product(id=2, code="AS-P2", name="售后商品二"),
        ])
        db.flush()
        db.add(Order(
            id=1, order_no="AS-ORDER-1", shop_id=1,
            status="SHIPPED", audit_status="APPROVED",
        ))
        db.add(Order(
            id=99, company_id=2, order_no="FOREIGN-AS-ORDER", shop_id=2,
            status="SHIPPED", audit_status="APPROVED",
        ))
        db.flush()
        db.add_all([
            ReturnOrder(
                id=1, return_no="AS-RETURN-1", order_id=1, shop_id=1,
                status="PENDING", refund_amount=Decimal("25.00"),
                return_qty=Decimal("2"), buyer_phone="13812345678",
                logistics_company="测试物流", tracking_no="SF1234567890",
                reason="客户地址和隐私原因", created_at=datetime(2026, 10, 11, 10),
            ),
            ReturnOrder(
                id=2, return_no="AS-RETURN-2", order_id=1, shop_id=1,
                status="CONFIRMED", refund_amount=Decimal("8.00"),
                return_qty=Decimal("1"), buyer_phone="13987654321",
                warehouse_id=1,
                created_at=datetime(2026, 10, 12, 10),
            ),
            ExchangeOrder(
                id=1, exchange_no="AS-EXCHANGE-1", order_id=1, shop_id=1,
                status="PENDING", exchange_qty=Decimal("2"), reason="不外发原因",
                compensate_amount=Decimal("12.50"), compensate_direction="PAY",
            ),
            ExchangeOrder(
                id=2, exchange_no="OTHER-EXCHANGE-2", order_id=1, shop_id=1,
                status="CONFIRMED", exchange_qty=Decimal("1"),
                compensate_amount=Decimal("5.00"), compensate_direction="RECEIVE",
            ),
            ReturnOrder(
                id=99, company_id=2, return_no="FOREIGN-RETURN", order_id=99,
                shop_id=2, status="PENDING", return_qty=Decimal("1"),
            ),
            ExchangeOrder(
                id=99, company_id=2, exchange_no="FOREIGN-EXCHANGE", order_id=99,
                shop_id=2, status="PENDING", exchange_qty=Decimal("1"),
            ),
        ])
        db.flush()
        db.add_all([
            ReturnOrderItem(id=1, return_order_id=1, product_id=1,
                            qty=Decimal("1"), refund_amount=Decimal("10.00")),
            ReturnOrderItem(id=2, return_order_id=1, product_id=2,
                            qty=Decimal("1"), refund_amount=Decimal("15.00")),
            ExchangeOrderItem(id=1, exchange_order_id=1, product_id=1,
                              qty=Decimal("1"), exchange_qty=Decimal("1")),
            ExchangeOrderItem(id=2, exchange_order_id=1, product_id=2,
                              qty=Decimal("1"), exchange_qty=Decimal("1")),
                ReturnOrderItem(
                    id=99, company_id=2, return_order_id=99, product_id=2,
                    qty=Decimal("1"), refund_amount=Decimal("1.00"),
                ),
                ExchangeOrderItem(
                    id=99, company_id=2, exchange_order_id=99, product_id=2,
                    qty=Decimal("1"), exchange_qty=Decimal("1"),
                ),
            ReturnRegister(
                id=1, return_order_id=1, logistics_company="测试物流",
                tracking_no="YT9988776655", sender="敏感寄件人",
                sender_phone="13711112222", product_info="不外发自由文本",
                image="private/image.jpg",
            ),
            ReturnRegister(
                id=2, exchange_order_id=1, logistics_company="测试物流",
                tracking_no="JD1234567890", sender_phone="13633334444",
            ),
            ReturnRegister(
                id=3, logistics_company="未绑定物流", tracking_no="UNBOUND-1",
                sender_phone="13555556666",
            ),
            ReturnRegister(
                id=99, company_id=2, return_order_id=99,
                logistics_company="外公司物流", tracking_no="FOREIGN-TRACKING",
            ),
        ])
        db.commit()

    after_sale_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:order"],
    )["token"]
    rt = runtime(factory, after_sale_token, limits=MemoryLimits(rpm=100))
    warehouse_token = issue(
        factory, staff_id=2, tool_scopes=tools,
        permission_scopes=["module:order"],
    )["token"]
    assert call(
        runtime(factory, warehouse_token), "erp_search_after_sale_returns",
    ).structuredContent["code"] == "FORBIDDEN"
    # 共享旧表可在多公司实例运行；列表仅含本公司记录，跨公司父子详情仍拒绝。
    multicompany_probes = {
        "erp_search_after_sale_returns": {},
        "erp_get_after_sale_return": {"return_id": 99},
        "erp_list_after_sale_return_items": {"return_id": 99},
        "erp_search_return_registers": {},
        "erp_search_after_sale_exchanges": {},
        "erp_get_after_sale_exchange": {"exchange_id": 99},
        "erp_list_after_sale_exchange_items": {"exchange_id": 99},
    }
    for tool_name, arguments in multicompany_probes.items():
        result = call(rt, tool_name, arguments)
        if tool_name.startswith("erp_search_"):
            assert not result.isError, (tool_name, result.structuredContent)
            assert "FOREIGN-" not in result.model_dump_json()
        else:
            assert result.isError and result.structuredContent["code"] == "FORBIDDEN", tool_name
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.commit()
        before = tuple(db.scalar(select(func.count(model.id))) for model in (
            ReturnOrder, ReturnOrderItem, ExchangeOrder, ExchangeOrderItem, ReturnRegister,
        ))

    role_probes = (
        ("erp_search_after_sale_returns", {}),
        ("erp_get_after_sale_return", {"return_id": 1}),
        ("erp_list_after_sale_return_items", {"return_id": 1}),
        ("erp_search_return_registers", {}),
        ("erp_search_after_sale_exchanges", {}),
        ("erp_get_after_sale_exchange", {"exchange_id": 1}),
        ("erp_list_after_sale_exchange_items", {"exchange_id": 1}),
    )
    role_matrix = (
        ("AFTER_SALE", 5, ["module:order"], True),
        ("ADMIN", 6, ["module:order"], True),
        ("FINANCE", 4, ["module:order"], False),
        ("PURCHASER", 7, ["module:purchase"], False),
        ("VP", 8, ["module:purchase"], False),
        ("QUALITY", 9, ["module:stock"], False),
        ("WAREHOUSE", 10, ["module:order"], False),
    )
    for role_code, staff_id, permission_scopes, should_allow in role_matrix:
        role_token = issue(
            factory, staff_id=staff_id, tool_scopes=tools,
            permission_scopes=permission_scopes,
        )["token"]
        role_rt = runtime(
            factory, role_token, limits=MemoryLimits(rpm=100),
        )
        for tool_name, arguments in role_probes:
            result = call(role_rt, tool_name, arguments)
            if should_allow:
                assert not result.isError, (
                    role_code, tool_name, result.structuredContent,
                )
                payload = result.structuredContent["data"]
                rows = payload if isinstance(payload, list) else [payload]
                assert all(
                    "refund_amount" not in row and "compensate_amount" not in row
                    for row in rows
                ), (role_code, tool_name)
            else:
                assert result.isError and result.structuredContent["code"] == "FORBIDDEN", (
                    role_code, tool_name,
                )

    search = call(rt, "erp_search_after_sale_returns", {
        "keyword": "AS-RETURN", "page_size": 1,
        "start_time": "2026-10-11T02:00:00Z",
        "end_time": "2026-10-11T10:00:00+08:00",
    })
    assert search.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 1, "has_more": False,
    }
    assert search.structuredContent["data"][0]["return_no"] == "AS-RETURN-1"
    assert "refund_amount" not in search.structuredContent["data"][0]
    assert search.structuredContent["data"][0]["buyer_phone"] == "****5678"
    assert search.structuredContent["data"][0]["tracking_no"] == "SF****90"
    empty_page = call(rt, "erp_search_after_sale_returns", {
        "keyword": "AS-RETURN", "page": 3, "page_size": 1,
    })
    assert empty_page.structuredContent["data"] == []
    assert empty_page.structuredContent["page"]["has_more"] is False

    detail = call(rt, "erp_get_after_sale_return", {"return_id": 1})
    assert detail.structuredContent["data"]["order_no"] == "AS-ORDER-1"
    assert "reason" not in detail.structuredContent["data"]
    assert "items" not in detail.structuredContent["data"]
    items = call(rt, "erp_list_after_sale_return_items", {
        "return_id": 1, "page_size": 1,
    })
    assert items.structuredContent["page"]["has_more"] is True
    assert items.structuredContent["data"][0]["product_name"] == "售后商品一"
    assert "refund_amount" not in items.structuredContent["data"][0]

    registers = call(rt, "erp_search_return_registers", {
        "return_order_id": 1, "page_size": 1,
    })
    register = registers.structuredContent["data"][0]
    assert register["tracking_no"] == "YT****55"
    assert register["sender_phone"] == "****2222"
    assert "sender" not in register and "product_info" not in register and "image" not in register
    assert "不外发自由文本" not in registers.model_dump_json()
    assert call(rt, "erp_search_return_registers", {
        "bound": True,
    }).structuredContent["page"]["total"] == 2
    unbound_registers = call(rt, "erp_search_return_registers", {
        "bound": False,
    }).structuredContent
    assert unbound_registers["page"]["total"] == 1
    assert unbound_registers["data"][0]["tracking_no"] == "UN****-1"
    exchange_registers = call(rt, "erp_search_return_registers", {
        "exchange_order_id": 1,
    }).structuredContent
    assert exchange_registers["page"]["total"] == 1
    assert call(rt, "erp_search_return_registers", {
        "return_order_id": 1, "exchange_order_id": 1,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    exchanges = call(rt, "erp_search_after_sale_exchanges", {"keyword": "AS-EXCHANGE"})
    assert exchanges.structuredContent["data"][0]["exchange_no"] == "AS-EXCHANGE-1"
    assert "compensate_amount" not in exchanges.structuredContent["data"][0]
    exchange = call(rt, "erp_get_after_sale_exchange", {"exchange_id": 1})
    assert "reason" not in exchange.structuredContent["data"]
    exchange_items = call(rt, "erp_list_after_sale_exchange_items", {
        "exchange_id": 1, "page_size": 1,
    })
    assert exchange_items.structuredContent["page"]["has_more"] is True

    # 同身份、同筛选对照现有 REST；MCP 仅做固定字段投影和独立明细分页。
    from app.api.after_sale import router as after_sale_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(after_sale_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "售后", [])}
    with TestClient(rest_app) as rest_client:
        def assert_mcp_summary_matches_rest(mcp_row, rest_row, context):
            # 电话和完整运单号有意只在MCP侧掩码；其余双方共有的固定摘要字段应一致。
            intentionally_masked = {"buyer_phone", "tracking_no"}
            decimal_fields = {
                "return_qty", "exchange_qty", "refund_amount",
                "compensate_amount",
            }
            for field in set(mcp_row) & set(rest_row) - intentionally_masked:
                if mcp_row[field] is None or rest_row[field] is None:
                    assert mcp_row[field] is rest_row[field], (context, field)
                elif field in decimal_fields:
                    assert Decimal(str(mcp_row[field])) == Decimal(
                        str(rest_row[field])
                    ), (context, field)
                else:
                    assert mcp_row[field] == rest_row[field], (context, field)

        rest_list = rest_client.get("/after-sales/returns", params={
            "keyword": "AS-RETURN", "page": 1, "page_size": 1,
            "start_time": "2026-10-11T02:00:00Z",
            "end_time": "2026-10-11T10:00:00+08:00",
        }, headers=rest_headers)
        assert rest_list.status_code == 200
        rest_list_data = rest_list.json()["data"]
        assert search.structuredContent["page"]["total"] == rest_list_data["total"] == 1
        rest_return_row = rest_list_data["items"][0]
        assert search.structuredContent["data"][0]["id"] == rest_return_row["id"]
        assert search.structuredContent["data"][0]["return_no"] == rest_return_row["return_no"]

        def assert_return_filter_pages(filters, expected_ids):
            rest_filtered = rest_client.get(
                "/after-sales/returns",
                params={**filters, "page": 1, "page_size": 20},
                headers=rest_headers,
            )
            assert rest_filtered.status_code == 200, filters
            rest_data = rest_filtered.json()["data"]
            rest_ids = [row["id"] for row in rest_data["items"]]
            assert rest_ids == expected_ids, filters
            rest_rows_by_id = {row["id"]: row for row in rest_data["items"]}
            for page_number in (1, 2):
                mcp_page = call(rt, "erp_search_after_sale_returns", {
                    **filters, "page": page_number, "page_size": 1,
                }).structuredContent
                assert mcp_page["page"]["total"] == rest_data["total"] == len(expected_ids), filters
                assert [row["id"] for row in mcp_page["data"]] == expected_ids[page_number - 1:page_number], filters
                assert mcp_page["page"]["has_more"] is (page_number < len(expected_ids)), filters
                for row in mcp_page["data"]:
                    assert_mcp_summary_matches_rest(
                        row, rest_rows_by_id[row["id"]],
                        ("returns", filters, row["id"]),
                    )

        for return_filters, return_ids in (
            ({"order_id": 1}, [2, 1]),
            ({"shop_id": 1}, [2, 1]),
            ({"warehouse_id": 1}, [2]),
            ({"status": "PENDING"}, [1]),
            ({"keyword": "AS-ORDER-1"}, [2, 1]),
            ({"start_time": "2026-10-12T10:00:00+08:00", "end_time": "2026-10-12T10:00:00+08:00"}, [2]),
            ({"order_id": 1, "shop_id": 1, "status": "PENDING"}, [1]),
            ({"warehouse_id": 1, "status": "CONFIRMED", "keyword": "AS-RETURN-2"}, [2]),
            ({"shop_id": 1, "keyword": "AS-RETURN-1", "status": "PENDING"}, [1]),
            ({"warehouse_id": 1, "start_time": "2026-10-12T10:00:00+08:00", "end_time": "2026-10-12T10:00:00+08:00"}, [2]),
        ):
            assert_return_filter_pages(return_filters, return_ids)

        rest_return = rest_client.get("/after-sales/returns/1", headers=rest_headers)
        assert rest_return.status_code == 200
        rest_return_data = rest_return.json()["data"]
        mcp_return = detail.structuredContent["data"]
        for field in (
            "id", "return_no", "order_id", "order_no", "order_status", "shop_id",
            "shop_name", "warehouse_id", "warehouse_name", "status", "status_label",
            "return_qty", "logistics_company", "is_arrived", "is_rejected_sign",
            "goods_status", "need_return_goods", "stock_in_id", "stock_in_no",
            "stock_in_status", "audit_time", "created_at", "updated_at",
        ):
            if field in rest_return_data:
                if field == "return_qty":
                    assert Decimal(str(mcp_return[field])) == Decimal(str(rest_return_data[field]))
                else:
                    assert mcp_return[field] == rest_return_data[field], field
        assert mcp_return["buyer_phone"] == "****5678"
        assert rest_return_data["buyer_phone"] == "13812345678"
        assert "reason" not in mcp_return and "items" not in mcp_return

        mcp_return_items = call(rt, "erp_list_after_sale_return_items", {
            "return_id": 1, "page_size": 1,
        }).structuredContent
        assert mcp_return_items["page"]["total"] == len(rest_return_data["items"]) == 2
        mcp_return_items_page_2 = call(rt, "erp_list_after_sale_return_items", {
            "return_id": 1, "page": 2, "page_size": 1,
        }).structuredContent
        assert mcp_return_items_page_2["page"]["has_more"] is False
        assert [row["id"] for row in mcp_return_items_page_2["data"]] == [
            rest_return_data["items"][1]["id"]
        ]
        all_mcp_return_items = (
            mcp_return_items["data"] + mcp_return_items_page_2["data"]
        )
        for mcp_item, rest_item in zip(all_mcp_return_items, rest_return_data["items"]):
            for field in ("id", "product_id", "product_code", "product_name", "sku_id", "qty"):
                if field in rest_item:
                    if field == "qty":
                        assert Decimal(str(mcp_item[field])) == Decimal(str(rest_item[field]))
                    else:
                        assert mcp_item[field] == rest_item[field], field
        assert "product_image" not in mcp_return_items["data"][0]

        rest_registers = rest_client.get(
            "/after-sales/registers", params={"bound": "true"}, headers=rest_headers,
        )
        assert rest_registers.status_code == 200
        rest_register_rows = rest_registers.json()["data"]
        mcp_register_page_1 = call(rt, "erp_search_return_registers", {
            "bound": True, "page": 1, "page_size": 1,
        }).structuredContent
        mcp_register_page_2 = call(rt, "erp_search_return_registers", {
            "bound": True, "page": 2, "page_size": 1,
        }).structuredContent
        assert mcp_register_page_1["page"]["total"] == len(rest_register_rows) == 2
        assert mcp_register_page_1["page"]["has_more"] is True
        assert mcp_register_page_2["page"]["has_more"] is False
        mcp_register_rows = mcp_register_page_1["data"] + mcp_register_page_2["data"]
        assert [row["id"] for row in mcp_register_rows] == [row["id"] for row in rest_register_rows]
        rest_register_by_id = {row["id"]: row for row in rest_register_rows}
        for mcp_register in mcp_register_rows:
            rest_register = rest_register_by_id[mcp_register["id"]]
            for field in (
                "return_order_id", "exchange_order_id", "target_type", "target_id",
                "target_no", "logistics_company", "created_at", "updated_at",
            ):
                assert mcp_register[field] == rest_register[field], field
        for combined_filter, expected_id in (
            ({"bound": True, "return_order_id": 1}, 1),
            ({"bound": True, "exchange_order_id": 1}, 2),
        ):
            filtered_registers = call(
                rt, "erp_search_return_registers", combined_filter,
            ).structuredContent
            assert filtered_registers["page"]["total"] == 1
            assert [row["id"] for row in filtered_registers["data"]] == [expected_id]
            for field in (
                "return_order_id", "exchange_order_id", "target_type",
                "target_id", "target_no", "logistics_company", "created_at",
                "updated_at",
            ):
                assert filtered_registers["data"][0][field] == (
                    rest_register_by_id[expected_id][field]
                ), (combined_filter, field)
        assert "sender" not in mcp_register_rows[0]
        assert "product_info" not in mcp_register_rows[0]
        assert "image" not in mcp_register_rows[0]

        rest_exchanges = rest_client.get("/after-sales/exchanges", params={
            "keyword": "AS-EXCHANGE", "page": 1, "page_size": 20,
        }, headers=rest_headers)
        assert rest_exchanges.status_code == 200
        rest_exchange_list = rest_exchanges.json()["data"]
        assert exchanges.structuredContent["page"]["total"] == rest_exchange_list["total"] == 1
        rest_exchange_row = rest_exchange_list["items"][0]
        assert exchanges.structuredContent["data"][0]["id"] == rest_exchange_row["id"]
        assert exchanges.structuredContent["data"][0]["exchange_no"] == rest_exchange_row["exchange_no"]

        def assert_exchange_filter_pages(filters, expected_ids):
            rest_filtered = rest_client.get(
                "/after-sales/exchanges",
                params={**filters, "page": 1, "page_size": 20},
                headers=rest_headers,
            )
            assert rest_filtered.status_code == 200, filters
            rest_data = rest_filtered.json()["data"]
            rest_ids = [row["id"] for row in rest_data["items"]]
            assert rest_ids == expected_ids, filters
            rest_rows_by_id = {row["id"]: row for row in rest_data["items"]}
            for page_number in (1, 2):
                mcp_page = call(rt, "erp_search_after_sale_exchanges", {
                    **filters, "page": page_number, "page_size": 1,
                }).structuredContent
                assert mcp_page["page"]["total"] == rest_data["total"] == len(expected_ids), filters
                assert [row["id"] for row in mcp_page["data"]] == expected_ids[page_number - 1:page_number], filters
                assert mcp_page["page"]["has_more"] is (page_number < len(expected_ids)), filters
                for row in mcp_page["data"]:
                    assert_mcp_summary_matches_rest(
                        row, rest_rows_by_id[row["id"]],
                        ("exchanges", filters, row["id"]),
                    )

        for exchange_filters, exchange_ids in (
            ({"order_id": 1}, [2, 1]),
            ({"shop_id": 1}, [2, 1]),
            ({"status": "CONFIRMED"}, [2]),
            ({"keyword": "OTHER-EXCHANGE"}, [2]),
            ({"order_id": 1, "shop_id": 1, "status": "CONFIRMED"}, [2]),
            ({"status": "PENDING", "keyword": "AS-EXCHANGE-1"}, [1]),
            ({"order_id": 1, "keyword": "OTHER-EXCHANGE", "status": "CONFIRMED"}, [2]),
        ):
            assert_exchange_filter_pages(exchange_filters, exchange_ids)

        rest_exchange = rest_client.get("/after-sales/exchanges/1", headers=rest_headers)
        assert rest_exchange.status_code == 200
        rest_exchange_data = rest_exchange.json()["data"]
        mcp_exchange = exchange.structuredContent["data"]
        for field in (
            "id", "exchange_no", "order_id", "order_no", "shop_id", "shop_name",
            "status", "status_label", "exchange_qty", "is_cod", "is_return",
            "compensate_order_no", "compensate_direction", "created_at", "updated_at",
        ):
            if field in rest_exchange_data:
                if field == "exchange_qty":
                    assert Decimal(str(mcp_exchange[field])) == Decimal(str(rest_exchange_data[field]))
                else:
                    assert mcp_exchange[field] == rest_exchange_data[field], field
        assert "reason" not in mcp_exchange and "items" not in mcp_exchange
        rest_exchange_items = rest_exchange_data["items"]
        assert exchange_items.structuredContent["page"]["total"] == len(rest_exchange_items) == 2
        exchange_items_page_2 = call(rt, "erp_list_after_sale_exchange_items", {
            "exchange_id": 1, "page": 2, "page_size": 1,
        }).structuredContent
        assert exchange_items_page_2["page"]["has_more"] is False
        assert [row["id"] for row in exchange_items_page_2["data"]] == [
            rest_exchange_items[1]["id"]
        ]
        all_mcp_exchange_items = (
            exchange_items.structuredContent["data"] + exchange_items_page_2["data"]
        )
        for mcp_item, rest_item in zip(all_mcp_exchange_items, rest_exchange_items):
            for field in ("id", "product_id", "product_code", "product_name", "sku_id", "qty", "exchange_qty"):
                if field in rest_item:
                    if field in {"qty", "exchange_qty"}:
                        assert Decimal(str(mcp_item[field])) == Decimal(str(rest_item[field]))
                    else:
                        assert mcp_item[field] == rest_item[field], field

        rest_unbound_registers = rest_client.get(
            "/after-sales/registers", params={"bound": "false"}, headers=rest_headers,
        )
        assert rest_unbound_registers.status_code == 200
        rest_unbound_ids = [row["id"] for row in rest_unbound_registers.json()["data"]]
        assert [row["id"] for row in unbound_registers["data"]] == rest_unbound_ids == [3]

        finance_headers = {"Authorization": "Bearer " + create_access_token(3, "售后兼财务", [])}
        rest_finance_return = rest_client.get(
            "/after-sales/returns/1", headers=finance_headers,
        ).json()["data"]
        rest_finance_exchange = rest_client.get(
            "/after-sales/exchanges/1", headers=finance_headers,
        ).json()["data"]
        rest_finance_return_amount = Decimal(str(rest_finance_return["refund_amount"]))
        rest_finance_exchange_amount = Decimal(str(rest_finance_exchange["compensate_amount"]))

    finance_token = issue(
        factory, staff_id=3, tool_scopes=tools,
        permission_scopes=["module:order", "module:finance"],
    )["token"]
    finance_search = call(
        runtime(factory, finance_token, limits=MemoryLimits(rpm=100)),
        "erp_search_after_sale_returns", {"order_id": 1},
    )
    assert {row["refund_amount"] for row in finance_search.structuredContent["data"]} == {
        "25.00", "8.00",
    }
    finance_detail = call(
        runtime(factory, finance_token, limits=MemoryLimits(rpm=100)),
        "erp_get_after_sale_return", {"return_id": 1},
    )
    assert finance_detail.structuredContent["data"]["refund_amount"] == "25.00"
    finance_items = call(
        runtime(factory, finance_token, limits=MemoryLimits(rpm=100)),
        "erp_list_after_sale_return_items", {"return_id": 1},
    )
    assert {row["refund_amount"] for row in finance_items.structuredContent["data"]} == {
        "10.00", "15.00",
    }
    finance_exchange = call(
        runtime(factory, finance_token, limits=MemoryLimits(rpm=100)),
        "erp_get_after_sale_exchange", {"exchange_id": 1},
    )
    assert finance_exchange.structuredContent["data"]["compensate_amount"] == "12.50"
    assert Decimal(finance_detail.structuredContent["data"]["refund_amount"]) == rest_finance_return_amount
    assert Decimal(finance_exchange.structuredContent["data"]["compensate_amount"]) == rest_finance_exchange_amount

    assert call(rt, "erp_search_after_sale_returns", {"status": "UNKNOWN"}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_search_after_sale_returns", {"page_size": 101}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_search_after_sale_returns", {
        "start_time": "2026-10-12T10:00:00+08:00",
        "end_time": "2026-10-11T10:00:00+08:00",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_after_sale_return", {"return_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"
    with factory() as db:
        after = tuple(db.scalar(select(func.count(model.id))) for model in (
            ReturnOrder, ReturnOrderItem, ExchangeOrder, ExchangeOrderItem, ReturnRegister,
        ))
    assert after == before


def test_approval_management_read_tools_keep_personal_scope_and_hide_sensitive_fields(factory):
    import json
    from decimal import Decimal
    from sqlalchemy import delete, event, func
    from app.api.approval import router as approval_router
    from app.api.approval_manage import router as approval_manage_router
    from app.core.data_scope import clear_scope_policy
    from app.models.approval import ApprovalFlow, ApprovalRecord
    from app.models.party import Supplier
    from app.models.purchase import PurchasePlan

    tools = [
        ACCESS_TOOL, "erp_list_approval_flows", "erp_list_approval_doc_types",
        "erp_list_approval_flow_configs", "erp_list_my_approvals",
        "erp_get_my_approval_detail",
    ]
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Role(id=3, company_id=1, code="ADMIN", name="管理员"),
            Staff(id=3, company_id=1, name="非审批管理员"),
            Supplier(id=1, code="APR-SUP", name="审批供应商"),
            PurchasePlan(
                id=1, plan_no="APR-PLAN-1", supplier_id=1, created_by=1,
                status="IN_PROGRESS", audit_status="PENDING",
                total_qty=Decimal("2"), total_amount=Decimal("123.45"),
                remark="秘密采购备注",
            ),
            PurchasePlan(
                id=2, plan_no="APR-PLAN-2", supplier_id=1, created_by=1,
                status="IN_PROGRESS", audit_status="PENDING",
                total_qty=Decimal("1"), total_amount=Decimal("10.00"),
            ),
            ApprovalFlow(
                id=1, name="采购计划流程", doc_type="purchase_plan",
                approvers=json.dumps([{
                    "step": 1, "name": "主管审批", "approver_ids": [1],
                }]), created_by=1,
            ),
            ApprovalFlow(
                id=2, name="采购订单流程", doc_type="purchase_order",
                approvers=json.dumps([{
                    "step": 1, "name": "财务审批", "approver_ids": [1],
                }]), created_by=1,
            ),
            ApprovalRecord(
                id=1, flow_id=1, doc_type="purchase_plan", doc_id=1,
                doc_no="APR-PLAN-1", step=0, approver_id=1,
                action="SUBMIT", status="DONE", comment="敏感审批意见",
                reason="敏感审批原因",
            ),
            ApprovalRecord(
                id=2, flow_id=1, doc_type="purchase_plan", doc_id=1,
                doc_no="APR-PLAN-1", step=1, action="APPROVE",
                status="PENDING",
            ),
            *[
                ApprovalRecord(
                    id=record_id, flow_id=1, doc_type="purchase_plan", doc_id=1,
                    step=1, action="APPROVE", status="APPROVED",
                    comment="长历史敏感意见", reason="长历史敏感原因",
                )
                for record_id in range(3, 1003)
            ],
            ApprovalRecord(
                id=1003, flow_id=1, doc_type="purchase_plan", doc_id=2,
                doc_no="APR-PLAN-2", step=0, action="SUBMIT", status="DONE",
            ),
            ApprovalRecord(
                id=1004, flow_id=1, doc_type="purchase_plan", doc_id=2,
                doc_no="APR-PLAN-2", step=1, action="APPROVE", status="PENDING",
            ),
            ApprovalRecord(
                id=1005, flow_id=1, doc_type="purchase_order", doc_id=2,
                doc_no="APR-ORDER-2", step=1, action="APPROVE", status="APPROVED",
            ),
            StaffRole(staff_id=3, role_id=3),
        ])
        db.commit()
        before = (
            db.scalar(select(func.count(PurchasePlan.id))),
            db.scalar(select(func.count(ApprovalFlow.id))),
            db.scalar(select(func.count(ApprovalRecord.id))),
        )

    purchaser_token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:approval", "module:purchase"],
    )["token"]
    purchaser_rt = runtime(factory, purchaser_token, limits=MemoryLimits(rpm=100))
    doc_types = call(purchaser_rt, "erp_list_approval_doc_types")
    assert "purchase_plan" in {row["code"] for row in doc_types.structuredContent["data"]}
    assert "stock_in" not in {row["code"] for row in doc_types.structuredContent["data"]}

    flows = call(purchaser_rt, "erp_list_approval_flows", {
        "doc_type": "purchase_plan", "page_size": 1,
    })
    assert flows.structuredContent["page"]["total"] == 1
    assert flows.structuredContent["data"] == [{
        "id": 1, "name": "采购计划流程", "doc_type": "purchase_plan",
    }]
    assert call(purchaser_rt, "erp_list_approval_flows", {
        "doc_type": "purchase_order",
    }).structuredContent["data"] == [{
        "id": 2, "name": "采购订单流程", "doc_type": "purchase_order",
    }]
    flow_page_1 = call(purchaser_rt, "erp_list_approval_flows", {
        "page": 1, "page_size": 1,
    }).structuredContent
    flow_page_2 = call(purchaser_rt, "erp_list_approval_flows", {
        "page": 2, "page_size": 1,
    }).structuredContent
    assert flow_page_1["page"]["total"] == 2
    assert flow_page_1["page"]["has_more"] is True
    assert flow_page_2["page"]["has_more"] is False
    assert call(purchaser_rt, "erp_list_approval_flows", {
        "doc_type": "expense",
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(purchaser_rt, "erp_list_approval_flows", {
        "page_size": 101,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    pending = call(purchaser_rt, "erp_list_my_approvals", {"scope": "pending"})
    assert pending.structuredContent["page"]["total"] == 2
    pending_item = next(
        item for item in pending.structuredContent["data"]
        if item["doc_no"] == "APR-PLAN-1"
    )
    assert pending_item["doc_no"] == "APR-PLAN-1"
    assert "submitted_by" not in pending_item and "current_approvers" not in pending_item
    submitted = call(purchaser_rt, "erp_list_my_approvals", {"scope": "submitted"})
    assert submitted.structuredContent["page"]["total"] == 1
    empty = call(purchaser_rt, "erp_list_my_approvals", {"scope": "processed"})
    assert empty.structuredContent["data"] == []

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(approval_router)
    rest_app.include_router(approval_manage_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    purchaser_headers = {
        "Authorization": "Bearer " + create_access_token(1, "采购审批", []),
    }
    with TestClient(rest_app) as rest_client:
        rest_flows = rest_client.get("/approval/flows", headers=purchaser_headers)
        assert rest_flows.status_code == 200
        rest_flow_rows = rest_flows.json()["data"]
        assert flow_page_1["page"]["total"] == len(rest_flow_rows) == 2
        assert {
            (row["id"], row["name"], row["doc_type"])
            for row in flow_page_1["data"] + flow_page_2["data"]
        } == {
            (row["id"], row["name"], row["doc_type"])
            for row in rest_flow_rows
        }

        rest_doc_types = rest_client.get(
            "/approval-manage/doc-types", headers=purchaser_headers,
        )
        assert rest_doc_types.status_code == 200
        rest_doc_type_codes = {row["code"] for row in rest_doc_types.json()["data"]}
        mcp_doc_type_codes = {row["code"] for row in doc_types.structuredContent["data"]}
        # REST列出完整字典；MCP按采购角色收敛为可办理类型。
        assert mcp_doc_type_codes <= rest_doc_type_codes
        assert "purchase_plan" in mcp_doc_type_codes
        assert "stock_in" not in mcp_doc_type_codes

        rest_pending = rest_client.get(
            "/approval-manage/my-approvals", params={"scope": "pending"},
            headers=purchaser_headers,
        )
        assert rest_pending.status_code == 200
        rest_pending_data = rest_pending.json()["data"]
        assert pending.structuredContent["page"]["total"] == rest_pending_data["total"] == 2
        assert {
            (row["doc_type"], row["doc_id"])
            for row in pending.structuredContent["data"]
        } == {
            (row["doc_type"], row["doc_id"])
            for row in rest_pending_data["items"]
        }

        def assert_my_approval_pages(scope, *, doc_type=None, keyword=None):
            filters = {"scope": scope}
            if doc_type is not None:
                filters["doc_type"] = doc_type
            if keyword is not None:
                filters["keyword"] = keyword
            rest_filtered = rest_client.get(
                "/approval-manage/my-approvals",
                params={**filters, "page": 1, "page_size": 200},
                headers=purchaser_headers,
            )
            assert rest_filtered.status_code == 200, filters
            rest_data = rest_filtered.json()["data"]
            rest_ids = [(row["doc_type"], row["doc_id"]) for row in rest_data["items"]]
            mcp_rows = []
            for page_number in (1, 2):
                mcp_page = call(purchaser_rt, "erp_list_my_approvals", {
                    **filters, "page": page_number, "page_size": 1,
                }).structuredContent
                assert mcp_page["page"]["total"] == rest_data["total"] == len(rest_ids), filters
                assert mcp_page["page"]["has_more"] is (page_number < len(rest_ids)), filters
                mcp_rows.extend(mcp_page["data"])
            assert [(row["doc_type"], row["doc_id"]) for row in mcp_rows] == rest_ids, filters

        assert_my_approval_pages("pending")
        assert_my_approval_pages("pending", doc_type="purchase_plan")
        assert_my_approval_pages("pending", keyword="APR-PLAN-1")
        assert_my_approval_pages("submitted")
        assert_my_approval_pages("processed")

        rest_approval_detail_response = rest_client.get(
            "/approval-manage/my-approvals/purchase_plan/1",
            headers=purchaser_headers,
        )
        assert rest_approval_detail_response.status_code == 200
        rest_approval_detail = rest_approval_detail_response.json()["data"]

    detail_statements = []

    def capture_detail_sql(_conn, _cursor, statement, *_args):
        if "from approval_record" in statement.lower():
            detail_statements.append(statement.lower())

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_detail_sql)
    try:
        detail = call(purchaser_rt, "erp_get_my_approval_detail", {
            "doc_type": "purchase_plan", "doc_id": 1,
        })
    finally:
        event.remove(engine, "before_cursor_execute", capture_detail_sql)
    assert detail.structuredContent["data"]["detail"]["plan_no"] == "APR-PLAN-1"
    assert detail.structuredContent["data"]["detail"]["total_amount"] == "123.45"
    assert "remark" not in detail.structuredContent["data"]["detail"]
    assert detail.structuredContent["data"]["records_truncated"] is True
    assert detail.structuredContent["data"]["approval"]["approved_steps_truncated"] is True
    assert len(detail.structuredContent["data"]["records"]) == 100
    assert len(detail_statements) >= 7
    record_page_sql = next(
        statement for statement in detail_statements
        if "approval_record.step, approval_record.action" in statement
        and "limit" in statement
    )
    assert "limit" in record_page_sql
    assert "comment" not in record_page_sql and "reason" not in record_page_sql
    assert all(not ({"approver_id", "approver_name", "comment", "reason"} & set(row))
               for row in detail.structuredContent["data"]["records"])
    assert "敏感审批意见" not in detail.model_dump_json()
    assert "敏感审批原因" not in detail.model_dump_json()
    assert "长历史敏感意见" not in detail.model_dump_json()
    assert "长历史敏感原因" not in detail.model_dump_json()
    mcp_approval_detail = detail.structuredContent["data"]
    for field in ("doc_type", "doc_type_label", "doc_id", "doc_no"):
        assert mcp_approval_detail[field] == rest_approval_detail[field]
    assert mcp_approval_detail["approval"]["flow_name"] == rest_approval_detail["flow"]["name"]
    for field in ("status", "current_step", "total_steps", "approved_steps"):
        rest_value = rest_approval_detail["approval"].get(field)
        if field == "approved_steps":
            rest_value = rest_value[:100]
        assert mcp_approval_detail["approval"][field] == rest_value
    for field in ("id", "name", "doc_type"):
        assert mcp_approval_detail["flow"][field] == rest_approval_detail["flow"][field]
    for field in ("plan_no", "status", "audit_status", "total_qty", "total_amount"):
        assert mcp_approval_detail["detail"][field] == rest_approval_detail["detail"][field]
    rest_records_by_id = {
        row["id"]: row for row in rest_approval_detail["records"]
    }
    assert len(rest_records_by_id) == 1002
    assert mcp_approval_detail["records_truncated"] is True
    assert len(mcp_approval_detail["records"]) == 100
    for compact_record in mcp_approval_detail["records"]:
        rest_record = rest_records_by_id[compact_record["id"]]
        for field in ("step", "step_name", "action", "status"):
            assert compact_record[field] == rest_record[field]
        assert not ({"approver_id", "approver_name", "comment", "reason"} & set(compact_record))
    assert "remark" in rest_approval_detail["detail"]
    assert "remark" not in mcp_approval_detail["detail"]
    same_id_other_type = call(purchaser_rt, "erp_get_my_approval_detail", {
        "doc_type": "purchase_plan", "doc_id": 2,
    })
    assert same_id_other_type.structuredContent["data"]["approval"]["approved_steps"] == []
    assert same_id_other_type.structuredContent["data"]["approval"]["approved_steps_truncated"] is False

    # 普通审批权限不能读取审批流配置；即使管理员能配置，也不能越过本人参与范围看单据。
    assert call(
        purchaser_rt, "erp_list_approval_flow_configs",
    ).structuredContent["code"] == "FORBIDDEN"
    no_approval_scope_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    assert call(
        runtime(factory, no_approval_scope_token), "erp_list_approval_flows", {},
    ).structuredContent["code"] == "FORBIDDEN"
    admin_token = issue(
        factory, staff_id=3, tool_scopes=tools,
        permission_scopes=["module:approval"],
    )["token"]
    admin_rt = runtime(factory, admin_token, limits=MemoryLimits(rpm=100))
    configs_page_1 = call(admin_rt, "erp_list_approval_flow_configs", {
        "page": 1, "page_size": 1,
    }).structuredContent
    configs_page_2 = call(admin_rt, "erp_list_approval_flow_configs", {
        "page": 2, "page_size": 1,
    }).structuredContent
    assert configs_page_1["page"]["total"] == 2
    assert configs_page_1["page"]["has_more"] is True
    assert configs_page_2["page"]["has_more"] is False
    configs = configs_page_1["data"][0]
    assert configs["steps"][0]["approver_count"] == 1
    assert "approver_ids" not in configs["steps"][0]
    admin_headers = {
        "Authorization": "Bearer " + create_access_token(3, "审批管理员", []),
    }
    with TestClient(rest_app) as rest_client:
        rest_configs = rest_client.get(
            "/approval-manage/flows", headers=admin_headers,
        )
        assert rest_configs.status_code == 200
        rest_config_rows = rest_configs.json()["data"]
        assert configs_page_1["page"]["total"] == len(rest_config_rows) == 2
        mcp_config_rows = configs_page_1["data"] + configs_page_2["data"]
        assert [row["id"] for row in mcp_config_rows] == [row["id"] for row in rest_config_rows]
        for mcp_config, rest_config in zip(mcp_config_rows, rest_config_rows):
            for field in ("id", "name", "doc_type"):
                assert mcp_config[field] == rest_config[field], field
        assert all("approver_ids" not in row["steps"][0] for row in mcp_config_rows)
    assert call(admin_rt, "erp_get_my_approval_detail", {
        "doc_type": "purchase_plan", "doc_id": 1,
    }).structuredContent["code"] == "FORBIDDEN"
    assert call(purchaser_rt, "erp_list_my_approvals", {"scope": "invalid"}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(purchaser_rt, "erp_list_my_approvals", {"page_size": 101}).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(purchaser_rt, "erp_get_my_approval_detail", {
        "doc_type": "unsupported", "doc_id": 1,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        after = (
            db.scalar(select(func.count(PurchasePlan.id))),
            db.scalar(select(func.count(ApprovalFlow.id))),
            db.scalar(select(func.count(ApprovalRecord.id))),
        )
    assert after == before


def test_my_approval_list_uses_bounded_sql_batches_and_keeps_page_order(factory, monkeypatch):
    from sqlalchemy import delete, event
    from app.models.approval import ApprovalRecord
    from app.services import approval_manage_service

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            ApprovalRecord(
                id=record_id, doc_type="purchase_plan", doc_id=record_id,
                doc_no=f"MCP-APP-{record_id:04d}", step=0, approver_id=1,
                action="SUBMIT", status="DONE", audit_time=utc_now(),
            )
            for record_id in range(1, 1206)
        ])
        db.commit()

    def build_list_item(
        _db, *, doc_type, doc_id, records, approval_state=None, compact=False,
    ):
        latest = max(records, key=lambda record: record.id)
        return {
            "doc_type": doc_type, "doc_type_label": "采购计划", "doc_id": doc_id,
            "doc_no": None, "latest_action": latest.action,
            "latest_action_status": latest.status, "_sort_id": latest.id,
            "approval_status": "APPROVED", "audit_status": "APPROVED",
            "business_status": "PENDING", "current_step": None,
            "flow_id": None, "flow_name": None,
            "latest_action_at": None, "submitted_at": None, "total_steps": 1,
        }

    monkeypatch.setattr(approval_manage_service, "_build_list_item", build_list_item)
    token = issue(
        factory,
        tool_scopes=[ACCESS_TOOL, "erp_list_my_approvals"],
        permission_scopes=["module:approval"],
    )["token"]
    statements = []

    def capture_candidate_select(_conn, _cursor, statement, parameters, *_args):
        if "approval_document_candidates" in statement.lower():
            statements.append((statement.lower(), parameters))

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_candidate_select)
    try:
        result = call(runtime(factory, token), "erp_list_my_approvals", {
            "scope": "submitted", "page": 2, "page_size": 7,
        }).structuredContent
    finally:
        event.remove(engine, "before_cursor_execute", capture_candidate_select)

    assert result["page"]["total"] == 1205
    assert result["page"]["has_more"] is True
    assert [row["doc_id"] for row in result["data"]] == list(range(1198, 1191, -1))
    assert len(statements) == 7  # 6批×200条 + 5条；每条候选SQL均有LIMIT。
    assert all("limit" in statement for statement, _ in statements)
    for _statement, parameters in statements:
        values = parameters.values() if isinstance(parameters, dict) else parameters
        assert 200 in values


def test_my_approval_list_summarizes_long_history_with_bounded_representatives(
    factory, monkeypatch,
):
    from app.models.approval import ApprovalFlow, ApprovalRecord
    from app.services import approval_manage_service

    now = utc_now().replace(tzinfo=None)
    with factory() as db:
        db.add(ApprovalFlow(
            id=1, name="长历史审批流", doc_type="purchase_plan",
            approvers=json.dumps([{
                "step": 1, "name": "审批", "approver_ids": [1],
            }]),
        ))
        db.add_all([
            ApprovalRecord(
                id=1, flow_id=1, doc_type="purchase_plan", doc_id=900,
                doc_no="LONG-HISTORY-900", step=0, approver_id=1,
                action="SUBMIT", status="DONE", audit_time=now,
            ),
            *[
                ApprovalRecord(
                    id=record_id, flow_id=1, doc_type="purchase_plan", doc_id=900,
                    doc_no=None, step=1, approver_id=1, action="APPROVE",
                    status="APPROVED", audit_time=now,
                )
                for record_id in range(2, 1002)
            ],
        ])
        db.commit()

        candidates = approval_manage_service._approval_document_batch(
            db, scope="submitted", staff_id=1, doc_type="purchase_plan",
            before_sort_id=None,
        )
        bounded_records, states = approval_manage_service._approval_list_record_summaries(
            db, candidates,
        )
        mysql57_records = approval_manage_service._approval_list_representative_records(
            db, [("purchase_plan", 900)], use_window_functions=False,
        )
        full_history = approval_manage_service.list_records(db, "purchase_plan", 900)

        assert candidates == [("purchase_plan", 900, 1001)]
        assert len(full_history) == 1001
        assert len(bounded_records[("purchase_plan", 900)]) <= 7
        assert {row.id for row in mysql57_records} == {
            row.id for row in bounded_records[("purchase_plan", 900)]
        }
        assert states[("purchase_plan", 900)] == {
            "status": "APPROVED", "current_step": None,
            "total_steps": 1, "approved_steps": [],
        }

        monkeypatch.setattr(
            approval_manage_service, "_load_detail",
            lambda *_args, **_kwargs: {
                "plan_no": "LONG-HISTORY-900",
                "status": "APPROVED", "audit_status": "APPROVED",
            },
        )
        legacy_item = approval_manage_service._build_list_item(
            db, doc_type="purchase_plan", doc_id=900,
            records=full_history, compact=True,
        )
        bounded_item = approval_manage_service._build_list_item(
            db, doc_type="purchase_plan", doc_id=900,
            records=bounded_records[("purchase_plan", 900)],
            approval_state=states[("purchase_plan", 900)], compact=True,
        )
        assert bounded_item == legacy_item


def test_mysql57_approval_summary_sql_uses_bounded_limit_branches():
    from sqlalchemy.dialects import mysql
    from app.services.approval_manage_service import (
        _legacy_approval_representative_id_query,
    )

    query = _legacy_approval_representative_id_query([
        ("purchase_plan", 900), ("purchase_order", 901),
    ])
    sql = str(query.compile(dialect=mysql.dialect())).lower()
    assert "row_number" not in sql and " over (" not in sql
    assert sql.count("limit") == 14
    assert "union all" in sql


def test_accounting_reference_tools_are_finance_scoped_paged_and_fixed(factory):
    from sqlalchemy import delete, event, func
    from app.api.account_subject import router as account_subject_router
    from app.core.data_scope import clear_scope_policy
    from app.models.finance import AccountSubject, ProjectCategory

    tools = [
        ACCESS_TOOL,
        "erp_account_subject_get_subject_meta",
        "erp_account_subject_list_subjects",
        "erp_account_subject_get_subject",
        "erp_account_subject_list_project_categories",
        "erp_account_subject_get_project_category",
    ]
    with factory() as db:
        db.add_all([
            Role(id=3, company_id=1, code="FINANCE", name="财务"),
            Role(id=4, company_id=1, code="WAREHOUSE", name="仓库"),
            Staff(id=4, company_id=1, name="仓库员工"),
        ])
        db.flush()
        db.add_all([
            StaffRole(staff_id=1, role_id=3),
            StaffRole(staff_id=4, role_id=4),
        ])
        db.commit()
    finance_token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:finance"],
    )["token"]
    finance_rt = runtime(factory, finance_token, limits=MemoryLimits(rpm=100))

    def call_bounded_page(tool_name, arguments, table_name):
        statements = []

        def capture_select(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = factory.kw["bind"]
        event.listen(engine, "before_cursor_execute", capture_select)
        try:
            result = call(finance_rt, tool_name, arguments)
        finally:
            event.remove(engine, "before_cursor_execute", capture_select)
        assert not result.isError, result.structuredContent
        assert any(
            table_name in statement.lower() and "LIMIT" in statement.upper()
            for statement in statements
        ), f"{tool_name} 未观察到目标表 SQL LIMIT: {statements}"
        return result.structuredContent

    multi_company_probes = {
        "erp_account_subject_get_subject_meta": {},
        "erp_account_subject_list_subjects": {},
        "erp_account_subject_get_subject": {"subject_id": 1},
        "erp_account_subject_list_project_categories": {},
        "erp_account_subject_get_project_category": {"category_id": 1},
    }
    for tool_name, arguments in multi_company_probes.items():
        result = call(finance_rt, tool_name, arguments)
        if tool_name.endswith("_meta"):
            assert not result.isError, (tool_name, result.structuredContent)
        elif "list_" in tool_name:
            assert not result.isError, (tool_name, result.structuredContent)
            assert result.structuredContent["page"]["total"] == 0
        else:
            # 详情 ID 不存在时仍拒绝；这是记录级检查，不是多公司全局拦截。
            assert result.isError and result.structuredContent["code"] == "FORBIDDEN", tool_name

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            ProjectCategory(id=1, code="PROJ-01", name="售后项目", remark="不外发备注"),
            ProjectCategory(id=2, code="PROJ-02", name="研发项目", remark="另一个备注"),
        ])
        db.flush()
        db.add_all([
            AccountSubject(
                id=1, code="1001", name="库存现金", category="ASSET",
                balance_direction="DEBIT", is_cash=True, is_enabled=True,
            ),
            AccountSubject(
                id=2, code="1002", name="银行存款", category="ASSET",
                balance_direction="DEBIT", is_bank=True,
                project_category_id=1, is_enabled=True,
            ),
            AccountSubject(
                id=3, code="2001", name="研发材料", category="ASSET",
                balance_direction="CREDIT", is_auxiliary=True,
                is_qty_amount=True, measure_unit="件", project_category_id=2,
                is_enabled=False,
            ),
        ])
        db.commit()
        before = (
            db.scalar(select(func.count(AccountSubject.id))),
            db.scalar(select(func.count(ProjectCategory.id))),
        )

    meta = call(finance_rt, "erp_account_subject_get_subject_meta")
    assert meta.structuredContent["data"]["seed_version"] == "KINGDEE_STANDARD_CHART_V1"
    assert "ASSET" in {row["code"] for row in meta.structuredContent["data"]["subject_categories"]}

    subjects = call_bounded_page("erp_account_subject_list_subjects", {
        "category": "ASSET", "keyword": "10", "page": 1, "page_size": 1,
    }, "account_subject")
    assert subjects["page"]["total"] == 2
    assert subjects["page"]["has_more"] is True
    assert subjects["data"][0]["code"] == "1001"
    subject = call(finance_rt, "erp_account_subject_get_subject", {"subject_id": 2})
    assert subject.structuredContent["data"]["project_category_name"] == "售后项目"
    assert "remark" not in subject.structuredContent["data"]
    assert "balance" not in subject.structuredContent["data"]

    categories = call_bounded_page("erp_account_subject_list_project_categories", {
        "page": 1, "page_size": 1,
    }, "project_category")
    category_direct_probe = call(
        finance_rt, "erp_account_subject_list_project_categories",
        {"page": 1, "page_size": 1},
    )
    assert category_direct_probe.structuredContent["data"] == categories["data"]
    assert categories["page"]["has_more"] is True
    assert "remark" not in categories["data"][0]
    category = call(finance_rt, "erp_account_subject_get_project_category", {
        "category_id": 1,
    })
    assert category.structuredContent["data"]["code"] == "PROJ-01"
    assert "remark" not in category.structuredContent["data"]

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(account_subject_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "财务", [])}
    with TestClient(rest_app) as rest_client:
        rest_meta = rest_client.get("/account-subjects/meta", headers=rest_headers)
        assert rest_meta.status_code == 200
        assert meta.structuredContent["data"] == rest_meta.json()["data"]

        rest_subjects = rest_client.get(
            "/account-subjects",
            params={"category": "ASSET", "keyword": "10", "page": 1, "page_size": 1},
            headers=rest_headers,
        )
        assert rest_subjects.status_code == 200
        rest_subject_data = rest_subjects.json()["data"]
        assert subjects["page"]["total"] == rest_subject_data["total"] == 2
        assert subjects["page"]["has_more"] is True
        assert subjects["data"][0]["id"] == rest_subject_data["items"][0]["id"]
        assert subjects["data"][0]["code"] == rest_subject_data["items"][0]["code"]
        for field, value in subjects["data"][0].items():
            assert rest_subject_data["items"][0][field] == value, field

        subject_page_2 = call_bounded_page("erp_account_subject_list_subjects", {
            "category": "ASSET", "keyword": "10", "page": 2, "page_size": 1,
        }, "account_subject")
        rest_subject_page_2 = rest_client.get(
            "/account-subjects",
            params={"category": "ASSET", "keyword": "10", "page": 2, "page_size": 1},
            headers=rest_headers,
        )
        assert rest_subject_page_2.status_code == 200
        rest_subject_page_2_data = rest_subject_page_2.json()["data"]
        assert subject_page_2["page"]["total"] == rest_subject_page_2_data["total"] == 2
        assert subject_page_2["page"]["has_more"] is False
        assert [row["id"] for row in subject_page_2["data"]] == [
            row["id"] for row in rest_subject_page_2_data["items"]
        ] == [2]
        for field, value in subject_page_2["data"][0].items():
            assert rest_subject_page_2_data["items"][0][field] == value, field

        subject_page_3 = call_bounded_page("erp_account_subject_list_subjects", {
            "category": "ASSET", "keyword": "10", "page": 3, "page_size": 1,
        }, "account_subject")
        rest_subject_page_3 = rest_client.get(
            "/account-subjects",
            params={"category": "ASSET", "keyword": "10", "page": 3, "page_size": 1},
            headers=rest_headers,
        )
        assert rest_subject_page_3.status_code == 200
        assert subject_page_3["page"]["total"] == rest_subject_page_3.json()["data"]["total"] == 2
        assert subject_page_3["data"] == []
        assert subject_page_3["page"]["has_more"] is False

        filter_cases = [
            ({"category": "ASSET"}, [1, 2, 3]),
            ({"balance_direction": "CREDIT"}, [3]),
            ({"is_enabled": False}, [3]),
            ({"is_auxiliary": True}, [3]),
            ({"is_qty_amount": True}, [3]),
            ({"project_category_id": 2}, [3]),
            ({"keyword": "库存现金"}, [1]),
            ({"category": "ASSET", "balance_direction": "DEBIT", "is_enabled": True}, [1, 2]),
            ({"category": "ASSET", "balance_direction": "CREDIT", "is_enabled": False,
              "is_auxiliary": True, "is_qty_amount": True, "project_category_id": 2,
              "keyword": "研发"}, [3]),
            ({"is_enabled": True, "keyword": "不存在的科目"}, []),
        ]
        for filters, expected_ids in filter_cases:
            mcp_filtered = call_bounded_page("erp_account_subject_list_subjects", {
                **filters, "page": 1, "page_size": 20,
            }, "account_subject")
            rest_filtered = rest_client.get(
                "/account-subjects",
                params={**filters, "page": 1, "page_size": 20},
                headers=rest_headers,
            )
            assert rest_filtered.status_code == 200, filters
            rest_filtered_data = rest_filtered.json()["data"]
            assert mcp_filtered["page"]["total"] == rest_filtered_data["total"] == len(expected_ids), filters
            assert mcp_filtered["page"]["has_more"] is False, filters
            assert [row["id"] for row in mcp_filtered["data"]] == [
                row["id"] for row in rest_filtered_data["items"]
            ] == expected_ids, filters
            rest_rows_by_id = {row["id"]: row for row in rest_filtered_data["items"]}
            for row in mcp_filtered["data"]:
                for field, value in row.items():
                    assert rest_rows_by_id[row["id"]][field] == value, (filters, field)

        rest_subject = rest_client.get("/account-subjects/2", headers=rest_headers)
        assert rest_subject.status_code == 200
        for field, value in subject.structuredContent["data"].items():
            assert rest_subject.json()["data"][field] == value, field

        rest_categories = rest_client.get("/project-categories", headers=rest_headers)
        assert rest_categories.status_code == 200
        rest_category_rows = rest_categories.json()["data"]
        assert categories["page"]["total"] == len(rest_category_rows) == 2
        assert categories["data"][0]["id"] == rest_category_rows[0]["id"]
        assert categories["data"][0]["code"] == rest_category_rows[0]["code"]
        for field, value in categories["data"][0].items():
            assert rest_category_rows[0][field] == value, field
        categories_page_2 = call_bounded_page("erp_account_subject_list_project_categories", {
            "page": 2, "page_size": 1,
        }, "project_category")
        assert categories_page_2["page"]["total"] == 2
        assert categories_page_2["page"]["has_more"] is False
        assert [row["id"] for row in categories["data"]] == [
            rest_category_rows[0]["id"]
        ]
        assert [row["id"] for row in categories_page_2["data"]] == [
            rest_category_rows[1]["id"]
        ]
        for field, value in categories_page_2["data"][0].items():
            assert rest_category_rows[1][field] == value, field
        categories_page_3 = call_bounded_page("erp_account_subject_list_project_categories", {
            "page": 3, "page_size": 1,
        }, "project_category")
        assert categories_page_3["page"]["total"] == 2
        assert categories_page_3["data"] == []
        assert categories_page_3["page"]["has_more"] is False

        rest_category = rest_client.get("/project-categories/1", headers=rest_headers)
        assert rest_category.status_code == 200
        for field, value in category.structuredContent["data"].items():
            assert rest_category.json()["data"][field] == value, field

    assert call(finance_rt, "erp_account_subject_list_subjects", {
        "category": "INVALID",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(finance_rt, "erp_account_subject_list_subjects", {
        "page_size": 101,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(finance_rt, "erp_account_subject_get_subject", {
        "subject_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    warehouse_token = issue(
        factory, staff_id=4, tool_scopes=tools,
        permission_scopes=["module:stock"],
    )["token"]
    assert call(
        runtime(factory, warehouse_token), "erp_account_subject_list_subjects",
    ).structuredContent["code"] == "FORBIDDEN"
    with factory() as db:
        after = (
            db.scalar(select(func.count(AccountSubject.id))),
            db.scalar(select(func.count(ProjectCategory.id))),
        )
    assert after == before


def test_purchase_plan_detail_and_items_are_owned_paged_and_fixed(factory):
    from decimal import Decimal
    from sqlalchemy import func
    from app.models.party import Supplier
    from app.models.product import Product
    from app.models.purchase import (
        PurchasePlan, PurchasePlanCreationRequest, PurchasePlanItem,
    )

    tools = [
        ACCESS_TOOL,
        "erp_search_purchase_plans",
        "erp_get_purchase_plan",
        "erp_list_purchase_plan_items",
        "erp_get_purchase_plan_creation_request",
    ]
    with factory() as db:
        # 保留第二家公司，验证采购计划及关联明细只返回当前凭据公司的记录。
        db.add(Staff(id=3, company_id=1, name="其他制单人"))
        db.add_all([
            Supplier(id=1, code="SUP-PLAN", name="计划测试供应商", remark="不外发供应商备注"),
            Supplier(id=2, code="SUP-PLAN-2", name="计划筛选供应商"),
        ])
        db.add_all([
            Product(id=1, name="计划商品一", code="PLAN-P1", description="不外发描述"),
            Product(id=2, name="计划商品二", code="PLAN-P2"),
            PurchasePlan(
                id=1, plan_no="MCP-PLAN-DETAIL", supplier_id=1, created_by=1,
                total_qty=Decimal("5"), total_amount=Decimal("25.50"),
                remark="不外发计划备注",
            ),
            PurchasePlan(
                id=2, plan_no="MCP-PLAN-OTHER", supplier_id=1, created_by=3,
                total_qty=Decimal("9"), total_amount=Decimal("90.00"),
            ),
            PurchasePlan(
                id=3, plan_no="MCP-PLAN-FILTER", supplier_id=2, created_by=1,
                status="FINISHED", audit_status="APPROVED",
                total_qty=Decimal("7"), total_amount=Decimal("77.00"),
            ),
            PurchasePlanCreationRequest(
                id=1, company_id=1, staff_id=1,
                request_id="00000000-0000-4000-8000-000000000001",
                payload_hash="a" * 64, status="SUCCEEDED", plan_id=1,
                result_json='{"id":1,"remark":"private","approval":{"approver_ids":[999]},"items":[{"amount":"private"}]}',
            ),
            PurchasePlanCreationRequest(
                id=2, company_id=1, staff_id=1,
                request_id="00000000-0000-4000-8000-000000000002",
                payload_hash="b" * 64, status="PENDING",
            ),
            PurchasePlanCreationRequest(
                id=3, company_id=1, staff_id=3,
                request_id="00000000-0000-4000-8000-000000000003",
                payload_hash="c" * 64, status="SUCCEEDED", plan_id=1,
                result_json='{"remark":"other employee"}',
            ),
            PurchasePlanCreationRequest(
                id=4, company_id=1, staff_id=1,
                request_id="00000000-0000-4000-8000-000000000004",
                payload_hash="d" * 64, status="CORRUPT",
            ),
        ])
        db.flush()
        db.add_all([
            PurchasePlanItem(
                id=1, plan_id=1, product_id=1, planned_qty=Decimal("2"),
                purchased_qty=Decimal("1"), purchase_price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
            PurchasePlanItem(
                id=2, plan_id=1, product_id=2, planned_qty=Decimal("3"),
                purchased_qty=Decimal("0"), purchase_price=Decimal("5.50"),
                amount=Decimal("16.50"),
            ),
        ])
        db.commit()
        before = (
            db.scalar(select(func.count(PurchasePlan.id))),
            db.scalar(select(func.count(PurchasePlanItem.id))),
            db.scalar(select(func.count(PurchasePlanCreationRequest.id))),
        )

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)

    from app.api.purchase_plan import router as purchase_plan_router
    from app.core.data_scope import clear_scope_policy

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(purchase_plan_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "采购员", [])}
    with TestClient(rest_app) as rest_client:
        plan_summary_fields = {
            "id", "plan_no", "supplier_id", "supplier_name", "plan_date",
            "status", "audit_status", "total_qty", "total_amount",
            "created_at", "updated_at",
        }

        def compare_plan_list(filters, *, page=1, page_size=20):
            params = {**filters, "page": page, "page_size": page_size}
            rest_response = rest_client.get(
                "/purchase-plans", params=params, headers=rest_headers,
            )
            assert rest_response.status_code == 200, params
            rest_data = rest_response.json()["data"]
            mcp_result = call(rt, "erp_search_purchase_plans", params)
            assert not mcp_result.isError, (params, mcp_result.structuredContent)
            mcp_data = mcp_result.structuredContent
            assert mcp_data["page"] == {
                "page": page,
                "page_size": page_size,
                "total": rest_data["total"],
                "has_more": page * page_size < rest_data["total"],
            }, params
            assert [row["id"] for row in mcp_data["data"]] == [
                row["id"] for row in rest_data["items"]
            ], params
            for mcp_row, rest_row in zip(mcp_data["data"], rest_data["items"]):
                assert set(mcp_row) == plan_summary_fields, params
                for field in plan_summary_fields:
                    assert mcp_row[field] == rest_row[field], (params, field)
            return mcp_data

        assert compare_plan_list({}, page=1, page_size=1)["data"][0]["id"] == 3
        assert compare_plan_list({}, page=2, page_size=1)["data"][0]["id"] == 1
        assert [
            row["id"] for row in compare_plan_list({"supplier_id": 1})["data"]
        ] == [1]
        assert [
            row["id"] for row in compare_plan_list({"status": "FINISHED"})["data"]
        ] == [3]
        assert [
            row["id"] for row in compare_plan_list({"audit_status": "APPROVED"})["data"]
        ] == [3]
        assert [
            row["id"] for row in compare_plan_list({
                "supplier_id": 2,
                "status": "FINISHED",
                "audit_status": "APPROVED",
                "keyword": "MCP-PLAN-FILTER",
            })["data"]
        ] == [3]
        assert compare_plan_list({"supplier_id": 2, "status": "ABORTED"})["data"] == []
        assert call(rt, "erp_search_purchase_plans", {"status": "UNKNOWN"}).structuredContent[
            "code"
        ] == "INVALID_ARGUMENT"
        assert call(rt, "erp_search_purchase_plans", {"audit_status": "UNKNOWN"}).structuredContent[
            "code"
        ] == "INVALID_ARGUMENT"

        rest_list = rest_client.get(
            "/purchase-plans", params={
                "keyword": "MCP-PLAN-DETAIL", "page": 1, "page_size": 100,
            }, headers=rest_headers,
        )
        assert rest_list.status_code == 200
        rest_list_data = rest_list.json()["data"]
        mcp_list = call(rt, "erp_search_purchase_plans", {
            "keyword": "MCP-PLAN-DETAIL", "page": 1, "page_size": 100,
        }).structuredContent
        assert mcp_list["page"]["total"] == rest_list_data["total"] == 1
        assert mcp_list["data"][0]["plan_no"] == rest_list_data["items"][0]["plan_no"]
        assert mcp_list["data"][0]["id"] == rest_list_data["items"][0]["id"]

        rest_detail = rest_client.get("/purchase-plans/1", headers=rest_headers)
        assert rest_detail.status_code == 200
        mcp_detail = call(rt, "erp_get_purchase_plan", {
            "plan_id": 1,
        }).structuredContent["data"]
        rest_detail_data = rest_detail.json()["data"]
        assert set(mcp_detail) == plan_summary_fields
        for field in plan_summary_fields:
            assert mcp_detail[field] == rest_detail_data[field], field
        assert "remark" not in mcp_detail and "items" not in mcp_detail

        plan_item_fields = {
            "id", "product_id", "product_name", "product_code", "sku_id",
            "spec_name", "spec_code", "planned_qty", "purchased_qty",
            "remaining_qty", "purchase_price", "amount",
        }
        rest_items = rest_detail_data["items"]
        assert len(rest_items) == 2
        for page, rest_item in enumerate(rest_items, start=1):
            mcp_items = call(rt, "erp_list_purchase_plan_items", {
                "plan_id": 1, "page": page, "page_size": 1,
            }).structuredContent
            assert mcp_items["page"] == {
                "page": page,
                "page_size": 1,
                "total": len(rest_items),
                "has_more": page < len(rest_items),
            }
            assert len(mcp_items["data"]) == 1
            mcp_item = mcp_items["data"][0]
            assert set(mcp_item) == plan_item_fields
            for field in plan_item_fields:
                assert mcp_item[field] == rest_item[field], (page, field)

        request_id = "00000000-0000-4000-8000-000000000001"
        rest_request = rest_client.get(
            f"/purchase-plans/creation-requests/{request_id}", headers=rest_headers,
        )
        assert rest_request.status_code == 200
        mcp_request = call(rt, "erp_get_purchase_plan_creation_request", {
            "request_id": request_id,
        }).structuredContent["data"]
        rest_request_data = rest_request.json()["data"]
        assert set(mcp_request) == {"status", "plan_id"}
        assert mcp_request == {
            "status": rest_request_data["status"],
            "plan_id": rest_request_data["result"]["id"],
        }
        rest_pending = rest_client.get(
            "/purchase-plans/creation-requests/00000000-0000-4000-8000-000000000002",
            headers=rest_headers,
        )
        assert rest_pending.status_code == 200
        mcp_pending = call(rt, "erp_get_purchase_plan_creation_request", {
            "request_id": "00000000-0000-4000-8000-000000000002",
        }).structuredContent["data"]
        assert mcp_pending == {
            "status": rest_pending.json()["data"]["status"], "plan_id": None,
        }
        rest_hidden_request = rest_client.get(
            "/purchase-plans/creation-requests/00000000-0000-4000-8000-000000000003",
            headers=rest_headers,
        )
        assert rest_hidden_request.status_code == 200
        mcp_hidden_request = call(rt, "erp_get_purchase_plan_creation_request", {
            "request_id": "00000000-0000-4000-8000-000000000003",
        }).structuredContent["data"]
        assert mcp_hidden_request == {
            "status": rest_hidden_request.json()["data"]["status"], "plan_id": None,
        }

    detail = call(rt, "erp_get_purchase_plan", {"plan_id": 1})
    assert detail.structuredContent["data"]["plan_no"] == "MCP-PLAN-DETAIL"
    assert detail.structuredContent["data"]["total_amount"] == "25.50"
    assert "items" not in detail.structuredContent["data"]
    assert not {"remark", "created_by", "auditor_id", "audit_time"} & set(
        detail.structuredContent["data"]
    )
    assert call(rt, "erp_get_purchase_plan", {"plan_id": 2}).isError
    assert call(rt, "erp_get_purchase_plan", {"plan_id": 0}).structuredContent["code"] == "INVALID_ARGUMENT"

    creation = call(rt, "erp_get_purchase_plan_creation_request", {
        "request_id": "00000000-0000-4000-8000-000000000001",
    })
    assert creation.structuredContent["data"] == {"status": "SUCCEEDED", "plan_id": 1}
    assert call(rt, "erp_get_purchase_plan_creation_request", {
        "request_id": "00000000-0000-4000-8000-000000000002",
    }).structuredContent["data"] == {"status": "PENDING", "plan_id": None}
    assert call(rt, "erp_get_purchase_plan_creation_request", {
        "request_id": "00000000-0000-4000-8000-000000000003",
    }).structuredContent["data"]["status"] == "NOT_FOUND"
    assert call(rt, "erp_get_purchase_plan_creation_request", {
        "request_id": "not-a-uuid-0000000000000000000000000000",
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_purchase_plan_creation_request", {
        "request_id": "x" * 36,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_get_purchase_plan_creation_request", {
        "request_id": "00000000-0000-4000-8000-000000000004",
    }).structuredContent["code"] == "FORBIDDEN"

    first = call(rt, "erp_list_purchase_plan_items", {"plan_id": 1, "page_size": 1})
    assert first.structuredContent["page"] == {
        "page": 1, "page_size": 1, "total": 2, "has_more": True,
    }
    assert first.structuredContent["data"][0]["product_name"] == "计划商品一"
    assert first.structuredContent["data"][0]["remaining_qty"] == "1.000"
    assert first.structuredContent["data"][0]["purchase_price"] == "10.00"
    assert not {"description", "image", "remark", "cost_price"} & set(
        first.structuredContent["data"][0]
    )
    second = call(rt, "erp_list_purchase_plan_items", {
        "plan_id": 1, "page": 2, "page_size": 1,
    })
    assert second.structuredContent["data"][0]["product_code"] == "PLAN-P2"
    assert second.structuredContent["page"]["has_more"] is False
    assert call(rt, "erp_list_purchase_plan_items", {
        "plan_id": 2,
    }).isError
    assert call(rt, "erp_list_purchase_plan_items", {
        "plan_id": 999,
    }).isError
    assert call(rt, "erp_list_purchase_plan_items", {
        "plan_id": 1, "page_size": 101,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    with factory() as db:
        after = (
            db.scalar(select(func.count(PurchasePlan.id))),
            db.scalar(select(func.count(PurchasePlanItem.id))),
            db.scalar(select(func.count(PurchasePlanCreationRequest.id))),
        )
    assert after == before


def test_purchase_plan_mcp_scope_matrix_matches_rest_and_approval_exception(factory):
    from decimal import Decimal
    from sqlalchemy import func
    from app.api.purchase_plan import router as purchase_plan_router
    from app.api.purchase_order import router as purchase_order_router
    from app.models.approval import ApprovalRecord
    from app.models.org import Department
    from app.models.party import Supplier
    from app.models.purchase import PurchaseOrder, PurchasePlan, PurchasePlanItem
    from app.models.rbac import DataScope
    from app.core.data_scope import clear_scope_policy
    from sqlalchemy import delete

    tools = [
        ACCESS_TOOL, "erp_search_purchase_plans", "erp_get_purchase_plan",
        "erp_list_purchase_plan_items", "erp_search_purchase_orders",
        "erp_get_purchase_order",
    ]
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Department(id=1, company_id=1, name="计划权限一部"),
            Department(id=2, company_id=1, name="计划权限二部"),
            Staff(id=3, company_id=1, department_id=2, name="跨部门制单人"),
            Staff(id=4, company_id=1, department_id=1, name="同部门制单人"),
            Supplier(id=1, code="SCOPE-SUP", name="范围测试供应商"),
            Product(id=1, code="SCOPE-P", name="范围测试商品"),
            PurchasePlan(id=1, plan_no="SCOPE-SELF", supplier_id=1, created_by=1,
                         total_qty=Decimal("1"), total_amount=Decimal("1")),
            PurchasePlan(id=2, plan_no="SCOPE-DEPARTMENT", supplier_id=1, created_by=4,
                         total_qty=Decimal("2"), total_amount=Decimal("2")),
            PurchasePlan(id=3, plan_no="SCOPE-COMPANY", supplier_id=1, created_by=3,
                         total_qty=Decimal("3"), total_amount=Decimal("3")),
            PurchaseOrder(id=11, order_no="SCOPE-ORDER-SELF", supplier_id=1,
                          purchaser_id=1, created_by=1),
            PurchaseOrder(id=12, order_no="SCOPE-ORDER-DEPARTMENT", supplier_id=1,
                          purchaser_id=4, created_by=4),
            PurchaseOrder(id=13, order_no="SCOPE-ORDER-COMPANY", supplier_id=1,
                          purchaser_id=3, created_by=3),
            PurchasePlanItem(id=1, plan_id=2, product_id=1, planned_qty=Decimal("2"),
                             purchased_qty=Decimal("0")),
            ApprovalRecord(id=1, doc_type="purchase_plan", doc_id=3,
                           approver_id=1, action="APPROVE", status="APPROVED"),
            DataScope(id=1, staff_id=1, scope_type="SELF", department_id=1),
        ])
        db.get(Staff, 1).department_id = 1
        db.commit()

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(purchase_plan_router)
    rest_app.include_router(purchase_order_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    headers = {"Authorization": "Bearer " + create_access_token(1, "范围测试", [])}
    expected_by_scope = {
        "SELF": {1},
        "DEPARTMENT": {1, 2},
        "COMPANY": {1, 2, 3},
    }
    expected_order_ids_by_scope = {
        "SELF": {11},
        "DEPARTMENT": {11, 12},
        "COMPANY": {11, 12, 13},
    }
    with TestClient(rest_app) as rest_client:
        for scope, expected_list_ids in expected_by_scope.items():
            with factory() as db:
                db.get(DataScope, 1).scope_type = scope
                db.commit()

            mcp_list = call(rt, "erp_search_purchase_plans", {"page_size": 20})
            rest_list = rest_client.get(
                "/purchase-plans", params={"page": 1, "page_size": 20},
                headers=headers,
            )
            assert rest_list.status_code == 200
            rest_data = rest_list.json()["data"]
            assert not mcp_list.isError, (scope, mcp_list.structuredContent)
            mcp_ids = {row["id"] for row in mcp_list.structuredContent["data"]}
            rest_ids = {row["id"] for row in rest_data["items"]}
            assert mcp_ids == rest_ids == expected_list_ids, scope
            assert mcp_list.structuredContent["page"]["total"] == rest_data["total"] == len(expected_list_ids)

            # 订单列表与详情同样遵循本人/部门/公司记录级范围。
            mcp_orders = call(rt, "erp_search_purchase_orders", {"page_size": 20})
            rest_orders = rest_client.get(
                "/purchase-orders", params={"page": 1, "page_size": 20},
                headers=headers,
            )
            assert rest_orders.status_code == 200
            rest_order_data = rest_orders.json()["data"]
            assert not mcp_orders.isError, (scope, mcp_orders.structuredContent)
            mcp_order_ids = {row["id"] for row in mcp_orders.structuredContent["data"]}
            rest_order_ids = {row["id"] for row in rest_order_data["items"]}
            expected_order_ids = expected_order_ids_by_scope[scope]
            assert mcp_order_ids == rest_order_ids == expected_order_ids, scope
            assert mcp_orders.structuredContent["page"]["total"] == rest_order_data["total"] == len(expected_order_ids)

            for order_id in (11, 12, 13):
                mcp_order = call(rt, "erp_get_purchase_order", {"order_id": order_id})
                rest_order = rest_client.get(f"/purchase-orders/{order_id}", headers=headers)
                if order_id in expected_order_ids:
                    assert not mcp_order.isError, (scope, order_id)
                    assert rest_order.status_code == 200, (scope, order_id)
                else:
                    assert mcp_order.structuredContent["code"] == "FORBIDDEN", (scope, order_id)
                    assert rest_order.status_code == 404, (scope, order_id)

            # 审批参与只允许详情例外，不扩展列表与关联明细范围。
            detail_ids = expected_list_ids | {3}
            for plan_id in (1, 2, 3):
                mcp_detail = call(rt, "erp_get_purchase_plan", {"plan_id": plan_id})
                rest_detail = rest_client.get(f"/purchase-plans/{plan_id}", headers=headers)
                if plan_id in detail_ids:
                    assert not mcp_detail.isError, (scope, plan_id)
                    assert rest_detail.status_code == 200, (scope, plan_id)
                else:
                    assert mcp_detail.structuredContent["code"] == "FORBIDDEN", (scope, plan_id)
                    assert rest_detail.status_code == 404, (scope, plan_id)

            child = call(rt, "erp_list_purchase_plan_items", {
                "plan_id": 2, "page_size": 10,
            })
            if scope == "SELF":
                assert child.structuredContent["code"] == "FORBIDDEN"
            else:
                assert [row["id"] for row in child.structuredContent["data"]] == [1]

    with factory() as db:
        assert db.scalar(select(func.count(PurchasePlan.id))) == 3
        assert db.scalar(select(func.count(PurchaseOrder.id))) == 3
        assert db.scalar(select(func.count(PurchasePlanItem.id))) == 1


def test_purchase_needs_preview_pages_flat_sku_rows_and_keeps_company_scope(factory):
    from decimal import Decimal
    from sqlalchemy import delete, func
    from app.models.party import Supplier, SupplierPrice
    from app.models.product import Product, ProductSku
    from app.models.purchase import PurchasePlan
    from app.models.rbac import StaffRole
    from app.api.purchase_plan import router as purchase_plan_router
    from app.core.data_scope import clear_scope_policy

    tools = [ACCESS_TOOL, "erp_preview_purchase_needs"]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    blocked = call(runtime(factory, token), "erp_preview_purchase_needs", {
        "supplier_id": 1,
    })
    assert blocked.structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Supplier(id=1, code="NEED-SUP", name="需求供应商"),
            Product(
                id=1, name="多规格商品", code="NEED-PROD", default_supplier_id=1,
                cost_price=Decimal("9.00"), image="/private/product.png",
            ),
            ProductSku(
                id=1, product_id=1, spec_name="规格一", spec_code="NEED-SKU-1",
                cost_price=Decimal("8.00"), image="/private/sku.png", is_active=True,
            ),
            ProductSku(
                id=2, product_id=1, spec_name="规格二", spec_code="NEED-SKU-2",
                cost_price=Decimal("7.00"), is_active=True,
            ),
            ProductSku(
                id=3, product_id=1, spec_name="规格三", spec_code="NEED-SKU-3",
                cost_price=Decimal("6.00"), is_active=True,
            ),
            SupplierPrice(
                id=1, supplier_id=1, product_id=1, sku_id=1,
                price=Decimal("10.00"), status="ACTIVE",
            ),
            SupplierPrice(
                id=2, supplier_id=1, product_id=1, sku_id=2,
                price=Decimal("15.00"), status="ACTIVE",
            ),
            Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            Staff(id=3, company_id=1, name="无采购权限员工"),
        ])
        db.flush()
        db.add(StaffRole(staff_id=3, role_id=3))
        db.commit()
        before = (
            db.scalar(select(func.count(PurchasePlan.id))),
            db.scalar(select(func.count(Product.id))),
            db.scalar(select(func.count(ProductSku.id))),
            db.scalar(select(func.count(SupplierPrice.id))),
        )

    rt = runtime(factory, token)
    first = call(rt, "erp_preview_purchase_needs", {
        "supplier_id": 1, "keyword": "多规格", "page_size": 2,
    })
    assert first.structuredContent["page"] == {
        "page": 1, "page_size": 2, "total": 3, "has_more": True,
    }
    assert first.structuredContent["effective_filters"]["sales_window_days"] == 20
    assert first.structuredContent["effective_filters"]["candidate_products_total"] == 1
    assert [row["sku_id"] for row in first.structuredContent["data"]] == [1, 2]
    assert first.structuredContent["data"][0]["purchase_price"] == "10.00"
    assert first.structuredContent["data"][1]["purchase_price"] == "15.00"
    assert not {"image", "cost_price", "description"} & set(
        first.structuredContent["data"][0]
    )
    last = call(rt, "erp_preview_purchase_needs", {
        "supplier_id": 1, "keyword": "多规格", "page": 2, "page_size": 2,
    })
    assert [row["sku_id"] for row in last.structuredContent["data"]] == [3]
    assert last.structuredContent["data"][0]["purchase_price"] == "6.00"
    assert last.structuredContent["page"]["has_more"] is False

    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(purchase_plan_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    rest_headers = {"Authorization": "Bearer " + create_access_token(1, "甲", [])}
    with TestClient(rest_app) as rest_client:
        rest_response = rest_client.get(
            "/purchase-plans/needs",
            params={"supplier_id": 1, "keyword": "多规格", "page": 1, "page_size": 2},
            headers=rest_headers,
        )
    assert rest_response.status_code == 200
    rest_result = rest_response.json()["data"]
    rest_rows = rest_result["items"]
    mcp_rows = first.structuredContent["data"] + last.structuredContent["data"]
    assert rest_result["total"] == first.structuredContent["effective_filters"]["candidate_products_total"] == 1
    assert rest_result["sales_window_days"] == first.structuredContent["effective_filters"]["sales_window_days"] == 20
    assert len(rest_rows) == len(mcp_rows) == first.structuredContent["page"]["total"] == 3
    rest_by_sku = {row["sku_id"]: row for row in rest_rows}
    assert set(rest_by_sku) == {1, 2, 3}
    for mcp_row in mcp_rows:
        rest_row = rest_by_sku[mcp_row["sku_id"]]
        for field, value in mcp_row.items():
            rest_value = rest_row[field]
            if value is None:
                assert rest_value is None, field
            elif field in {
                "purchase_price", "estimated_amount", "sales_20d", "current_stock",
                "in_transit_stock", "in_progress_qty", "suggested_qty",
            }:
                assert Decimal(str(value)) == Decimal(str(rest_value)), field
            else:
                assert value == rest_value, field
    # REST pages products and may expand all of a product's SKUs; MCP bounds
    # both response length and SQL result window by the SKU/specification row.
    assert first.structuredContent["page"]["page_size"] == 2
    assert len(first.structuredContent["data"]) == 2
    assert len(rest_rows) == 3
    assert not {"image", "cost_price", "art_no", "brand_id", "brand_name"} & set(
        first.structuredContent["data"][0]
    )
    assert call(rt, "erp_preview_purchase_needs", {
        "supplier_id": 1, "page_size": 101,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_preview_purchase_needs", {
        "supplier_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_preview_purchase_needs", {
        "supplier_id": 999,
    }).isError

    warehouse_token = issue(
        factory, staff_id=3, tool_scopes=tools,
        permission_scopes=["module:stock"],
    )["token"]
    assert call(
        runtime(factory, warehouse_token), "erp_preview_purchase_needs",
        {"supplier_id": 1},
    ).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after = (
            db.scalar(select(func.count(PurchasePlan.id))),
            db.scalar(select(func.count(Product.id))),
            db.scalar(select(func.count(ProductSku.id))),
            db.scalar(select(func.count(SupplierPrice.id))),
        )
    assert after == before


def test_product_reference_tools_are_bounded_fixed_and_permission_scoped(factory):
    from decimal import Decimal
    from sqlalchemy import func
    from app.models.basic import (
        Brand, Category, SpecType, SpecValue, Unit, UnitGroup, UnitGroupItem,
    )
    from app.models.rbac import StaffRole

    tools = [
        ACCESS_TOOL,
        "erp_product_list_categories",
        "erp_product_list_brands",
        "erp_product_list_units",
        "erp_product_list_unit_groups",
        "erp_product_list_unit_group_items",
        "erp_product_list_spec_types",
        "erp_product_list_spec_values",
    ]
    with factory() as db:
        db.add_all([
            Category(id=1, company_id=1, name="根分类", sort_order=1),
            Category(id=2, company_id=1, name="子分类", parent_id=1, sort_order=2),
            Brand(id=1, company_id=1, name="品牌一", image="/private/brand.png"),
            Brand(id=2, company_id=1, name="品牌二"),
            Unit(id=1, company_id=1, name="件", code="PCS"),
            Unit(id=2, company_id=1, name="箱", code="BOX"),
            UnitGroup(id=1, company_id=1, name="件箱组", main_unit_id=1),
            SpecType(id=1, company_id=1, name="颜色", category_id=1),
            SpecValue(id=1, company_id=1, spec_type_id=1, name="红色", code="RED", sort_order=1),
            SpecValue(id=2, company_id=1, spec_type_id=1, name="蓝色", code="BLUE", sort_order=2),
            Category(id=3, company_id=2, name="他公司分类"),
            Brand(id=3, company_id=2, name="他公司品牌"),
            Unit(id=3, company_id=2, name="他公司单位", code="OTHER"),
            UnitGroup(id=2, company_id=2, name="他公司单位组", main_unit_id=3),
            SpecType(id=2, company_id=2, name="他公司规格", category_id=3),
            SpecValue(id=3, company_id=2, spec_type_id=2, name="他公司规格值", code="OTHER"),
        ])
        db.flush()
        db.add_all([
            UnitGroupItem(
                id=1, company_id=1, unit_group_id=1, unit_id=1, conversion_rate=Decimal("1"),
            ),
            UnitGroupItem(
                id=2, company_id=1, unit_group_id=1, unit_id=2, conversion_rate=Decimal("12"),
            ),
            UnitGroupItem(
                id=3, company_id=2, unit_group_id=2, unit_id=3, conversion_rate=Decimal("2"),
            ),
            Role(id=3, company_id=1, code="WAREHOUSE", name="仓库"),
            Staff(id=3, company_id=1, name="无商品权限员工"),
        ])
        db.flush()
        db.add(StaffRole(staff_id=3, role_id=3))
        db.commit()
        before = (
            db.scalar(select(func.count(Category.id))),
            db.scalar(select(func.count(Brand.id))),
            db.scalar(select(func.count(Unit.id))),
            db.scalar(select(func.count(UnitGroup.id))),
            db.scalar(select(func.count(UnitGroupItem.id))),
            db.scalar(select(func.count(SpecType.id))),
            db.scalar(select(func.count(SpecValue.id))),
        )

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:product"],
    )["token"]
    rt = runtime(factory, token)
    categories = call(rt, "erp_product_list_categories", {"page_size": 1})
    assert categories.structuredContent["page"]["total"] == 2
    assert categories.structuredContent["page"]["has_more"] is True
    assert categories.structuredContent["data"][0]["name"] == "根分类"

    brands = call(rt, "erp_product_list_brands", {"page_size": 1})
    assert brands.structuredContent["page"]["total"] == 2
    assert brands.structuredContent["page"]["has_more"] is True
    assert brands.structuredContent["data"][0] == {"id": 1, "name": "品牌一"}
    units = call(rt, "erp_product_list_units", {"page_size": 1})
    assert units.structuredContent["data"][0]["code"] == "PCS"
    assert units.structuredContent["page"]["has_more"] is True
    assert units.structuredContent["page"]["total"] == 2

    groups = call(rt, "erp_product_list_unit_groups", {})
    assert groups.structuredContent["page"]["total"] == 1
    assert groups.structuredContent["data"] == [{
        "id": 1, "name": "件箱组", "main_unit_id": 1,
        "main_unit_name": "件", "item_count": 2,
    }]
    assert "items" not in groups.structuredContent["data"][0]
    group_items = call(rt, "erp_product_list_unit_group_items", {
        "unit_group_id": 1, "page_size": 1,
    })
    assert group_items.structuredContent["data"][0]["unit_name"] == "件"
    assert group_items.structuredContent["page"]["has_more"] is True
    assert call(rt, "erp_product_list_unit_group_items", {
        "unit_group_id": 2,
    }).structuredContent["page"]["total"] == 0
    spec_types = call(rt, "erp_product_list_spec_types", {})
    assert spec_types.structuredContent["page"]["total"] == 1
    assert spec_types.structuredContent["data"][0]["category_name"] == "根分类"
    values = call(rt, "erp_product_list_spec_values", {
        "spec_type_id": 1, "page_size": 1,
    })
    assert values.structuredContent["data"][0]["code"] == "RED"
    assert values.structuredContent["page"]["has_more"] is True
    assert call(rt, "erp_product_list_spec_values", {
        "spec_type_id": 999,
    }).structuredContent["data"] == []
    assert call(rt, "erp_product_list_spec_values", {
        "spec_type_id": 2,
    }).structuredContent["page"]["total"] == 0
    assert call(rt, "erp_product_list_spec_values", {
        "spec_type_id": 1, "page_size": 101,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    assert call(rt, "erp_product_list_unit_group_items", {
        "unit_group_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    warehouse_token = issue(
        factory, staff_id=3, tool_scopes=tools,
        permission_scopes=["module:stock"],
    )["token"]
    assert call(
        runtime(factory, warehouse_token), "erp_product_list_categories",
    ).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after = (
            db.scalar(select(func.count(Category.id))),
            db.scalar(select(func.count(Brand.id))),
            db.scalar(select(func.count(Unit.id))),
            db.scalar(select(func.count(UnitGroup.id))),
            db.scalar(select(func.count(UnitGroupItem.id))),
            db.scalar(select(func.count(SpecType.id))),
            db.scalar(select(func.count(SpecValue.id))),
        )
    assert after == before


def test_product_settings_tools_match_rest_for_same_identity_and_paged_outputs(factory):
    from decimal import Decimal
    from sqlalchemy import delete
    from app.api.product import router as product_router
    from app.core.data_scope import clear_scope_policy
    from app.models.basic import (
        Brand, Category, SpecType, SpecValue, Unit, UnitGroup, UnitGroupItem,
    )

    tools = [
        ACCESS_TOOL,
        "erp_product_list_categories", "erp_product_list_brands",
        "erp_product_list_units", "erp_product_list_unit_groups",
        "erp_product_list_unit_group_items", "erp_product_list_spec_types",
        "erp_product_list_spec_values",
    ]
    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Category(id=1, company_id=1, name="MCP-PROD-根类", sort_order=1),
            Category(id=2, company_id=1, name="MCP-PROD-子类", parent_id=1, sort_order=2),
            Category(id=3, company_id=1, name="MCP-PROD-其他类", sort_order=3),
            Brand(id=1, company_id=1, name="MCP-PROD-品牌一", image="/private/brand-1.png"),
            Brand(id=2, company_id=1, name="MCP-PROD-品牌二", image="/private/brand-2.png"),
            Unit(id=1, company_id=1, name="MCP-PROD-件", code="PCS"),
            Unit(id=2, company_id=1, name="MCP-PROD-箱", code="BOX"),
            Unit(id=3, company_id=1, name="MCP-PROD-个", code=None),
            UnitGroup(id=1, company_id=1, name="MCP-PROD-件箱组", main_unit_id=1),
            UnitGroup(id=2, company_id=1, name="MCP-PROD-件个组", main_unit_id=1),
            SpecType(id=1, company_id=1, name="MCP-PROD-颜色", category_id=1),
            SpecType(id=2, company_id=1, name="MCP-PROD-无分类规格", category_id=None),
            SpecValue(id=1, company_id=1, spec_type_id=1, name="MCP-PROD-红", code="RED", sort_order=1),
            SpecValue(id=2, company_id=1, spec_type_id=1, name="MCP-PROD-蓝", code="BLUE", sort_order=2),
            SpecValue(id=3, company_id=1, spec_type_id=2, name="MCP-PROD-空编码值", code=None, sort_order=1),
        ])
        db.flush()
        db.add_all([
            UnitGroupItem(
                id=1, company_id=1, unit_group_id=1, unit_id=1,
                conversion_rate=Decimal("1.000000"),
            ),
            UnitGroupItem(
                id=2, company_id=1, unit_group_id=1, unit_id=2,
                conversion_rate=Decimal("12.000000"),
            ),
            UnitGroupItem(
                id=3, company_id=1, unit_group_id=2, unit_id=3,
                conversion_rate=Decimal("24.000000"),
            ),
        ])
        db.commit()

    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:product"],
    )["token"]
    rt = runtime(factory, token, limits=MemoryLimits(rpm=100))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(product_router)

    def sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app.dependency_overrides[get_db] = sessions
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + create_access_token(1, "test", [])}

    def rest_data(path, params=None):
        response = client.get(path, params=params or {}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def mcp_pages(tool_name, filters=None):
        filters = filters or {}
        first = call(rt, tool_name, {**filters, "page": 1, "page_size": 1})
        assert not first.isError, first.structuredContent
        rows = list(first.structuredContent["data"])
        page = first.structuredContent["page"]
        while page["has_more"]:
            next_page = call(rt, tool_name, {
                **filters, "page": page["page"] + 1, "page_size": 1,
            })
            assert not next_page.isError, next_page.structuredContent
            rows.extend(next_page.structuredContent["data"])
            page = next_page.structuredContent["page"]
        return rows

    def assert_fields_match(rest_row, mcp_row, fields):
        for field in fields:
            left, right = rest_row[field], mcp_row[field]
            if left is None or right is None:
                assert left is right, field
            elif all(
                isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
                for value in (left, right)
            ):
                assert Decimal(str(left)) == Decimal(str(right)), field
            else:
                assert left == right, field

    list_cases = (
        ("erp_product_list_categories", "/product-settings/categories",
         ("id", "name", "parent_id", "sort_order")),
        ("erp_product_list_brands", "/product-settings/brands", ("id", "name")),
        ("erp_product_list_units", "/product-settings/units", ("id", "name", "code")),
        ("erp_product_list_spec_types", "/product-settings/spec-types",
         ("id", "name", "category_id")),
    )
    for tool_name, path, fields in list_cases:
        rest_rows = rest_data(path)
        mcp_rows = mcp_pages(tool_name)
        assert {row["id"] for row in mcp_rows} == {row["id"] for row in rest_rows}
        rest_by_id = {row["id"]: row for row in rest_rows}
        for row in mcp_rows:
            assert_fields_match(rest_by_id[row["id"]], row, fields)

    brands = mcp_pages("erp_product_list_brands")
    assert all("image" not in row for row in brands)
    units = mcp_pages("erp_product_list_units")
    assert units[-1]["code"] is None

    rest_groups = rest_data("/product-settings/unit-groups")
    mcp_groups = mcp_pages("erp_product_list_unit_groups")
    assert {row["id"] for row in mcp_groups} == {row["id"] for row in rest_groups}
    rest_groups_by_id = {row["id"]: row for row in rest_groups}
    for row in mcp_groups:
        rest_group = rest_groups_by_id[row["id"]]
        assert_fields_match(rest_group, row, ("id", "name", "main_unit_id"))
        assert row["item_count"] == len(rest_group["items"])
        assert row["main_unit_name"] == "MCP-PROD-件"
        rest_items = {item["unit_id"]: item for item in rest_group["items"]}
        mcp_items = mcp_pages(
            "erp_product_list_unit_group_items", {"unit_group_id": row["id"]},
        )
        assert {item["unit_id"] for item in mcp_items} == set(rest_items)
        for item in mcp_items:
            assert_fields_match(
                rest_items[item["unit_id"]], item,
                ("unit_id", "conversion_rate"),
            )

    spec_types_by_id = {row["id"]: row for row in mcp_pages("erp_product_list_spec_types")}
    assert spec_types_by_id[1]["category_name"] == "MCP-PROD-根类"
    assert spec_types_by_id[2]["category_id"] is None
    assert spec_types_by_id[2]["category_name"] is None
    rest_values = rest_data("/product-settings/spec-types/1/values")
    mcp_values = mcp_pages("erp_product_list_spec_values", {"spec_type_id": 1})
    assert {row["id"] for row in mcp_values} == {row["id"] for row in rest_values}
    rest_values_by_id = {row["id"]: row for row in rest_values}
    for row in mcp_values:
        assert_fields_match(
            rest_values_by_id[row["id"]], row,
            ("id", "spec_type_id", "name", "code", "sort_order"),
        )
    rest_null_code = rest_data("/product-settings/spec-types/2/values")
    mcp_null_code = call(rt, "erp_product_list_spec_values", {
        "spec_type_id": 2,
    }).structuredContent["data"]
    assert mcp_null_code[0]["code"] is None
    assert mcp_null_code[0]["id"] == rest_null_code[0]["id"]


def test_party_account_and_supplier_price_tools_mask_sensitive_fields(factory):
    from decimal import Decimal
    from sqlalchemy import delete, event, func
    from app.api.party import router as party_router
    from app.core.data_scope import clear_scope_policy
    from app.models.party import Account, Customer, Supplier, SupplierPrice
    from app.models.product import Product
    from app.models.rbac import StaffRole

    tools = [
        ACCESS_TOOL,
        "erp_list_supplier_accounts",
        "erp_list_customer_accounts",
        "erp_list_supplier_prices",
    ]
    token = issue(
        factory, tool_scopes=tools, permission_scopes=["module:purchase"],
    )["token"]
    rt = runtime(factory, token)
    for tool_name, arguments in (
        ("erp_list_supplier_accounts", {"supplier_id": 1}),
        ("erp_list_customer_accounts", {"customer_id": 1}),
        ("erp_list_supplier_prices", {"supplier_id": 1}),
    ):
        multi_company = call(rt, tool_name, arguments)
        assert multi_company.structuredContent["code"] == "FORBIDDEN", tool_name

    with factory() as db:
        db.execute(delete(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(db.get(Staff, 2))
        db.delete(db.get(Role, 2))
        db.delete(db.get(Company, 2))
        db.add_all([
            Supplier(id=1, code="BANK-SUP", name="银行信息供应商"),
            Customer(id=1, code="BANK-CUS", name="银行信息客户"),
            Product(id=1, name="供应商品", code="BANK-PROD"),
            Product(id=2, name="供应商品二", code="BANK-PROD-2"),
            Account(
                id=1, supplier_id=1, bank="测试银行", branch="测试支行",
                bank_no="6222-1234-5678-9999", account_no="1234567890123456",
                address="绝不外发开户地址", balance=Decimal("123.45"),
                kingdee_account_code="KINGDEE-SECRET", kingdee_sync_message="同步错误详情",
                is_default=True,
            ),
            Account(
                id=2, supplier_id=1, bank="备用银行", account_no="8888",
                balance=Decimal("9.00"),
            ),
            Account(
                id=3, customer_id=1, bank="客户银行", branch="客户支行",
                bank_no="6217-1234-5678-8888", account_no="9988776655443322",
                balance=Decimal("50.00"),
            ),
            Account(
                id=4, customer_id=1, bank=None, branch=None, bank_no=None, account_no=None,
                balance=Decimal("20.00"), is_default=True,
            ),
            SupplierPrice(
                id=1, supplier_id=1, product_id=1, price=Decimal("7.25"),
                status="ACTIVE",
            ),
            SupplierPrice(
                id=2, supplier_id=1, product_id=1, price=Decimal("8.00"),
                status="INACTIVE",
            ),
            SupplierPrice(
                id=3, supplier_id=1, product_id=2, price=Decimal("12.50"),
                status="ACTIVE",
            ),
            Role(id=3, company_id=1, code="AFTER_SALE", name="售后"),
            Staff(id=3, company_id=1, name="无采购权限员工"),
        ])
        db.flush()
        db.add(StaffRole(staff_id=3, role_id=3))
        db.commit()
        before = (
            db.scalar(select(func.count(Account.id))),
            db.scalar(select(func.count(SupplierPrice.id))),
        )

    rt = runtime(factory, token)
    rest_app = FastAPI()
    register_exception_handlers(rest_app)
    rest_app.include_router(party_router)

    def rest_sessions():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    rest_app.dependency_overrides[get_db] = rest_sessions
    purchaser_headers = {
        "Authorization": "Bearer " + create_access_token(1, "甲", []),
    }
    with TestClient(rest_app) as rest_client:
        rest_supplier_accounts_response = rest_client.get(
            "/suppliers/1/accounts", headers=purchaser_headers,
        )
        rest_customer_accounts_response = rest_client.get(
            "/customers/1/accounts", headers=purchaser_headers,
        )
        rest_prices_first_response = rest_client.get(
            "/suppliers/1/prices", params={"product_id": 1, "page": 1, "page_size": 1},
            headers=purchaser_headers,
        )
        rest_prices_second_response = rest_client.get(
            "/suppliers/1/prices", params={"product_id": 1, "page": 2, "page_size": 1},
            headers=purchaser_headers,
        )
        rest_prices_empty_response = rest_client.get(
            "/suppliers/1/prices", params={"product_id": 999, "page": 1, "page_size": 1},
            headers=purchaser_headers,
        )
    assert rest_supplier_accounts_response.status_code == 200
    assert rest_customer_accounts_response.status_code == 200
    assert rest_prices_first_response.status_code == 200
    assert rest_prices_second_response.status_code == 200
    assert rest_prices_empty_response.status_code == 200

    def call_bounded_page(tool_name, arguments, table_name, active_runtime=None):
        statements = []

        def capture_select(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = factory.kw["bind"]
        event.listen(engine, "before_cursor_execute", capture_select)
        try:
            result = call(active_runtime or rt, tool_name, arguments)
        finally:
            event.remove(engine, "before_cursor_execute", capture_select)
        assert not result.isError, result.structuredContent
        assert any(
            table_name in statement.lower() and "LIMIT" in statement.upper()
            for statement in statements
        ), f"{tool_name} 未观察到目标表 SQL LIMIT: {statements}"
        return result.structuredContent

    def expected_mask(value):
        if not value:
            return None
        normalized = "".join(character for character in value if character.isalnum())
        return f"****{normalized[-4:]}" if len(normalized) > 4 else "已脱敏"

    safe_account_fields = {
        "id", "supplier_id", "customer_id", "bank", "branch", "bank_no",
        "account_no", "is_default",
    }
    rest_supplier_accounts = rest_supplier_accounts_response.json()["data"]
    assert len(rest_supplier_accounts) == 2
    supplier_account_pages = [
        call_bounded_page("erp_list_supplier_accounts", {
            "supplier_id": 1, "page": page, "page_size": 1,
        }, "account")
        for page in (1, 2)
    ]
    for page_number, page_payload in enumerate(supplier_account_pages, start=1):
        assert page_payload["page"]["page"] == page_number
        assert page_payload["page"]["page_size"] == 1
        assert page_payload["page"]["total"] == len(rest_supplier_accounts)
        assert page_payload["page"]["has_more"] is (page_number == 1)
        assert len(page_payload["data"]) == 1
        row = page_payload["data"][0]
        rest_row = rest_supplier_accounts[page_number - 1]
        assert set(row) == safe_account_fields
        for field in ("id", "supplier_id", "customer_id", "bank", "branch", "is_default"):
            assert row[field] == rest_row[field]
        assert row["bank_no"] == expected_mask(rest_row["bank_no"])
        assert row["account_no"] == expected_mask(rest_row["account_no"])
        assert row["id"] == rest_row["id"]

    rest_customer_accounts = rest_customer_accounts_response.json()["data"]
    assert len(rest_customer_accounts) == 2
    customer_account_pages = [
        call_bounded_page("erp_list_customer_accounts", {
            "customer_id": 1, "page": page, "page_size": 1,
        }, "account")
        for page in (1, 2)
    ]
    for page_number, page_payload in enumerate(customer_account_pages, start=1):
        assert page_payload["page"]["page"] == page_number
        assert page_payload["page"]["page_size"] == 1
        assert page_payload["page"]["total"] == len(rest_customer_accounts)
        assert page_payload["page"]["has_more"] is (page_number == 1)
        row = page_payload["data"][0]
        rest_row = rest_customer_accounts[page_number - 1]
        assert set(row) == safe_account_fields
        for field in ("id", "supplier_id", "customer_id", "bank", "branch", "is_default"):
            assert row[field] == rest_row[field]
        assert row["bank_no"] == expected_mask(rest_row["bank_no"])
        assert row["account_no"] == expected_mask(rest_row["account_no"])

    with factory() as db:
        db.add(Role(id=4, company_id=1, code="FINANCE", name="财务"))
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=4))
        db.commit()
    finance_token = issue(
        factory, tool_scopes=tools,
        permission_scopes=["module:purchase", "module:finance"],
    )["token"]
    finance_rt = runtime(factory, finance_token)
    finance_headers = {
        "Authorization": "Bearer " + create_access_token(1, "甲", []),
    }
    with TestClient(rest_app) as rest_client:
        rest_finance_accounts_response = rest_client.get(
            "/suppliers/1/accounts", headers=finance_headers,
        )
    rest_finance_accounts = rest_finance_accounts_response.json()["data"]
    assert len(rest_finance_accounts) == 2
    finance_account_pages = [
        call_bounded_page("erp_list_supplier_accounts", {
                "supplier_id": 1, "page": page, "page_size": 1,
            }, "account", active_runtime=finance_rt)
        for page in (1, 2)
    ]
    finance_safe_fields = safe_account_fields | {"balance"}
    for page_number, page_payload in enumerate(finance_account_pages, start=1):
        row = page_payload["data"][0]
        rest_row = rest_finance_accounts[page_number - 1]
        assert page_payload["page"]["total"] == len(rest_finance_accounts)
        assert set(row) == finance_safe_fields
        assert row["id"] == rest_row["id"]
        assert row["bank_no"] == expected_mask(rest_row["bank_no"])
        assert row["account_no"] == expected_mask(rest_row["account_no"])
        assert Decimal(row["balance"]) == Decimal(str(rest_row["balance"]))
    with TestClient(rest_app) as rest_client:
        rest_finance_customer_accounts_response = rest_client.get(
            "/customers/1/accounts", headers=finance_headers,
        )
    rest_finance_customer_accounts = rest_finance_customer_accounts_response.json()["data"]
    assert len(rest_finance_customer_accounts) == 2
    finance_customer_pages = [
        call_bounded_page("erp_list_customer_accounts", {
            "customer_id": 1, "page": page, "page_size": 1,
        }, "account", active_runtime=finance_rt)
        for page in (1, 2)
    ]
    for page_number, page_payload in enumerate(finance_customer_pages, start=1):
        row = page_payload["data"][0]
        rest_row = rest_finance_customer_accounts[page_number - 1]
        assert set(row) == finance_safe_fields
        assert row["id"] == rest_row["id"]
        assert row["bank_no"] == expected_mask(rest_row["bank_no"])
        assert row["account_no"] == expected_mask(rest_row["account_no"])
        assert Decimal(row["balance"]) == Decimal(str(rest_row["balance"]))
    assert call(rt, "erp_list_supplier_accounts", {
        "supplier_id": 999,
    }).isError
    assert call(rt, "erp_list_supplier_accounts", {
        "supplier_id": 1, "page_size": 101,
    }).structuredContent["code"] == "INVALID_ARGUMENT"
    rest_price_rows = [
        rest_prices_first_response.json()["data"]["items"][0],
        rest_prices_second_response.json()["data"]["items"][0],
    ]
    price_fields = {
        "id", "supplier_id", "product_id", "product_name", "product_code",
        "product_status", "sku_id", "sku_spec_code", "status", "price",
    }
    price_pages = [
        call_bounded_page("erp_list_supplier_prices", {
            "supplier_id": 1, "product_id": 1,
            "page": page, "page_size": 1,
        }, "supplier_price")
        for page in (1, 2)
    ]
    for page_number, page_payload in enumerate(price_pages, start=1):
        rest_page = (
            rest_prices_first_response.json()["data"]
            if page_number == 1 else rest_prices_second_response.json()["data"]
        )
        row = page_payload["data"][0]
        rest_row = rest_price_rows[page_number - 1]
        assert page_payload["page"]["page"] == page_number
        assert page_payload["page"]["page_size"] == 1
        assert page_payload["page"]["total"] == rest_page["total"] == 2
        assert page_payload["page"]["has_more"] is (page_number == 1)
        assert set(row) == price_fields
        for field in price_fields - {"price"}:
            assert row[field] == rest_row[field], field
        assert Decimal(row["price"]) == Decimal(str(rest_row["price"]))
    assert price_pages[0]["data"][0]["price"] == "8.00"

    empty_prices = call_bounded_page("erp_list_supplier_prices", {
        "supplier_id": 1, "product_id": 999, "page": 1, "page_size": 1,
    }, "supplier_price")
    rest_empty_prices = rest_prices_empty_response.json()["data"]
    assert rest_empty_prices["total"] == 0
    assert empty_prices["page"]["total"] == rest_empty_prices["total"]
    assert empty_prices["page"]["has_more"] is False
    assert empty_prices["data"] == []
    assert call(rt, "erp_list_supplier_prices", {
        "supplier_id": 1, "product_id": 0,
    }).structuredContent["code"] == "INVALID_ARGUMENT"

    after_sale_token = issue(
        factory, staff_id=3,
        tool_scopes=tools, permission_scopes=["module:order"],
    )["token"]
    assert call(
        runtime(factory, after_sale_token), "erp_list_supplier_prices",
        {"supplier_id": 1},
    ).structuredContent["code"] == "FORBIDDEN"

    with factory() as db:
        after = (
            db.scalar(select(func.count(Account.id))),
            db.scalar(select(func.count(SupplierPrice.id))),
        )
    assert after == before
