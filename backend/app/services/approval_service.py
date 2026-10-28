"""通用审批流引擎（TASK-019）。

记录语义（`approval_record` 表）：
- `action=SUBMIT  / status=DONE`    ：制单人提交留痕，`step=0`。
- `action=APPROVE / status=PENDING` ：当前环节待审核占位记录。
- `action=APPROVE / status=APPROVED`：该环节已审核通过（同一条占位记录就地更新）。
- `action=REJECT  / status=REJECTED`：该环节审核不通过。
- `action=UNAUDIT / status=DONE`    ：弃审留痕，`reason` 必填。
- `status=INVALID`                  ：被弃审覆盖而失效的历史环节。

引擎只维护审批记录与状态，不改业务单据本身的状态；业务服务在调用本引擎后，
依据返回的 `state` 更新自身 `audit_status/status` 字段，从而保持单据模块解耦。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.org import Staff
from app.repositories.approval_repo import (
    get_flow_by_id,
    get_flow_by_name,
    list_flows,
    list_flows_by_doc_type,
    list_records,
)
from app.repositories.org_repo import get_staff_by_id


class ApprovalAction:
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    UNAUDIT = "UNAUDIT"


class ApprovalStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INVALID = "INVALID"
    DONE = "DONE"


class DocApprovalState:
    NOT_SUBMITTED = "NOT_SUBMITTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ApprovalStep:
    step: int
    name: str | None
    approver_ids: tuple[int, ...]


def _now() -> datetime:
    """应用层统一存 UTC，模型列为无时区 DateTime。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _notification_doc_supported(doc_type: str) -> bool:
    from app.services.notification_service import DOC_MODELS

    return doc_type in DOC_MODELS


def _enqueue_approval_task_notice(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    doc_no: str | None,
    pending_record: ApprovalRecord,
    step: ApprovalStep,
    phase: str,
    staff: Staff,
) -> None:
    if not staff.company_id or not _notification_doc_supported(doc_type):
        return
    from app.services.notification_service import enqueue_approval_task

    enqueue_approval_task(
        db,
        company_id=staff.company_id,
        doc_type=doc_type,
        doc_id=doc_id,
        doc_no=doc_no,
        pending_record_id=pending_record.id,
        step=step.step,
        phase=phase,
        recipient_staff_ids=list(step.approver_ids),
    )


def _latest_submitter_id(db: Session, *, doc_type: str, doc_id: int) -> int | None:
    return db.scalar(
        select(ApprovalRecord.approver_id)
        .where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.action == ApprovalAction.SUBMIT,
            ApprovalRecord.status == ApprovalStatus.DONE,
        )
        .order_by(ApprovalRecord.id.desc())
        .limit(1)
    )


def _resolve_approval_task_notice(
    db: Session, *, record_id: int, staff_id: int | None = None
) -> None:
    from app.services.notification_service import resolve_approval_task_notifications

    resolve_approval_task_notifications(
        db,
        approval_record_id=record_id,
        handler_staff_id=staff_id,
    )


def _enqueue_approval_result_notice(
    db: Session,
    *,
    event_code: str,
    doc_type: str,
    doc_id: int,
    doc_no: str | None,
    terminal_record: ApprovalRecord,
    staff: Staff,
) -> None:
    if not staff.company_id or not _notification_doc_supported(doc_type):
        return
    from app.services.notification_service import enqueue_approval_result

    enqueue_approval_result(
        db,
        company_id=staff.company_id,
        event_code=event_code,
        doc_type=doc_type,
        doc_id=doc_id,
        doc_no=doc_no,
        terminal_record_id=terminal_record.id,
        submitter_id=_latest_submitter_id(db, doc_type=doc_type, doc_id=doc_id),
    )


def normalize_steps(raw: Any) -> list[ApprovalStep]:
    """把审批流配置标准化为有序环节列表。

    支持两种配置形态：
    - 简写：`[12, 15, 20]`，按顺序对应第 1、2、3 环节，各环节一个审批人。
    - 完整：`[{"step": 1, "name": "主管", "approver_ids": [12, 15]}, ...]`。
    """
    if not isinstance(raw, list) or not raw:
        raise BizException(ErrorCode.BAD_REQUEST, "审批人配置不能为空")

    steps: list[ApprovalStep] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, int):
            steps.append(ApprovalStep(step=index, name=None, approver_ids=(item,)))
            continue
        if not isinstance(item, dict):
            raise BizException(ErrorCode.BAD_REQUEST, f"第 {index} 个审批环节配置格式错误")

        step_no = item.get("step", index)
        if not isinstance(step_no, int) or step_no < 1:
            raise BizException(ErrorCode.BAD_REQUEST, f"第 {index} 个审批环节的 step 非法")

        ids = item.get("approver_ids")
        if ids is None and item.get("approver_id") is not None:
            ids = [item["approver_id"]]
        if not isinstance(ids, list) or not ids:
            raise BizException(ErrorCode.BAD_REQUEST, f"第 {step_no} 环节必须选择审批人")

        approver_ids: list[int] = []
        for approver_id in ids:
            if not isinstance(approver_id, int) or approver_id < 1:
                raise BizException(ErrorCode.BAD_REQUEST, "审批人 ID 非法")
            if approver_id not in approver_ids:
                approver_ids.append(approver_id)

        name = item.get("name")
        if name is not None and not isinstance(name, str):
            raise BizException(ErrorCode.BAD_REQUEST, f"第 {step_no} 环节名称非法")
        steps.append(
            ApprovalStep(step=step_no, name=name, approver_ids=tuple(approver_ids))
        )

    steps.sort(key=lambda s: s.step)
    expected = list(range(1, len(steps) + 1))
    if [s.step for s in steps] != expected:
        raise BizException(ErrorCode.BAD_REQUEST, "审批环节必须从 1 开始且连续编号")
    return steps


def _validate_approvers(db: Session, steps: list[ApprovalStep]) -> None:
    for step in steps:
        for approver_id in step.approver_ids:
            if get_staff_by_id(db, approver_id) is None:
                raise BizException(
                    ErrorCode.BAD_REQUEST, f"第 {step.step} 环节审批人不存在：{approver_id}"
                )


def steps_of(flow: ApprovalFlow) -> list[ApprovalStep]:
    if not flow.approvers:
        raise BizException(ErrorCode.CONFLICT, f"审批流「{flow.name}」未配置审批人")
    try:
        raw = json.loads(flow.approvers)
    except json.JSONDecodeError as exc:
        raise BizException(ErrorCode.CONFLICT, f"审批流「{flow.name}」配置解析失败") from exc
    return normalize_steps(raw)


def resolve_flow(db: Session, doc_type: str) -> ApprovalFlow:
    """按单据类型解析审批流；同类型配置多个流程时拒绝执行，避免误审批。"""
    flows = list_flows_by_doc_type(db, doc_type)
    if not flows:
        raise BizException(ErrorCode.NOT_FOUND, f"单据类型「{doc_type}」未配置审批流")
    if len(flows) > 1:
        raise BizException(
            ErrorCode.CONFLICT, f"单据类型「{doc_type}」配置了多个审批流，请先合并配置"
        )
    return flows[0]


def list_flow_dicts(db: Session) -> list[dict]:
    return [flow_dict(flow) for flow in list_flows(db)]


def flow_dict(flow: ApprovalFlow) -> dict:
    approvers = json.loads(flow.approvers) if flow.approvers else []
    return {
        "id": flow.id,
        "name": flow.name,
        "doc_type": flow.doc_type,
        "approvers": approvers,
        "created_by": flow.created_by,
        "created_at": flow.created_at,
        "updated_at": flow.updated_at,
    }


def create_flow(
    db: Session,
    *,
    name: str,
    doc_type: str,
    approvers: Any,
    actor: Staff,
    commit: bool = True,
) -> ApprovalFlow:
    if get_flow_by_name(db, name) is not None:
        raise BizException(ErrorCode.CONFLICT, f"审批流名称已存在：{name}")
    if list_flows_by_doc_type(db, doc_type):
        raise BizException(
            ErrorCode.CONFLICT,
            f"单据类型「{doc_type}」已配置审批流；当前引擎按单据类型唯一匹配，请直接修改原流程",
        )
    steps = normalize_steps(approvers)
    _validate_approvers(db, steps)

    flow = ApprovalFlow(
        company_id=actor.company_id,
        name=name,
        doc_type=doc_type,
        approvers=json.dumps(approvers, ensure_ascii=False),
        created_by=actor.id,
    )
    db.add(flow)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="approval_flow",
        target_id=str(flow.id),
        summary=f"新增审批流：{name}",
        detail={"doc_type": doc_type, "approvers": approvers},
        actor_id=actor.id,
        actor_name=actor.name,
    )
    _commit(db, commit)
    return flow


def update_flow(
    db: Session,
    *,
    flow_id: int,
    name: str | None = None,
    doc_type: str | None = None,
    approvers: Any = None,
    actor: Staff,
    commit: bool = True,
) -> ApprovalFlow:
    flow = get_flow_by_id(db, flow_id)
    if flow is None:
        raise BizException(ErrorCode.NOT_FOUND, "审批流不存在")

    if name is not None and name != flow.name:
        if get_flow_by_name(db, name) is not None:
            raise BizException(ErrorCode.CONFLICT, f"审批流名称已存在：{name}")
        flow.name = name
    if doc_type is not None and doc_type != flow.doc_type:
        if list_flows_by_doc_type(db, doc_type):
            raise BizException(
                ErrorCode.CONFLICT,
                f"单据类型「{doc_type}」已配置审批流；当前引擎按单据类型唯一匹配",
            )
        flow.doc_type = doc_type
    if approvers is not None:
        steps = normalize_steps(approvers)
        _validate_approvers(db, steps)

        flow.approvers = json.dumps(approvers, ensure_ascii=False)

    write_audit(
        db,
        action="UPDATE",
        target_type="approval_flow",
        target_id=str(flow.id),
        summary=f"修改审批流：{flow.name}",
        detail={"doc_type": flow.doc_type, "approvers": approvers},
        actor_id=actor.id,
        actor_name=actor.name,
    )
    _commit(db, commit)
    return flow


def submit(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    doc_no: str | None = None,
    comment: str | None = None,
    commit: bool = True,
) -> dict:
    """提交单据进入审批流，生成首环节待审核记录。"""
    flow = resolve_flow(db, doc_type)
    steps = steps_of(flow)
    records = list_records(db, doc_type, doc_id)
    if any(r.status == ApprovalStatus.PENDING for r in records):
        raise BizException(ErrorCode.CONFLICT, "单据已在审批中，请勿重复提交")

    state = _derive_state(records, total_steps=len(steps))
    if state["status"] == DocApprovalState.APPROVED:
        raise BizException(ErrorCode.CONFLICT, "单据已审核通过，如需修改请先弃审")
    if state["status"] == DocApprovalState.REJECTED:
        # 驳回后重提视为新一次审批，旧记录失效但保留完整操作历史。
        for record in records:
            if record.status != ApprovalStatus.INVALID:
                record.status = ApprovalStatus.INVALID

    now = _now()
    submit_record = ApprovalRecord(
            company_id=staff.company_id,
            flow_id=flow.id,
            doc_type=doc_type,
            doc_id=doc_id,
            doc_no=doc_no,
            step=0,
            approver_id=staff.id,
            action=ApprovalAction.SUBMIT,
            status=ApprovalStatus.DONE,
            comment=comment,
            audit_time=now,
        )
    pending_record = ApprovalRecord(
            company_id=staff.company_id,
            flow_id=flow.id,
            doc_type=doc_type,
            doc_id=doc_id,
            doc_no=doc_no,
            step=1,
            approver_id=None,
            action=ApprovalAction.APPROVE,
            status=ApprovalStatus.PENDING,
            audit_time=now,
        )
    db.add_all([submit_record, pending_record])
    db.flush()
    _enqueue_approval_task_notice(
        db,
        doc_type=doc_type,
        doc_id=doc_id,
        doc_no=doc_no,
        pending_record=pending_record,
        step=steps[0],
        phase="SUBMITTED",
        staff=staff,
    )
    write_audit(
        db,
        action="SUBMIT",
        target_type=doc_type,
        target_id=str(doc_id),
        summary=f"提交审批：{doc_no or doc_id}",
        detail={"flow": flow.name, "step": 1},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return get_state(db, doc_type=doc_type, doc_id=doc_id)["state"]


def _pending_record(records: list[ApprovalRecord]) -> ApprovalRecord | None:
    pending = [r for r in records if r.status == ApprovalStatus.PENDING]
    if not pending:
        return None
    return max(pending, key=lambda r: (r.step, r.id))


def _assert_step_actor(step: ApprovalStep, staff: Staff) -> None:
    if staff.id not in step.approver_ids:
        raise BizException(
            ErrorCode.FORBIDDEN, f"第 {step.step} 环节审批人不是当前用户，禁止越级审批"
        )


def _step_of(steps: list[ApprovalStep], step_no: int) -> ApprovalStep | None:
    for step in steps:
        if step.step == step_no:
            return step
    return None


def approve(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> dict:
    """通过当前环节；存在下一环节时自动流转，末环节通过后单据审核完成。"""
    flow = resolve_flow(db, doc_type)
    steps = steps_of(flow)
    records = list_records(db, doc_type, doc_id)
    current = _pending_record(records)
    if current is None:
        state = _derive_state(records, total_steps=len(steps))
        if state["status"] == DocApprovalState.APPROVED:
            raise BizException(ErrorCode.CONFLICT, "单据已审核通过，无需重复审核")
        raise BizException(ErrorCode.CONFLICT, "当前没有待审核环节")

    step_conf = _step_of(steps, current.step)
    if step_conf is None:
        raise BizException(ErrorCode.CONFLICT, f"审批流缺少第 {current.step} 环节配置")
    _assert_step_actor(step_conf, staff)

    now = _now()
    current.action = ApprovalAction.APPROVE
    current.status = ApprovalStatus.APPROVED
    current.approver_id = staff.id
    current.comment = comment
    current.audit_time = now
    _resolve_approval_task_notice(db, record_id=current.id, staff_id=staff.id)

    next_step = None
    pending_record = None
    if current.step < len(steps):
        next_step = current.step + 1
        pending_record = ApprovalRecord(
                company_id=staff.company_id,
                flow_id=flow.id,
                doc_type=doc_type,
                doc_id=doc_id,
                doc_no=current.doc_no,
                step=next_step,
                action=ApprovalAction.APPROVE,
                status=ApprovalStatus.PENDING,
                audit_time=now,
            )
        db.add(pending_record)

    write_audit(
        db,
        action="APPROVE",
        target_type=doc_type,
        target_id=str(doc_id),
        summary=f"审批通过第 {current.step} 环节",
        detail={"step": current.step, "next_step": next_step, "comment": comment},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.flush()
    if pending_record is not None and next_step is not None:
        step_conf = _step_of(steps, next_step)
        if step_conf is not None:
            _enqueue_approval_task_notice(
                db,
                doc_type=doc_type,
                doc_id=doc_id,
                doc_no=current.doc_no,
                pending_record=pending_record,
                step=step_conf,
                phase="ADVANCED",
                staff=staff,
            )
    else:
        _enqueue_approval_result_notice(
            db,
            event_code="approval.approved",
            doc_type=doc_type,
            doc_id=doc_id,
            doc_no=current.doc_no,
            terminal_record=current,
            staff=staff,
        )
    _commit(db, commit)
    return get_state(db, doc_type=doc_type, doc_id=doc_id)["state"]


def reject(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> dict:
    """审核不通过：当前环节置为 REJECTED，流程终止。"""
    flow = resolve_flow(db, doc_type)
    steps = steps_of(flow)
    records = list_records(db, doc_type, doc_id)
    current = _pending_record(records)
    if current is None:
        raise BizException(ErrorCode.CONFLICT, "当前没有待审核环节")

    step_conf = _step_of(steps, current.step)
    if step_conf is None:
        raise BizException(ErrorCode.CONFLICT, f"审批流缺少第 {current.step} 环节配置")
    _assert_step_actor(step_conf, staff)

    current.action = ApprovalAction.REJECT
    current.status = ApprovalStatus.REJECTED
    current.approver_id = staff.id
    current.comment = comment
    current.reason = comment
    current.audit_time = _now()
    _resolve_approval_task_notice(db, record_id=current.id, staff_id=staff.id)

    write_audit(
        db,
        action="REJECT",
        target_type=doc_type,
        target_id=str(doc_id),
        summary=f"审批不通过第 {current.step} 环节",
        reason=comment,
        detail={"step": current.step, "comment": comment},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.flush()
    _enqueue_approval_result_notice(
        db,
        event_code="approval.rejected",
        doc_type=doc_type,
        doc_id=doc_id,
        doc_no=current.doc_no,
        terminal_record=current,
        staff=staff,
    )
    _commit(db, commit)
    return get_state(db, doc_type=doc_type, doc_id=doc_id)["state"]


def unaudit(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> dict:
    """弃审：原因必填；目标环节及其后续已审环节全部失效，单据回到该环节待审核。"""
    if not reason or not reason.strip():
        raise BizException(ErrorCode.BAD_REQUEST, "弃审原因不能为空")

    flow = resolve_flow(db, doc_type)
    steps = steps_of(flow)
    records = list_records(db, doc_type, doc_id)
    if not records:
        raise BizException(ErrorCode.NOT_FOUND, "单据尚未提交审批")

    approved = [
        r
        for r in records
        if r.status == ApprovalStatus.APPROVED and r.step > 0
    ]
    if not approved:
        raise BizException(ErrorCode.CONFLICT, "单据尚未审核通过，无法弃审")

    target = step if step is not None else max(r.step for r in approved)
    if target not in {r.step for r in approved}:
        raise BizException(ErrorCode.BAD_REQUEST, f"第 {target} 环节尚未审核通过，无法弃审")

    submit_record = next(
        (r for r in records if r.action == ApprovalAction.SUBMIT), None
    )
    allowed_ids = {r.approver_id for r in approved if r.approver_id}
    if submit_record is not None and submit_record.approver_id:
        allowed_ids.add(submit_record.approver_id)
    if staff.id not in allowed_ids:
        raise BizException(ErrorCode.FORBIDDEN, "仅制单人或已审核人可执行弃审")

    doc_no = next((r.doc_no for r in records if r.doc_no), None)
    for record in records:
        if record.step >= target and record.status != ApprovalStatus.INVALID:
            record.status = ApprovalStatus.INVALID

    now = _now()
    unaudit_record = ApprovalRecord(
            company_id=staff.company_id,
            flow_id=flow.id,
            doc_type=doc_type,
            doc_id=doc_id,
            doc_no=doc_no,
            step=target,
            approver_id=staff.id,
            action=ApprovalAction.UNAUDIT,
            status=ApprovalStatus.DONE,
            reason=reason,
            audit_time=now,
        )
    pending_record = ApprovalRecord(
            company_id=staff.company_id,
            flow_id=flow.id,
            doc_type=doc_type,
            doc_id=doc_id,
            doc_no=doc_no,
            step=target,
            action=ApprovalAction.APPROVE,
            status=ApprovalStatus.PENDING,
            audit_time=now,
        )
    db.add_all([unaudit_record, pending_record])
    write_audit(
        db,
        action="UNAUDIT",
        target_type=doc_type,
        target_id=str(doc_id),
        summary=f"弃审第 {target} 环节，退回待审核",
        reason=reason,
        detail={"step": target, "invalidated_after": target},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.flush()
    if staff.company_id and _notification_doc_supported(doc_type):
        from app.services.notification_service import enqueue_approval_reopened

        enqueue_approval_reopened(
            db,
            company_id=staff.company_id,
            doc_type=doc_type,
            doc_id=doc_id,
            doc_no=doc_no,
            unaudit_record_id=unaudit_record.id,
            pending_record_id=pending_record.id,
            step=target,
            submitter_id=_latest_submitter_id(db, doc_type=doc_type, doc_id=doc_id),
        )
        step_conf = _step_of(steps, target)
        if step_conf is not None:
            _enqueue_approval_task_notice(
                db,
                doc_type=doc_type,
                doc_id=doc_id,
                doc_no=doc_no,
                pending_record=pending_record,
                step=step_conf,
                phase="REOPENED",
                staff=staff,
            )
    _commit(db, commit)
    return get_state(db, doc_type=doc_type, doc_id=doc_id)["state"]


def _derive_state(records: list[ApprovalRecord], *, total_steps: int) -> dict:
    effective = [r for r in records if r.status != ApprovalStatus.INVALID]
    if not effective:
        return {
            "status": DocApprovalState.NOT_SUBMITTED,
            "current_step": None,
            "total_steps": total_steps,
            "approved_steps": [],
        }

    pending = _pending_record(effective)
    approved_steps = sorted(
        r.step for r in effective if r.status == ApprovalStatus.APPROVED and r.step > 0
    )
    if pending is not None:
        return {
            "status": DocApprovalState.PENDING,
            "current_step": pending.step,
            "total_steps": total_steps,
            "approved_steps": approved_steps,
        }
    if any(r.status == ApprovalStatus.REJECTED for r in effective):
        return {
            "status": DocApprovalState.REJECTED,
            "current_step": None,
            "total_steps": total_steps,
            "approved_steps": approved_steps,
        }
    if total_steps > 0 and len(approved_steps) >= total_steps:
        return {
            "status": DocApprovalState.APPROVED,
            "current_step": None,
            "total_steps": total_steps,
            "approved_steps": approved_steps,
        }
    return {
        "status": DocApprovalState.PENDING,
        "current_step": None,
        "total_steps": total_steps,
        "approved_steps": approved_steps,
    }


def _record_dict(record: ApprovalRecord) -> dict:
    return {
        "id": record.id,
        "step": record.step,
        "action": record.action,
        "status": record.status,
        "approver_id": record.approver_id,
        "comment": record.comment,
        "reason": record.reason,
        "audit_time": record.audit_time,
    }


def get_state(db: Session, *, doc_type: str, doc_id: int) -> dict:
    """返回单据审批状态、当前环节与完整留痕（含失效记录）。"""
    records = list_records(db, doc_type, doc_id)
    flows = list_flows_by_doc_type(db, doc_type)
    total_steps = 0
    flow_name: str | None = None
    if flows:
        total_steps = len(steps_of(flows[0]))
        flow_name = flows[0].name
    elif records and records[0].flow_id:
        flow = get_flow_by_id(db, records[0].flow_id)
        if flow is not None:
            total_steps = len(steps_of(flow))
            flow_name = flow.name

    return {
        "doc_type": doc_type,
        "doc_id": doc_id,
        "flow_name": flow_name,
        "state": _derive_state(records, total_steps=total_steps),
        "records": [_record_dict(r) for r in records],
    }
