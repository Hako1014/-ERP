"""基于制单员工的查询范围约束。无制单员工的旧记录不向普通员工开放。"""

from dataclasses import dataclass

from sqlalchemy import and_, event, select
from sqlalchemy.orm import Session

from app.db.base import CompanyOwnedBase
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.org import Staff
from app.models.rbac import DataScope, Role, StaffRole

DATA_SCOPE_POLICY_KEY = "data_scope_policy"
QUERY_COMPANY_SCOPE_KEY = "query_company_scope_id"
VALID_SCOPE_TYPES = frozenset({"SELF", "DEPARTMENT", "COMPANY"})
_POLICY_ERROR = "数据范围策略未初始化或与当前身份不匹配"


@event.listens_for(Session, "before_flush")
def _assign_company_to_new_rows(session: Session, _flush_context, _instances) -> None:
    """为已绑定身份的新业务行补齐当前公司，避免后续查询因归属为空而漏数。"""
    policy = session.info.get(DATA_SCOPE_POLICY_KEY)
    if not isinstance(policy, ScopePolicy):
        return
    for row in session.new:
        if isinstance(row, CompanyOwnedBase) and row.company_id is None:
            row.company_id = policy.company_id


@dataclass(frozen=True)
class ScopePolicy:
    staff_id: int
    company_id: int
    department_id: int | None
    scope_type: str


def resolve_policy(db: Session, staff: Staff) -> ScopePolicy:
    is_admin = db.scalar(
        select(StaffRole.id)
        .join(Role, Role.id == StaffRole.role_id)
        .where(StaffRole.staff_id == staff.id, Role.code == "ADMIN")
        .limit(1)
    ) is not None
    configured = db.scalars(
        select(DataScope.scope_type).where(DataScope.staff_id == staff.id)
    ).all()
    if is_admin:
        scope_type = "COMPANY"
    elif "COMPANY" in configured:
        scope_type = "COMPANY"
    elif "DEPARTMENT" in configured:
        scope_type = "DEPARTMENT"
    else:
        scope_type = "SELF"
    return ScopePolicy(staff.id, staff.company_id, staff.department_id, scope_type)


def initialize_scope_policy(db: Session, staff: Staff) -> ScopePolicy:
    """为当前数据库会话显式绑定已认证员工的数据范围策略。

    一个 Session 不允许在未清理旧身份的情况下切换员工或公司。
    独立请求/后台会话应先加载并授权 staff，再调用此函数；之后该会话中的
    公司归属模型查询自动限定到该员工的公司。
    """
    if not staff.id or not staff.company_id:
        raise RuntimeError(_POLICY_ERROR)

    if DATA_SCOPE_POLICY_KEY in db.info:
        existing = db.info[DATA_SCOPE_POLICY_KEY]
        if (
            not isinstance(existing, ScopePolicy)
            or existing.staff_id != staff.id
            or existing.company_id != staff.company_id
        ):
            raise RuntimeError(_POLICY_ERROR)

    existing_company = db.info.get(QUERY_COMPANY_SCOPE_KEY)
    if existing_company not in (None, staff.company_id):
        raise RuntimeError(_POLICY_ERROR)

    policy = resolve_policy(db, staff)
    if policy.scope_type not in VALID_SCOPE_TYPES:
        raise RuntimeError(_POLICY_ERROR)
    db.info[DATA_SCOPE_POLICY_KEY] = policy
    db.info[QUERY_COMPANY_SCOPE_KEY] = policy.company_id
    return policy


def clear_scope_policy(db: Session) -> None:
    """清除 Session 上的身份策略，供请求/事务生命周期结束时调用。"""
    db.info.pop(DATA_SCOPE_POLICY_KEY, None)
    db.info.pop(QUERY_COMPANY_SCOPE_KEY, None)
    db.info.pop("mcp_query_grant", None)


def require_scope_policy(
    db: Session,
    *,
    staff_id: int | None = None,
    company_id: int | None = None,
) -> ScopePolicy:
    """取得可信策略；策略缺失、损坏或与调用者身份不匹配时拒绝。"""
    policy = db.info.get(DATA_SCOPE_POLICY_KEY)
    if (
        not isinstance(policy, ScopePolicy)
        or policy.scope_type not in VALID_SCOPE_TYPES
        or policy.staff_id < 1
        or policy.company_id < 1
        or (staff_id is not None and policy.staff_id != staff_id)
        or (company_id is not None and policy.company_id != company_id)
    ):
        raise RuntimeError(_POLICY_ERROR)
    return policy


def owner_condition(owner_column, policy: ScopePolicy):
    owner_model = owner_column.property.parent.class_
    company_id_column = getattr(owner_model, "company_id", None)
    company_condition = (
        company_id_column == policy.company_id
        if company_id_column is not None
        else None
    )
    if policy.scope_type == "SELF":
        owner_filter = owner_column == policy.staff_id
        return and_(company_condition, owner_filter) if company_condition is not None else owner_filter
    # 外层报表也可能 JOIN Staff（采购员）；员工范围子查询必须独立，
    # 否则 SQLAlchemy 会自动关联掉它的 FROM，导致部门/公司报表失败。
    owners = select(Staff.id).where(Staff.company_id == policy.company_id).correlate(None)
    if policy.scope_type == "DEPARTMENT":
        if policy.department_id is None:
            owner_filter = owner_column == policy.staff_id
            return and_(company_condition, owner_filter) if company_condition is not None else owner_filter
        owners = owners.where(Staff.department_id == policy.department_id)
        owner_filter = owner_column.in_(owners)
        return and_(company_condition, owner_filter) if company_condition is not None else owner_filter
    if policy.scope_type == "COMPANY":
        owner_filter = owner_column.in_(owners)
        return and_(company_condition, owner_filter) if company_condition is not None else owner_filter
    raise RuntimeError(_POLICY_ERROR)


def _owner_visible(db: Session, owner_id: int | None, policy: ScopePolicy) -> bool:
    if owner_id is None:
        return False
    owner = db.get(Staff, owner_id, populate_existing=True)
    if owner is None or owner.company_id != policy.company_id:
        return False
    if policy.scope_type == "COMPANY":
        return True
    if policy.scope_type == "DEPARTMENT" and policy.department_id is not None:
        return owner.department_id == policy.department_id
    return owner.id == policy.staff_id


def apply_owner_scope(stmt, owner_column, db: Session):
    policy = require_scope_policy(db)
    return stmt.where(owner_condition(owner_column, policy))


def scope_conditions(db: Session, owner_column) -> list:
    policy = require_scope_policy(db)
    return [owner_condition(owner_column, policy)]


def owner_visible(db: Session, owner_id: int | None) -> bool:
    policy = require_scope_policy(db)
    return _owner_visible(db, owner_id, policy)


def document_visible(
    db: Session, owner_id: int | None, doc_type: str, doc_id: int
) -> bool:
    """制单范围内或审批流程中的本人可查看单据详情。"""
    policy = require_scope_policy(db)
    if _owner_visible(db, owner_id, policy):
        return True
    if owner_id is None:
        # 仅旧订单允许通过有效审批参与记录查看；先确认订单本身属于当前公司。
        if doc_type != "order":
            return False
        from app.models.order import Order

        if db.scalar(select(Order.id).where(
            Order.id == doc_id, Order.company_id == policy.company_id,
        )) is None:
            return False
    else:
        owner = db.get(Staff, owner_id, populate_existing=True)
        if owner is None or owner.company_id != policy.company_id:
            return False
    has_actor_record = db.scalar(
        select(ApprovalRecord.id).where(
            ApprovalRecord.company_id == policy.company_id,
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.status != "INVALID",
            ApprovalRecord.approver_id == policy.staff_id,
        ).limit(1)
    )
    if has_actor_record is not None:
        return True
    pending = db.scalar(
        select(ApprovalRecord).where(
            ApprovalRecord.company_id == policy.company_id,
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.status != "INVALID",
            ApprovalRecord.status == "PENDING",
        ).order_by(ApprovalRecord.id.desc()).limit(1)
    )
    if pending is None or pending.flow_id is None or pending.step < 1:
        return False
    flow = db.scalar(select(ApprovalFlow).where(
        ApprovalFlow.id == pending.flow_id,
        ApprovalFlow.company_id == policy.company_id,
    ).execution_options(populate_existing=True))
    if flow is None:
        return False
    from app.services.approval_service import steps_of

    return any(
        step.step == pending.step and policy.staff_id in step.approver_ids
        for step in steps_of(flow)
    )
