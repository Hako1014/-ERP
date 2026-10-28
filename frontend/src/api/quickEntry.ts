import { api } from '@/api/client'

export interface QuickEntryDocumentType {
  module_key: string
  module_label: string
  document_type: string
  document_label: string
  target_path: string
}

export interface QuickEntryDraftData {
  supplier_name: string
  plan_date: string
  remark: string
  items: Array<{
    product_name: string
    sku_name: string
    unit_name: string
    quantity: string
    purchase_price: string
  }>
  errors: Array<{ row?: number; message: string }>
  missing: string[]
}

export interface QuickEntryDraft {
  draft_id: string
  company_id: number
  staff_id: number
  module_key: string
  document_type: string
  source_mode: 'natural' | 'table'
  title: string
  input_text: string
  draft_data: QuickEntryDraftData
  status: 'EDITING' | 'APPLIED' | 'EXPIRED' | 'DELETED'
  version: number
  applied_version: number | null
  expires_at: string
  updated_at: string
}

export const quickEntryApi = {
  catalog(): Promise<QuickEntryDocumentType[]> {
    return api.get<QuickEntryDocumentType[]>('/quick-entry/catalog')
  },
  listDrafts(): Promise<QuickEntryDraft[]> {
    return api.get<QuickEntryDraft[]>('/quick-entry/drafts')
  },
  createDraft(payload: {
    request_id: string
    document_type: string
    source_mode: 'natural' | 'table'
    title: string
    input_text: string
    draft_data: QuickEntryDraftData
  }): Promise<QuickEntryDraft> {
    return api.post<QuickEntryDraft>('/quick-entry/drafts', payload)
  },
  updateDraft(id: string, payload: {
    draft_version: number
    title: string
    input_text: string
    source_mode: 'natural' | 'table'
    draft_data: QuickEntryDraftData
  }): Promise<QuickEntryDraft> {
    return api.put<QuickEntryDraft>(`/quick-entry/drafts/${id}`, payload)
  },
  confirmImport(id: string, draft_version: number): Promise<QuickEntryDraft> {
    return api.post<QuickEntryDraft>(`/quick-entry/drafts/${id}/confirm-import`, { draft_version })
  },
  deleteDraft(id: string): Promise<{ deleted: boolean }> {
    return api.delete<{ deleted: boolean }>(`/quick-entry/drafts/${id}`)
  },
}
