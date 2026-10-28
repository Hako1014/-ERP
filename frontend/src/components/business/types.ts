import type { StatusTone } from '@/components/types'

export interface AttachmentItem {
  uid?: string | number
  name: string
  url: string
  size?: number
}

export interface AttachmentUploadResponse {
  url: string
  filename: string
  size: number
}

export interface ExcelImportError {
  row: number
  field?: string
  message: string
}

export interface ExcelImportResult {
  total?: number
  created?: number
  updated?: number
  created_count?: number
  updated_count?: number
  error_count?: number
  errors?: ExcelImportError[]
  [key: string]: unknown
}

export interface PrintPayload {
  blob: Blob
  filename?: string
}

export type ApprovalStatus =
  | 'NOT_SUBMITTED'
  | 'PENDING'
  | 'APPROVED'
  | 'REJECTED'
  | 'UNKNOWN'

export interface ApprovalState {
  status: ApprovalStatus | string
  current_step: number | null
  total_steps: number
  approved_steps: number[]
}

export interface ApprovalRecord {
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
}

export interface ApprovalStateResponse {
  doc_type: string
  doc_id: number
  flow_name?: string | null
  state: ApprovalState
  records: ApprovalRecord[]
}

export interface OperationLogItem {
  id?: string | number
  action: string
  actor_name?: string | null
  target_type?: string | null
  target_id?: string | number | null
  summary?: string | null
  reason?: string | null
  detail?: unknown
  created_at?: string | null
}

export interface OperationTimelineItem {
  title: string
  description?: string
  timestamp?: string | null
  operator?: string
  status?: string
  statusLabel?: string
  statusTone?: StatusTone
}
