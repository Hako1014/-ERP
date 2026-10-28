"""ERP 登录下管理本人 MCP 凭据；不提供 MCP 凭据签发工具。"""

from fastapi import APIRouter, Depends, Path, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_staff
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import mcp_credentials as service

router = APIRouter(prefix="/mcp/credentials", tags=["MCP个人查询凭据"])


class IssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)
    permission_scopes: list[str] = Field(min_length=1, max_length=8)
    tool_scopes: list[str] = Field(
        default_factory=lambda: [service.ACCESS_TOOL],
        min_length=1,
        max_length=len(service.REGISTERED_TOOLS),
    )
    expires_days: int | None = Field(
        default=7, ge=1, le=30, description="有效天数；null 表示永久有效",
    )


@router.post("", response_model=Result[dict])
def issue(req: IssueRequest, response: Response, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return Result.ok(service.issue_credential(db, staff, **req.model_dump()))


@router.get("", response_model=Result[list[dict]])
def list_mine(staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.list_credentials(db, staff))


@router.post("/{credential_id}/revoke", response_model=Result[dict])
def revoke(credential_id: int = Path(gt=0), staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)):
    service.revoke_credential(db, staff, credential_id)
    return Result.ok({"revoked": True})
