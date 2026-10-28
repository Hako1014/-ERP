import { ApiError, api, getAccessToken, handleUnauthorizedResponse, trackAuthenticatedStream } from '@/api/client'
import { readSseEvents, type AssistantPageContext, type AssistantSource } from '@/api/assistant'
import type { AssistantAttachment } from './assistantAttachments'

export type MessageStatus = 'PENDING' | 'STREAMING' | 'COMPLETED' | 'FAILED' | 'INTERRUPTED'
export interface Conversation {
  id: string
  title: string
  summary: string
  is_pinned: boolean
  status: 'ACTIVE' | 'ARCHIVED'
  version: number
  latest_message_seq: number
  message_count: number
  created_at: string
  updated_at: string
  retention_days: number | null
  matched_message_seq?: number | null
}
export interface ConversationMessage {
  id: string
  seq: number
  role: 'user' | 'assistant'
  content: string
  status: MessageStatus
  hidden: boolean
  attachments?: AssistantAttachment[]
  client_message_id: string | null
  request_id: string
  sources: AssistantSource[]
  page_context: AssistantPageContext | null
  error_code: string | null
  created_at: string
  updated_at: string
}
export interface ConversationPage {
  page: number
  page_size: number
  total: number
  items: Conversation[]
}
export interface MessagePage {
  items: ConversationMessage[]
  next_before_seq: number | null
  version: number
}
export interface ConversationSend {
  version: number
  request_id: string
  client_message_id: string
  session_id: string
  session_version: number
  content: string
  page_context?: AssistantPageContext
}
export interface AcceptedMessage {
  request_id: string
  user_message_id: string
  assistant_message_id?: string
  seq: number
  conversation_version: number
  replayed: boolean
}
export interface StreamEnd {
  request_id: string
  assistant_message_id: string
  seq: number
  status: MessageStatus
  conversation_version?: number
  finish_reason?: string
}
export interface ConversationStreamHandlers {
  onAccepted: (data: AcceptedMessage) => void
  onSnapshot: (message: ConversationMessage, version: number) => void
  onStatus: (stage: string) => void
  onSource: (items: AssistantSource[]) => void
  onDelta: (text: string) => void
  onDone: (data: StreamEnd) => void
  onError: (data: StreamEnd & { code: string; message: string }) => void
}

const base = '/assistant/conversations'
const path = (id: string) => `${base}/${encodeURIComponent(id)}`

export const assistantConversationsApi = {
  list(params: { page: number; page_size?: number; q?: string; status?: 'ACTIVE' | 'ARCHIVED' | 'ALL' }, signal?: AbortSignal) {
    return api.get<ConversationPage>(base, { params, signal })
  },
  create(title = '新会话') {
    return api.post<Conversation>(base, { title, request_id: crypto.randomUUID() })
  },
  get(id: string, signal?: AbortSignal) {
    return api.get<Conversation>(path(id), { signal })
  },
  update(id: string, version: number, changes: Partial<Pick<Conversation, 'title' | 'is_pinned' | 'status'>>) {
    return api.patch<Conversation>(path(id), { ...changes, version, request_id: crypto.randomUUID() })
  },
  remove(id: string, version: number) {
    return api.delete<{ id: string; version: number; status: string }>(path(id), { params: { version, request_id: crypto.randomUUID() } })
  },
  clear(id: string, version: number) {
    return api.post<{ id: string; version: number }>(`${path(id)}/clear`, { version, request_id: crypto.randomUUID() })
  },
  messages(id: string, before_seq?: number, signal?: AbortSignal) {
    return api.get<MessagePage>(`${path(id)}/messages`, { params: { limit: 50, before_seq }, signal })
  },
  cancel(id: string, messageId: string) {
    return api.post<{ id: string; status: MessageStatus; version: number }>(`${path(id)}/messages/${encodeURIComponent(messageId)}/cancel`, { request_id: crypto.randomUUID() })
  },
  async stream(id: string, payload: ConversationSend, handlers: ConversationStreamHandlers, controller: AbortController): Promise<void> {
    const token = getAccessToken()
    const unregister = trackAuthenticatedStream(controller)
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
    try {
      const response = await fetch(`${(import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')}${path(id)}/messages/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(payload), signal: controller.signal, cache: 'no-store',
      })
      if (!response.ok) {
        if (response.status === 401) handleUnauthorizedResponse()
        const error = await response.json().catch(() => ({})) as { message?: string; code?: string }
        throw new ApiError(error.message || '会话请求失败', error.code || String(response.status), response.status)
      }
      if (!response.body) throw new ApiError('服务器未返回流式内容', 'AI_STREAM_UNAVAILABLE')
      reader = response.body.getReader()
      let accepted = false
      let ended = false
      for await (const event of readSseEvents(reader)) {
        if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError')
        let data: Record<string, unknown>
        try { data = JSON.parse(event.data) as Record<string, unknown> } catch { throw new ApiError('会话响应格式无效', 'AI_STREAM_INVALID') }
        if (!data || typeof data !== 'object') throw new ApiError('会话响应格式无效', 'AI_STREAM_INVALID')
        if (typeof data.request_id === 'string' && data.request_id !== payload.request_id) throw new ApiError('会话响应请求不匹配', 'AI_STREAM_INVALID')
        if (event.event === 'accepted') {
          if (typeof data.user_message_id !== 'string' || !Number.isInteger(data.seq) || !Number.isInteger(data.conversation_version)) throw new ApiError('消息接收凭据无效', 'AI_STREAM_INVALID')
          accepted = true
          handlers.onAccepted(data as unknown as AcceptedMessage)
        } else if (event.event === 'error') {
          const error = data as unknown as StreamEnd & { code: string; message: string }
          handlers.onError(error)
          if (error.code === '40100') handleUnauthorizedResponse()
          throw new ApiError(error.message || '回答未完成', error.code || '50000')
        } else {
          if (!accepted) throw new ApiError('响应缺少消息接收凭据', 'AI_STREAM_INVALID')
          if (event.event === 'status' && typeof data.stage === 'string') handlers.onStatus(data.stage)
          if (event.event === 'source' && Array.isArray(data.items)) handlers.onSource(data.items as AssistantSource[])
          if (event.event === 'delta' && typeof data.text === 'string') handlers.onDelta(data.text)
          if (event.event === 'snapshot') handlers.onSnapshot(data.message as ConversationMessage, Number(data.conversation_version))
          if (event.event === 'done') {
            handlers.onDone(data as unknown as StreamEnd)
            ended = true
            return
          }
        }
      }
      if (!ended) throw new ApiError('连接已中断，请先恢复服务器保存状态', 'AI_STREAM_INTERRUPTED')
    } finally {
      if (reader) {
        await reader.cancel().catch(() => undefined)
        reader.releaseLock()
      }
      unregister()
    }
  },
}
