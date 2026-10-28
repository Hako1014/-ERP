import re
from pathlib import Path

from pydantic import ValidationError

from app.core.query_access import CAPABILITIES
from app.services.mcp_credentials import ACCESS_TOOL
from app.services.mcp_tool_registry import MCP_TOOL_SPECS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = PROJECT_ROOT / "docs" / "MCP查询能力清单.md"


def _expand_q_ids(value: str) -> set[int]:
    result: set[int] = set()
    for match in re.finditer(r"Q(\d{3})(?:-Q?(\d{3}))?", value):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        result.update(range(start, end + 1))
    return result


def _expand_q_ids_ordered(value: str) -> list[int]:
    result: list[int] = []
    for match in re.finditer(r"Q(\d{3})(?:-Q?(\d{3}))?", value):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        result.extend(range(start, end + 1))
    return result


def _json_schema_array_paths(schema: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if not isinstance(schema, dict):
        return paths
    if schema.get("type") == "array":
        path = prefix or "$"
        paths.add(path)
        paths.update(_json_schema_array_paths(schema.get("items", {}), f"{path}[]"))
    for field_name, child in schema.get("properties", {}).items():
        child_prefix = f"{prefix}.{field_name}" if prefix else field_name
        paths.update(_json_schema_array_paths(child, child_prefix))
    for branch in schema.get("anyOf", []):
        paths.update(_json_schema_array_paths(branch, prefix))
    return paths


def test_query_catalog_partitions_every_entry_and_maps_every_registered_tool():
    catalog = CATALOG_PATH.read_text(encoding="utf-8")
    assert "授权后统一按凭据当前公司过滤" in catalog
    q_sections = list(re.finditer(
        r"(?ms)^### Q(\d{3}) · ([^\r\n]+)\r?\n.*?(?=^### Q\d{3} · |^## |\Z)",
        catalog,
    ))
    q_ids = {int(section.group(1)) for section in q_sections}
    excluded_q_ids = {
        int(section.group(1))
        for section in q_sections
        if re.search(r"(?m)^- 状态：EXCLUDED(?:\b|（)", section.group(0))
    }
    statuses = [
        re.search(
            r"(?m)^- 状态：(EXCLUDED|IMPLEMENTED_ISOLATED_TEST_ONLY)(?:\b|（)",
            section.group(0),
        )
        for section in q_sections
    ]
    included_q_ids = q_ids - excluded_q_ids

    delta_start = catalog.index("### TASK-099 首批固定工具实现增量")
    delta_end = catalog.index("### TASK-098 增量与阻塞台账", delta_start)
    delta = catalog[delta_start:delta_end]
    table_q_ids: set[int] = set()
    table_tool_names: set[str] = set()
    table_tools_by_q: dict[int, set[str]] = {}
    table_role_context_by_q: dict[int, str] = {}
    multi_tool_rows = {
        "Q154-Q159": {
            154: {"erp_transfer_list_transfers"},
            155: {"erp_transfer_get_transfer", "erp_transfer_list_transfer_items"},
            156: {"erp_transfer_list_transfer_outs"},
            157: {"erp_transfer_get_transfer_out", "erp_transfer_list_transfer_out_items"},
            158: {"erp_transfer_list_transfer_ins"},
            159: {"erp_transfer_get_transfer_in", "erp_transfer_list_transfer_in_items"},
        },
        "Q160-Q161": {
            160: {"erp_voucher_list_vouchers"},
            161: {"erp_voucher_get_voucher", "erp_voucher_list_voucher_items"},
        },
        "Q169-Q174": {
            169: {"erp_wave_list_wave_strategies"},
            170: {"erp_wave_get_wave_strategy"},
            171: {"erp_wave_get_wave_strategy_pending_count"},
            172: {"erp_wave_list_waves"},
            173: {"erp_wave_get_wave"},
            174: {"erp_wave_list_wave_orders", "erp_wave_list_load_numbers"},
        },
    }
    for row in re.finditer(
        r"(?m)^\|\s*(Q[^|]+)\|\s*([^|]+)\|\s*([^|\r\n]+)\|", delta,
    ):
        label = row.group(1).strip()
        row_q_ids = _expand_q_ids_ordered(label)
        tools = re.findall(r"`(erp_[a-z0-9_]+)`", row.group(2))
        table_q_ids.update(_expand_q_ids(label))
        table_tool_names.update(tools)
        table_role_context_by_q.update(
            {q_id: row.group(0) for q_id in row_q_ids}
        )
        if len(row_q_ids) == len(tools):
            table_tools_by_q.update(
                {q_id: {tool} for q_id, tool in zip(row_q_ids, tools)}
            )
        elif len(row_q_ids) == 1:
            table_tools_by_q[row_q_ids[0]] = set(tools)
        else:
            assert label in multi_tool_rows, f"需显式拆分清单映射行：{label}"
            expected_by_q = multi_tool_rows[label]
            assert set().union(*expected_by_q.values()) == set(tools), label
            table_tools_by_q.update(expected_by_q)

    identity_section = next(section.group(0) for section in q_sections
                            if int(section.group(1)) == 23)
    mapped_included_q_ids = table_q_ids | {23}
    table_tools_by_q[23] = {ACCESS_TOOL}
    all_registered_tools = set(MCP_TOOL_SPECS) | {ACCESS_TOOL}
    capability_tools = {
        tool
        for capability in CAPABILITIES.values()
        for tool in (
            (capability.tool,)
            if isinstance(capability.tool, str)
            else (capability.tool or ())
        )
    }
    assert len(q_ids) == 174, "Q入口数量变化时需重新核对源路由盘点"
    assert len(included_q_ids) == 158
    assert len(excluded_q_ids) == 16
    assert all(statuses), "每个Q入口必须有明确的纳入/排除状态"
    assert sum(status.group(1) == "EXCLUDED" for status in statuses) == 16
    assert all(status.group(1) == "IMPLEMENTED_ISOLATED_TEST_ONLY"
               for status in statuses if status.group(1) != "EXCLUDED")
    stale_implementation_notes = (
        "静态候选读取；本次未做数据库动态验证或完整跨文件副作用证明，注册前须验证。",
        "库区/库位未适配",
        "库内库存未适配",
        "容量和库存查询仍未适配",
        "G01/G02/G03/G04：须共享鉴权、字段裁剪、公司归属和分页；对应 TASK-098/099",
        "Q094计算展开暂不注册",
        "其他业务单据详情仍须逐类适配",
    )
    implemented_sections = [
        section.group(0) for section, status in zip(q_sections, statuses)
        if status.group(1) != "EXCLUDED"
    ]
    required_entry_fields = (
        "原生输入：", "当前接口门槛：", "数据范围：", "副作用：", "缺口/归属：",
    )
    assert all(
        all(re.search(r"(?m)^- " + re.escape(field), section)
            for field in required_entry_fields)
        for section in implemented_sections
    ), "每个纳入入口都必须记录输入、权限、数据范围、副作用和缺口"
    assert all(
        not any(note in section for note in stale_implementation_notes)
        for section in implemented_sections
    ), "纳入并标记已实现的Q入口不得残留未实现模板说明"
    assert mapped_included_q_ids == included_q_ids, "存在纳入入口漏映射或排除项误纳入"
    assert "erp_get_my_access" in identity_section
    assert table_tool_names == set(MCP_TOOL_SPECS), "清单工具与实际注册表不一致"
    assert set(table_tools_by_q) == included_q_ids
    unpaged_qs_with_page_bounds = []
    input_contract_errors = []
    missing_role_gate_docs = []
    for section in q_sections:
        q_id = int(section.group(1))
        if q_id in excluded_q_ids:
            continue
        type_line = re.search(r"(?m)^- 类型：[^\r\n]*", section.group(0))
        assert type_line, f"Q{q_id:03} 缺少类型/实际工具映射"
        documented_tools = set(
            re.findall(r"`(erp_[a-z0-9_]+)`", type_line.group(0))
        )
        assert documented_tools == table_tools_by_q[q_id], (
            f"Q{q_id:03} 的详细工具映射与TASK-099增量表不一致："
            f"{documented_tools} != {table_tools_by_q[q_id]}"
        )
        required_roles = {
            role
            for tool_name in documented_tools
            if tool_name in MCP_TOOL_SPECS
            for role in CAPABILITIES[MCP_TOOL_SPECS[tool_name].capability].roles
        }
        role_context = table_role_context_by_q.get(q_id, "")
        missing_roles = sorted(
            role for role in required_roles
            if role not in role_context
            and not (role == "ADMIN" and "管理员" in role_context)
        )
        if missing_roles:
            missing_role_gate_docs.append(
                f"Q{q_id:03}: {','.join(missing_roles)}"
            )
        input_line = re.search(r"(?m)^- 原生输入：(.*)$", section.group(0))
        assert input_line, f"Q{q_id:03} 缺少原生输入字段说明"
        q_has_pagination = False
        for tool_name in documented_tools - {ACCESS_TOOL}:
            schema_fields = set(MCP_TOOL_SPECS[tool_name].arguments.model_fields)
            q_has_pagination |= bool(schema_fields & {"page", "page_size", "cursor"})
            assert (
                "page" not in schema_fields
                or "page_size" in schema_fields
            ), f"{tool_name} 定长分页必须同时定义page_size"
            assert (
                "page_size" not in schema_fields
                or "page" in schema_fields
                or "cursor" in schema_fields
            ), (
                f"{tool_name} 的page_size必须对应页码或游标分页"
            )
            if "page" in schema_fields or "cursor" in schema_fields:
                if not any(
                    marker in input_line.group(1)
                    for marker in ("page", "分页", "页长")
                ):
                    input_contract_errors.append(f"Q{q_id:03} 未说明分页")
                if not any(
                    marker in input_line.group(1)
                    for marker in ("page_size", "最大100", "≤100", "页长")
                ):
                    input_contract_errors.append(f"Q{q_id:03} 未说明分页上限")
            schema_fields -= {"page", "page_size"}
            for field_name in schema_fields:
                if not re.search(
                    rf"(?<![A-Za-z0-9_]){re.escape(field_name)}(?![A-Za-z0-9_])",
                    input_line.group(1),
                ):
                    input_contract_errors.append(
                        f"Q{q_id:03} 未记录 {tool_name}.{field_name}"
                    )
        if not q_has_pagination and re.search(
            r"page_size|页长|≤\s*100|最大\s*100", input_line.group(1), re.IGNORECASE,
        ):
            unpaged_qs_with_page_bounds.append(q_id)
    assert not unpaged_qs_with_page_bounds, (
        "无分页参数的Q条目不得在原生输入中声明MCP页长："
        f"{unpaged_qs_with_page_bounds}"
    )
    assert not input_contract_errors, "原生输入契约与工具schema不一致：" + "; ".join(
        input_contract_errors
    )
    assert not missing_role_gate_docs, (
        "TASK-099工具映射行必须记录对应运行时角色门槛："
        + "; ".join(missing_role_gate_docs)
    )
    supplier_balance_sections = {
        int(section.group(1)): section.group(0)
        for section in q_sections
        if int(section.group(1)) in {48, 49}
    }
    assert set(supplier_balance_sections) == {48, 49}
    assert CAPABILITIES["supplier_balances"].shared_legacy is True
    assert CAPABILITIES["supplier_balances"].roles == frozenset({"FINANCE"})
    for q_id, section in supplier_balance_sections.items():
        assert "S：供应商账户余额无员工/部门归属" in section, f"Q{q_id:03}误标制单人范围"
        assert "company_id=当前凭据公司" in section, f"Q{q_id:03}缺少公司过滤规则"
        assert "FINANCE角色" in section, f"Q{q_id:03}缺少MCP角色门槛"
    assert len(MCP_TOOL_SPECS) == 175
    assert all_registered_tools <= capability_tools, "存在未绑定权限能力的已注册工具"


def test_registered_tool_schemas_reject_untrusted_fields_and_enforce_page_limits():
    forbidden_fields = (
        "company_id", "role_id", "sql", "table_name", "arbitrary_url",
        "permission_scopes", "tool_scopes", "api_key",
    )
    for tool_name, spec in MCP_TOOL_SPECS.items():
        assert spec.arguments.model_config.get("extra") == "forbid", tool_name
        for field in forbidden_fields:
            try:
                spec.arguments.model_validate({field: "untrusted"})
            except ValidationError as error:
                assert any(
                    item["type"] == "extra_forbidden" and item["loc"] == (field,)
                    for item in error.errors()
                ), f"{tool_name} 没有明确拒绝额外字段 {field}"
            else:
                raise AssertionError(f"{tool_name} 接受了不允许的额外字段 {field}")
        if "page_size" in spec.arguments.model_fields:
            page_boundaries = [
                ({"page_size": 0}, "page_size", "greater_than_equal"),
                ({"page_size": 101}, "page_size", "less_than_equal"),
            ]
            if "page" in spec.arguments.model_fields:
                page_boundaries.append(({"page": 0}, "page", "greater_than_equal"))
            for payload, field, error_type in page_boundaries:
                try:
                    spec.arguments.model_validate(payload)
                except ValidationError as error:
                    assert any(
                        item["type"] == error_type and item["loc"] == (field,)
                        for item in error.errors()
                    ), f"{tool_name} 未按统一边界拒绝 {payload}"
                else:
                    raise AssertionError(f"{tool_name} 接受了越界分页参数 {payload}")


def test_unpaged_array_outputs_are_allowlisted_for_domain_bound_review():
    from app.services.mcp_tool_output_schemas import get_tool_data_schema

    expected_unpaged_arrays = {
        "erp_account_subject_get_subject_meta": {
            "balance_directions", "subject_categories",
        },
        "erp_assistant_capabilities": {
            "features", "features[].document_types", "modules",
        },
        "erp_dashboard_get_dashboard_overview": {
            "metrics", "sales_trend.items",
        },
        "erp_get_my_approval_detail": {
            "approval.approved_steps", "records",
        },
        "erp_list_approval_doc_types": {"$"},
        "erp_order_get_order": {"flags", "flags_label"},
        "erp_order_list_order_flags": {"$"},
        "erp_print_get_print_batch": {"orders"},
    }
    actual_unpaged_arrays = {}
    for tool_name, spec in MCP_TOOL_SPECS.items():
        fields = set(spec.arguments.model_fields)
        if fields & {"page", "page_size", "cursor"}:
            continue
        paths = _json_schema_array_paths(get_tool_data_schema(tool_name))
        if paths:
            actual_unpaged_arrays[tool_name] = paths

    assert actual_unpaged_arrays == expected_unpaged_arrays, (
        "无分页响应数组必须逐项确认固定上限、静态枚举或截断标记；"
        "新增、删除或变更时请同步域级测试和清单："
        f"{actual_unpaged_arrays}"
    )


def test_every_registered_tool_is_listed_by_its_declared_permission_capability():
    for tool_name, spec in MCP_TOOL_SPECS.items():
        capability = CAPABILITIES.get(spec.capability)
        assert capability is not None, f"{tool_name} 声明了未知能力 {spec.capability}"
        capability_tools = (
            {capability.tool}
            if isinstance(capability.tool, str)
            else set(capability.tool or ())
        )
        assert tool_name in capability_tools, (
            f"{tool_name} 未登记到其声明能力 {spec.capability}"
        )
