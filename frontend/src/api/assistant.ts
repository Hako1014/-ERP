import {
  ApiError,
  api,
  getAccessToken,
  handleUnauthorizedResponse,
} from '@/api/client'

export type AssistantServiceStatus = 'ready' | 'unconfigured' | 'unavailable'
export type AssistantProviderMode = 'deepseek' | 'mock' | 'unavailable'

export interface AssistantFeature {
  code: string
  enabled: boolean
  reason?: string
  document_types?: string[]
}

export interface AssistantCapabilities {
  staff_id: number
  service_status: AssistantServiceStatus
  provider_mode: AssistantProviderMode
  roles: string[]
  permissions: string[]
  modules: string[]
  features: AssistantFeature[]
}

export interface AssistantAdminConfig {
  enabled: boolean
  allow_attachment_external_send: boolean
  provider: 'deepseek'
  model: string
  api_key_configured: boolean
  api_key_overrides_environment: boolean
  requests_per_minute: number
  concurrent_requests_per_user: number
  daily_requests_per_user: number | null
  monthly_budget: string | null
  input_price_per_million: string | null
  output_price_per_million: string | null
  secret_store_ready: boolean
  base_url: string
}

export interface AssistantAdminConfigPayload {
  enabled: boolean
  allow_attachment_external_send: boolean
  provider: 'deepseek'
  model: string
  api_key?: string | null
  clear_api_key: boolean
  requests_per_minute: number
  concurrent_requests_per_user: number
  daily_requests_per_user: number | null
  monthly_budget: string | null
  input_price_per_million: string | null
  output_price_per_million: string | null
}

export interface AssistantUsageRecord {
  request_id: string
  provider: string
  model: string | null
  status: string
  failure_category: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  latency_ms: number | null
  tool_names: string[]
  cost_status: string
  estimated_cost: string | null
  started_at: string | null
  completed_at: string | null
  staff_id?: number
  staff_name?: string | null
}

export interface AssistantUsageReport {
  summary: {
    request_count: number
    known_prompt_tokens: number
    known_completion_tokens: number
    unknown_token_count: number
    known_cost: string
    unknown_cost_count: number
    monthly_budget?: string | null
    monthly_budget_committed?: string
  }
  limits?: {
    requests_per_minute: number
    daily_requests_per_user: number | null
    concurrent_requests_per_user: number
  }
  items: AssistantUsageRecord[]
}

export interface AssistantHistoryMessage {
  role: 'user' | 'assistant'
  content: string
  page_context?: AssistantPageContext
}

export interface AssistantSessionContract {
  session_id: string
  version: number
  expires_at: string
}

export interface AssistantPageContext {
  path: string
  filters?: Partial<Record<'status' | 'date_from' | 'date_to', string>>
  /** 当前详情记录不构成授权；持久消息仅在服务端保存授权依赖以复核历史可见性。 */
  selected_document?: {
    doc_type: 'order' | 'purchase_order'
    doc_id: number
  }
}

export interface AssistantStreamRequest {
  request_id: string
  session_id: string
  session_version: number
  message: string
  history: AssistantHistoryMessage[]
  page_context?: AssistantPageContext
}

export type AssistantDraftStatus = 'recognized' | 'missing' | 'ambiguous' | 'invalid' | 'user_edited' | 'optional'

export interface AssistantDraftCandidate {
  id: number
  code?: string | null
  name: string
  art_no?: string | null
  skus?: AssistantDraftCandidate[]
}

export interface AssistantDraftField<T> {
  status: AssistantDraftStatus
  value: T | null
  query?: string | null
  source?: string | null
  candidates?: AssistantDraftCandidate[]
  input_value?: string | null
  input_unit?: string | null
  preview_value?: string | null
}

export interface AssistantPurchasePlanDraft {
  draft_id: string
  version: number
  doc_type: 'purchase_plan'
  source_kind: 'natural' | 'table' | 'attachment'
  draft_status?: 'EDITING' | 'READY' | 'CREATING' | 'CREATED' | 'FAILED' | 'CANCELLED' | 'EXPIRED'
  conversation_id?: string
  source_text?: string
  source_attachment_id?: string | null
  source_extraction_id?: string | null
  source_display_name?: string | null
  attach_source_on_confirm?: boolean
  created_plan_id?: number | null
  creation_request_id?: string | null
  creation_result?: Record<string, unknown> | null
  sources?: Array<{ ref: string; location: string; text?: string }>
  status: 'ready' | 'needs_input'
  ready: boolean
  supplier: AssistantDraftField<AssistantDraftCandidate>
  plan_date: AssistantDraftField<string>
  remark: AssistantDraftField<string>
  items: Array<{
    row: number
    product: AssistantDraftField<AssistantDraftCandidate>
    sku: AssistantDraftField<AssistantDraftCandidate>
    unit: AssistantDraftField<AssistantDraftCandidate>
    quantity: AssistantDraftField<string>
    purchase_price: AssistantDraftField<string>
    calculation: {
      unit_price: string | null
      amount: string | null
      source: string
    }
  }>
  table_mapping?: {
    columns: Array<{ column: number; header: string; field: string }>
    row_count: number
    errors: Array<{ row: number; message: string }>
  } | null
  errors: Array<{ row?: number; message: string }>
  missing: string[]
  edited_fields: string[]
  diff: Array<{ field: string; before: unknown; after: unknown; user_edit_preserved?: boolean }>
  estimated_total: string | null
  estimate_complete: boolean
  expires_at: string
  payload: {
    supplier_id: number
    plan_date: string
    remark: string | null
    items: Array<{
      product_id: number
      sku_id: number | null
      planned_qty: string
      purchase_price: string | null
    }>
  } | null
}

export interface AssistantDraftSession {
  session_id: string
  session_version: number
}

export interface AssistantDraftParsePayload extends AssistantDraftSession {
  request_id: string
  mode: 'natural' | 'table'
  input_text: string
}

export interface AssistantDraftEdits {
  supplier_id?: number | null
  plan_date?: string | null
  remark?: string | null
  items?: Array<{
    product_id?: number | null
    sku_id?: number | null
    unit_id?: number | null
    planned_qty?: string | null
    purchase_price?: string | null
  }>
}

export interface AssistantSource {
  type: string
  title: string
  href?: string
  queried_at?: string
  scope?: string
  /** 经当前记录权限校验后，仅返回 ERP 页面展示，不进入模型上下文。 */
  document_number?: string
  knowledge_version?: string
}

export interface AssistantStreamHandlers {
  onStatus?: (stage: string) => void
  onSource?: (items: AssistantSource[]) => void
  onDelta: (text: string) => void
}

interface SseEvent {
  event: string
  data: string
}

export async function* readSseEvents(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): AsyncGenerator<SseEvent> {
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = 'message'
  let dataLines: string[] = []

  const consumeLine = (line: string): SseEvent | null => {
    if (line === '') {
      if (dataLines.length === 0) {
        eventName = 'message'
        return null
      }
      const event = { event: eventName, data: dataLines.join('\n') }
      eventName = 'message'
      dataLines = []
      return event
    }
    if (line.startsWith(':')) return null
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'event') eventName = value
    if (field === 'data') dataLines.push(value)
    return null
  }

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    let newline = buffer.indexOf('\n')
    while (newline >= 0) {
      const line = buffer.slice(0, newline).replace(/\r$/, '')
      buffer = buffer.slice(newline + 1)
      const event = consumeLine(line)
      if (event) yield event
      newline = buffer.indexOf('\n')
    }
    if (done) break
  }

  if (buffer) {
    const event = consumeLine(buffer.replace(/\r$/, ''))
    if (event) yield event
  }
  const trailingEvent = consumeLine('')
  if (trailingEvent) yield trailingEvent
}

function endpointUrl(path: string): string {
  const baseUrl = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')
  return `${baseUrl}${path}`
}

async function responseError(response: Response): Promise<ApiError> {
  if (response.status === 401) handleUnauthorizedResponse()
  try {
    const payload = (await response.json()) as {
      code?: unknown
      message?: unknown
    }
    return new ApiError(
      typeof payload.message === 'string' ? payload.message : 'AI 请求失败',
      typeof payload.code === 'string' ? payload.code : String(response.status),
      response.status,
    )
  } catch {
    return new ApiError('AI 请求失败，请稍后重试', String(response.status), response.status)
  }
}

export const assistantApi = {
  createSession(): Promise<AssistantSessionContract> {
    return api.post<AssistantSessionContract>('/assistant/sessions', {})
  },

  clearSession(
    sessionId: string,
    version: number,
  ): Promise<AssistantSessionContract> {
    return api.post<AssistantSessionContract>(
      `/assistant/sessions/${encodeURIComponent(sessionId)}/clear`,
      { version },
    )
  },

  /**
   * 读取当前身份的助手能力与配置状态。
   * 这是展示信息，工具执行时后端仍会重新鉴权。
   */
  getCapabilities(): Promise<AssistantCapabilities> {
    return api.get<AssistantCapabilities>('/assistant/capabilities')
  },

  parseDraft(payload: AssistantDraftParsePayload): Promise<AssistantPurchasePlanDraft> {
    return api.post<AssistantPurchasePlanDraft>('/assistant/drafts/parse', payload)
  },

  getCurrentDraft(session: AssistantDraftSession): Promise<AssistantPurchasePlanDraft | null> {
    return api.get<AssistantPurchasePlanDraft | null>('/assistant/drafts/current', {
      params: session,
    })
  },

  editDraft(
    draftId: string,
    session: AssistantDraftSession,
    draftVersion: number,
    edits: AssistantDraftEdits,
  ): Promise<AssistantPurchasePlanDraft> {
    return api.put<AssistantPurchasePlanDraft>(`/assistant/drafts/${encodeURIComponent(draftId)}`, {
      ...session,
      draft_version: draftVersion,
      edits,
    })
  },

  reparseDraft(
    draftId: string,
    payload: AssistantDraftParsePayload & { draft_version: number },
  ): Promise<AssistantPurchasePlanDraft> {
    return api.post<AssistantPurchasePlanDraft>(
      `/assistant/drafts/${encodeURIComponent(draftId)}/reparse`,
      payload,
    )
  },

  cancelDraft(
    draftId: string,
    session: AssistantDraftSession,
    draftVersion: number,
  ): Promise<{ cancelled: boolean }> {
    return api.delete<{ cancelled: boolean }>(
      `/assistant/drafts/${encodeURIComponent(draftId)}`,
      { data: { ...session, draft_version: draftVersion } },
    )
  },

  listDocumentDrafts(conversation_id: string): Promise<AssistantPurchasePlanDraft[]> {
    return api.get<AssistantPurchasePlanDraft[]>('/assistant/document-drafts', { params: { conversation_id } })
  },

  getDocumentDraft(draftId: string): Promise<AssistantPurchasePlanDraft> {
    return api.get<AssistantPurchasePlanDraft>(`/assistant/document-drafts/${encodeURIComponent(draftId)}`)
  },

  parseDocumentDraft(payload: AssistantDraftParsePayload & { conversation_id: string }): Promise<AssistantPurchasePlanDraft> {
    return api.post<AssistantPurchasePlanDraft>('/assistant/document-drafts/parse', payload)
  },

  createDocumentDraftFromExtraction(payload: { conversation_id: string; extraction_id: string; request_id: string }): Promise<AssistantPurchasePlanDraft> {
    return api.post<AssistantPurchasePlanDraft>('/assistant/document-drafts/from-extraction', payload)
  },

  editDocumentDraft(draftId: string, draftVersion: number, edits: AssistantDraftEdits): Promise<AssistantPurchasePlanDraft> {
    return api.put<AssistantPurchasePlanDraft>(`/assistant/document-drafts/${encodeURIComponent(draftId)}`, {
      draft_version: draftVersion,
      edits,
    })
  },

  reparseDocumentDraft(draftId: string, payload: AssistantDraftParsePayload & { conversation_id: string; draft_version: number }): Promise<AssistantPurchasePlanDraft> {
    return api.post<AssistantPurchasePlanDraft>(`/assistant/document-drafts/${encodeURIComponent(draftId)}/reparse`, payload)
  },

  cancelDocumentDraft(draftId: string, draftVersion: number): Promise<{ cancelled: boolean; draft_id: string }> {
    return api.delete<{ cancelled: boolean; draft_id: string }>(
      `/assistant/document-drafts/${encodeURIComponent(draftId)}`,
      { params: { draft_version: draftVersion } },
    )
  },

  confirmDocumentDraft(
    draftId: string,
    payload: { draft_version: number; request_id: string; form: import('@/api/purchase').PurchasePlanCreatePayload; attach_source: boolean },
  ): Promise<import('@/api/purchase').PurchasePlan> {
    return api.post<import('@/api/purchase').PurchasePlan>(
      `/assistant/document-drafts/${encodeURIComponent(draftId)}/confirm`, payload,
    )
  },

  getAdminConfig(): Promise<AssistantAdminConfig> {
    return api.get<AssistantAdminConfig>('/assistant/admin/config')
  },

  updateAdminConfig(payload: AssistantAdminConfigPayload): Promise<AssistantAdminConfig> {
    return api.put<AssistantAdminConfig>('/assistant/admin/config', payload)
  },

  getAdminUsage(days = 30): Promise<AssistantUsageReport> {
    return api.get<AssistantUsageReport>(`/assistant/admin/usage?days=${days}`)
  },

  getMyUsage(days = 30): Promise<AssistantUsageReport> {
    return api.get<AssistantUsageReport>(`/assistant/usage/me?days=${days}`)
  },

  /**
   * 直接消费后端 SSE；请求只发起一次，不自动重试，取消通过 AbortSignal 传递。
   */
  async streamMessage(
    payload: AssistantStreamRequest,
    handlers: AssistantStreamHandlers,
    signal: AbortSignal,
  ): Promise<void> {
    const token = getAccessToken()
    const response = await fetch(endpointUrl('/assistant/messages/stream'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(payload),
      signal,
      cache: 'no-store',
    })
    if (!response.ok) throw await responseError(response)
    if (!response.body) {
      throw new ApiError('AI 服务未返回流式内容', 'AI_STREAM_UNAVAILABLE', response.status)
    }

    const reader = response.body.getReader()
    let completed = false
    try {
      for await (const event of readSseEvents(reader)) {
        let data: Record<string, unknown>
        try {
          data = JSON.parse(event.data) as Record<string, unknown>
        } catch {
          throw new ApiError('AI 流式响应格式无效', 'AI_STREAM_INVALID', response.status)
        }
        if (event.event === 'status' && typeof data.stage === 'string') {
          handlers.onStatus?.(data.stage)
        } else if (event.event === 'source' && Array.isArray(data.items)) {
          handlers.onSource?.(data.items as AssistantSource[])
        } else if (event.event === 'delta' && typeof data.text === 'string') {
          handlers.onDelta(data.text)
        } else if (event.event === 'error') {
          throw new ApiError(
            typeof data.message === 'string' ? data.message : 'AI 服务暂时异常',
            typeof data.code === 'string' ? data.code : 'AI_STREAM_ERROR',
            response.status,
          )
        } else if (event.event === 'done') {
          completed = true
        }
      }
    } catch (error) {
      await reader.cancel().catch(() => undefined)
      throw error
    } finally {
      reader.releaseLock()
    }
    if (!completed) {
      throw new ApiError('AI 流式连接意外结束，请检查网络后手动重试', 'AI_STREAM_INCOMPLETE')
    }
  },
}

export function featureEnabled(
  capabilities: AssistantCapabilities | null,
  code: string,
): boolean {
  if (!capabilities) {
    return false
  }
  return capabilities.features.some((item) => item.code === code && item.enabled)
}
