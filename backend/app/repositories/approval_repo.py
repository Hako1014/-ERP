from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import ApprovalFlow, ApprovalRecord


def get_flow_by_name(db: Session, name: str) -> ApprovalFlow | None:
    return db.scalar(select(ApprovalFlow).where(ApprovalFlow.name == name))


def get_flow_by_id(db: Session, flow_id: int) -> ApprovalFlow | None:
    return db.get(ApprovalFlow, flow_id)


def list_flows(db: Session) -> list[ApprovalFlow]:
    return list(db.scalars(select(ApprovalFlow).order_by(ApprovalFlow.id)))


def list_flows_by_doc_type(db: Session, doc_type: str) -> list[ApprovalFlow]:
    return list(
        db.scalars(
            select(ApprovalFlow)
            .where(ApprovalFlow.doc_type == doc_type)
            .order_by(ApprovalFlow.id)
        )
    )


def list_records(db: Session, doc_type: str, doc_id: int) -> list[ApprovalRecord]:
    """按环节号和写入顺序返回单据的全部审批记录（含失效记录）。"""
    return list(
        db.scalars(
            select(ApprovalRecord)
            .where(ApprovalRecord.doc_type == doc_type, ApprovalRecord.doc_id == doc_id)
            .order_by(ApprovalRecord.step, ApprovalRecord.id)
        )
    )


def list_pending_records(db: Session) -> list[ApprovalRecord]:
    """返回所有待审核占位记录，供我的待审批按当前环节审批人过滤。"""
    return list(
        db.scalars(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.action == "APPROVE",
                ApprovalRecord.status == "PENDING",
            )
            .order_by(ApprovalRecord.audit_time.desc(), ApprovalRecord.id.desc())
        )
    )


def list_records_by_actor(
    db: Session, staff_id: int, actions: list[str]
) -> list[ApprovalRecord]:
    """返回指定员工执行过的审批动作，用于已提交和已处理列表。"""
    if not actions:
        return []
    return list(
        db.scalars(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.approver_id == staff_id,
                ApprovalRecord.action.in_(actions),
            )
            .order_by(ApprovalRecord.audit_time.desc(), ApprovalRecord.id.desc())
        )
    )
