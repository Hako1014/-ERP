import { api, getAccessToken, trackAuthenticatedStream } from './client'

export interface AssistantAttachment {
  id: string; conversation_id: string; message_id: string | null; display_name: string
  media_type: string; size: number; status: 'SCANNING' | 'READY' | 'DELETED' | 'PURGED'
  purpose: 'CHAT'; model_sent: boolean; expires_at: string | null; created_at: string
}
export interface AttachmentPolicy {
  extensions: string[]; max_file_bytes: number; max_files: number; max_total_bytes: number
  owner_quota_bytes: number; max_image_pixels: number; max_pdf_pages: number
  max_zip_entries: number; max_uncompressed_bytes: number; orphan_hours: number; deleted_retention_days: number
}
export interface AttachmentPreview {
  kind: 'image' | 'text' | 'table' | 'metadata'; text?: string; rows?: string[][]
  sheet?: string; pages?: number; note?: string; truncated: boolean
}
export interface SaveAttachments {
  conversation_id: string; version: number; request_id: string; client_message_id: string
  attachment_ids: string[]; content: string
}
export interface RecognitionOptions {
  kind: 'image' | 'pdf' | 'xlsx' | 'text'; page_count: number | null; scan_pages: number[]
  sheets: string[]; attachment_id: string; conversation_id: string; external_send_allowed: boolean
}
export interface AssistantExtraction {
  id: string; attachment_id: string; conversation_id: string
  status: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'DELETED'
  input_mode: string; model: string | null; selections: { pages: number[]; sheet_name?: string; external_send_confirmed?: boolean }
  result: {
    document_type: 'purchase_plan' | 'other' | 'uncertain'; document_type_supported: boolean; supported_for_draft: boolean
    fields: Record<string, { value: string | null; source_ref: string | null; source_confirmed: boolean }>
    items: Array<{ fields: Record<string, { value: string | null; source_ref: string | null; source_confirmed: boolean }>; source_ref: string | null }>
    missing_fields: string[]; extracted_text: string | null; source_text: string; sources: Array<{ ref: string; location: string; text: string }>
    confirmation_required: boolean; created_business_document: false; notice: string
    usage: { model: string; provider_prompt_tokens: number | null; provider_completion_tokens: number | null; provider_total_tokens: number | null; image_count: number; image_usage_note: string }
  } | null
  error_category: string | null; created_at: string; updated_at: string
}
export interface RecognitionRequest {
  conversation_id: string; request_id: string; external_send_confirmed: boolean; pages: number[]; sheet_name: string | null
}
const base = '/assistant/attachments'
const path = (id: string) => `${base}/${encodeURIComponent(id)}`
export const assistantAttachmentsApi = {
  policy(signal?: AbortSignal) { return api.get<AttachmentPolicy>(`${base}/policy`, { signal }) },
  pending(conversation_id: string, signal?: AbortSignal) { return api.get<AssistantAttachment[]>(base, { params: { conversation_id }, signal }) },
  async upload(conversation_id: string, file: File, controller: AbortController, progress: (value: number) => void) {
    const form = new FormData()
    form.append('conversation_id', conversation_id); form.append('file', file)
    const unregister = trackAuthenticatedStream(controller)
    try {
      return await api.post<AssistantAttachment>(base, form, { signal: controller.signal, timeout: 120_000,
        onUploadProgress: (event) => progress(event.total ? Math.min(99, Math.round(event.loaded * 100 / event.total)) : 0) })
    } finally { unregister() }
  },
  preview(id: string, conversation_id: string, signal?: AbortSignal) {
    return api.get<AttachmentPreview>(`${path(id)}/preview`, { params: { conversation_id }, signal })
  },
  async content(id: string, conversation_id: string, controller: AbortController): Promise<Blob> {
    const token = getAccessToken(), unregister = trackAuthenticatedStream(controller)
    try {
      const result = await api.get<Blob>(`${path(id)}/content`, { params: { conversation_id }, responseType: 'blob', signal: controller.signal })
      if (controller.signal.aborted || token !== getAccessToken()) throw new DOMException('身份已变化', 'AbortError')
      return result
    } finally { unregister() }
  },
  remove(id: string, conversation_id: string, signal?: AbortSignal) {
    return api.delete<{ id: string; status: string }>(path(id), { params: { conversation_id }, signal })
  },
  save(payload: SaveAttachments, signal?: AbortSignal) {
    return api.post<{ version: number }>(`${base}/messages`, payload, { signal })
  },
  recognitionOptions(id: string, conversation_id: string, signal?: AbortSignal) {
    return api.get<RecognitionOptions>(`/assistant/attachments/${encodeURIComponent(id)}/recognition-options`, { params: { conversation_id }, signal })
  },
  extractions(id: string, conversation_id: string, signal?: AbortSignal) {
    return api.get<AssistantExtraction[]>(`/assistant/attachments/${encodeURIComponent(id)}/extractions`, { params: { conversation_id }, signal })
  },
  extract(id: string, payload: RecognitionRequest, signal?: AbortSignal) {
    return api.post<AssistantExtraction>(`/assistant/attachments/${encodeURIComponent(id)}/extractions`, payload, { signal, timeout: 120_000 })
  },
  extraction(id: string, signal?: AbortSignal) {
    return api.get<AssistantExtraction>(`/assistant/extractions/${encodeURIComponent(id)}`, { signal })
  },
  cancelExtraction(id: string, signal?: AbortSignal) {
    return api.delete<AssistantExtraction>(`/assistant/extractions/${encodeURIComponent(id)}`, { signal })
  },
}
