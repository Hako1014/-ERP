import { api } from '@/api/client'

export interface StaffBrief {
  id: number
  name?: string | null
  staff_no?: string | null
  department_id?: number | null
  position_id?: number | null
  is_active?: boolean
}

export interface ApprovalStep {
  step: number
  name?: string | null
  approver_ids: number[]
  approvers: StaffBrief[]
}

export interface ApprovalFlowRecord {
  id: number
  name: string
  doc_type: string
  doc_type_label: string
  flow_display?: string | null
  steps: ApprovalStep[]
  created_by_name?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ApprovalDocType {
  code: string
  name: string
}

export interface ApprovalListItem {
  doc_type: string
  doc_type_label: string
  doc_id: number
  doc_no?: string | null
  business_status?: string | null
  audit_status?: string | null
  approval_status: string
  current_step?: number | null
  total_steps: number
  flow_name?: string | null
  submitted_by_name?: string | null
  submitted_at?: string | null
  current_approvers: StaffBrief[]
  latest_action: string
  latest_action_status: string
  latest_action_at?: string | null
}

export interface ApprovalListResult {
  total: number
  page: number
  page_size: number
  scope: string
  items: ApprovalListItem[]
}

export interface ApprovalDetail {
  doc_type: string
  doc_type_label: string
  doc_id: number
  doc_no?: string | null
  approval: {
    status: string
    current_step: number | null
    total_steps: number
    approved_steps: number[]
  }
  flow?: ApprovalFlowRecord | null
  records: Array<{
    id: number
    step: number
    step_name?: string | null
    action: string
    status: string
    approver_id?: number | null
    approver_name?: string | null
    comment?: string | null
    reason?: string | null
    audit_time?: string | null
    created_at?: string | null
  }>
  can_approve: boolean
  can_reject: boolean
  can_unaudit: boolean
  detail: Record<string, any>
}

export const approvalApi = {
  listDocTypes(): Promise<ApprovalDocType[]> {
    return api.get<ApprovalDocType[]>('/approval-manage/doc-types')
  },
  listFlows(): Promise<ApprovalFlowRecord[]> {
    return api.get<ApprovalFlowRecord[]>('/approval-manage/flows')
  },
  createFlow(payload: { name: string; doc_type: string; approvers: unknown[] }): Promise<ApprovalFlowRecord> {
    return api.post<ApprovalFlowRecord>('/approval-manage/flows', payload)
  },
  updateFlow(id: number, payload: { name?: string; doc_type?: string; approvers?: unknown[] }): Promise<ApprovalFlowRecord> {
    return api.put<ApprovalFlowRecord>(`/approval-manage/flows/${id}`, payload)
  },
  listMine(params: { scope: string; doc_type?: string; keyword?: string; page: number; page_size: number }): Promise<ApprovalListResult> {
    return api.get<ApprovalListResult>('/approval-manage/my-approvals', { params })
  },
  getDetail(docType: string, docId: number): Promise<ApprovalDetail> {
    return api.get<ApprovalDetail>(`/approval-manage/my-approvals/${encodeURIComponent(docType)}/${docId}`)
  },
  approve(docType: string, docId: number, comment?: string): Promise<ApprovalDetail> {
    return api.post<ApprovalDetail>(`/approval-manage/my-approvals/${encodeURIComponent(docType)}/${docId}/approve`, { comment })
  },
  reject(docType: string, docId: number, comment: string): Promise<ApprovalDetail> {
    return api.post<ApprovalDetail>(`/approval-manage/my-approvals/${encodeURIComponent(docType)}/${docId}/reject`, { comment })
  },
  unaudit(docType: string, docId: number, reason: string, step?: number): Promise<ApprovalDetail> {
    return api.post<ApprovalDetail>(`/approval-manage/my-approvals/${encodeURIComponent(docType)}/${docId}/unaudit`, { reason, step })
  },
}
