"""不依赖数据库的快捷录入策略测试；MySQL 草稿 API 集成测试另行执行。"""

import pytest
from types import SimpleNamespace

from app.api import quick_entry
from app.api.quick_entry import (
    DRAFT_DAYS,
    DOCUMENT_CATALOG,
    MAX_DRAFT_BYTES,
    _hash_payload,
    _validate_size,
)
from app.core.errors import BizException


def test_quick_entry_drafts_have_seven_day_retention_and_purchase_plan_adapter():
    assert DRAFT_DAYS == 7
    assert [item["document_type"] for item in DOCUMENT_CATALOG] == ["purchase_plan"]
    assert DOCUMENT_CATALOG[0]["permission"] == "purchase:create"


def test_request_hash_is_stable_for_equivalent_json_objects():
    assert _hash_payload({"supplier": "A", "qty": 2}) == _hash_payload({"qty": 2, "supplier": "A"})


def test_draft_payload_size_is_bounded():
    _validate_size({"remark": "x"})
    with pytest.raises(BizException, match="草稿内容过大"):
        _validate_size({"remark": "x" * MAX_DRAFT_BYTES})


def test_catalog_is_filtered_by_module_and_document_create_permissions(monkeypatch):
    staff = SimpleNamespace(id=18)
    monkeypatch.setattr(
        quick_entry,
        "current_actor_access",
        lambda _db, _staff_id: (staff, frozenset(), frozenset({"module:purchase", "purchase:create"})),
    )

    catalog = quick_entry.get_catalog(staff=staff, db=object())

    assert [item["document_type"] for item in catalog.data] == ["purchase_plan"]
    assert "permission" not in catalog.data[0]


def test_draft_type_requires_module_and_create_permission(monkeypatch):
    staff = SimpleNamespace(id=18)
    monkeypatch.setattr(
        quick_entry,
        "current_actor_access",
        lambda _db, _staff_id: (staff, frozenset(), frozenset({"module:purchase"})),
    )

    with pytest.raises(BizException) as error:
        quick_entry._spec(object(), staff, "purchase_plan")

    assert error.value.code.value == "40300"
