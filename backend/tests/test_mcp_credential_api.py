import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.api.mcp_credentials import IssueRequest
from app.core.errors import BizException
from app.db.base import Base
from app.models.mcp_credential import McpCredential
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services.mcp_credentials import (
    ACCESS_TOOL,
    REGISTERED_TOOLS,
    authenticate_credential,
    issue_credential,
)


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(Company(id=1, full_name="测试公司"))
        db.flush()
        db.add_all([
            Staff(id=1, company_id=1, name="管理员"),
            Role(id=1, company_id=1, code="ADMIN", name="管理员"),
        ])
        db.flush()
        db.add(StaffRole(staff_id=1, role_id=1))
        db.commit()
    yield factory
    engine.dispose()


def test_issue_request_accepts_all_registered_mcp_tools():
    tools = sorted(REGISTERED_TOOLS)

    request = IssueRequest(
        name="WorkBuddy ERP readonly",
        permission_scopes=["module:dashboard"],
        tool_scopes=tools,
    )

    assert request.tool_scopes == tools


def test_issue_request_accepts_permanent_credentials():
    request = IssueRequest(
        name="WorkBuddy ERP readonly",
        permission_scopes=["module:dashboard"],
        tool_scopes=["erp_get_my_access"],
        expires_days=None,
    )

    assert request.expires_days is None


def test_permanent_credential_authenticates_and_counts_as_active(factory):
    with factory() as db:
        staff = db.get(Staff, 1)
        issued = issue_credential(
            db,
            staff,
            name="永久凭据",
            permission_scopes=["module:dashboard"],
            tool_scopes=[ACCESS_TOOL],
            expires_days=None,
        )

    assert issued["expires_at"] is None
    with factory() as db:
        row = db.get(McpCredential, issued["id"])
        assert row.expires_at is None
        identity = authenticate_credential(db, issued["token"])
        assert identity.staff_id == 1

    with factory() as db:
        for index in range(9):
            issue_credential(
                db,
                db.get(Staff, 1),
                name=f"临时凭据{index}",
                permission_scopes=["module:dashboard"],
                tool_scopes=[ACCESS_TOOL],
                expires_days=7,
            )
        with pytest.raises(BizException):
            issue_credential(
                db,
                db.get(Staff, 1),
                name="超出上限",
                permission_scopes=["module:dashboard"],
                tool_scopes=[ACCESS_TOOL],
                expires_days=7,
            )
