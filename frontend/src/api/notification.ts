import {
  ApiError,
  api,
  getAccessToken,
  handleUnauthorizedResponse,
  trackAuthenticatedStream,
} from '@/api/client'

export type NotificationCategory = 'approval' | 'purchase' | 'all'
export type NotificationReadState = 'UNREAD' | 'READ'

export interface NotificationItem {
  id: number
  event_code: string
  category: NotificationCategory
  title: string
  body: string
  target: { type: string; id: number }
  read_state: NotificationReadState
  read_at: string | null
  business_state: string
  created_at: string
  cursor: string
}

export interface NotificationPage {
  items: NotificationItem[]
  page_size: number
  next_cursor: string | null
}

export interface NotificationSignal {
  inbox_id: number
  category: NotificationCategory
  cursor: string
}

export interface NotificationStreamHandlers {
  onNotification: (signal: NotificationSignal) => void
  onConnected?: () => void
  onAuthExpired?: () => void
  onAuthRevoked?: () => void
  onPollingFallback?: () => void
  onError?: (error: Error) => void
}

export interface NotificationStreamOptions {
  signal?: AbortSignal
  reconnectDelayMs?: number
}

interface SseEvent {
  event: string
  data: string
  id?: string
}

async function* readSseEvents(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): AsyncGenerator<SseEvent> {
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = 'message'
  let eventId: string | undefined
  let dataLines: string[] = []

  const consumeLine = (line: string): SseEvent | null => {
    if (line === '') {
      const event = dataLines.length
        ? { event: eventName, data: dataLines.join('\n'), id: eventId }
        : null
      eventName = 'message'
      eventId = undefined
      dataLines = []
      return event
    }
    if (line.startsWith(':')) return null
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'event') eventName = value
    if (field === 'id') eventId = value
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
  try {
    const payload = (await response.json()) as {
      code?: unknown
      message?: unknown
    }
    return new ApiError(
      typeof payload.message === 'string' ? payload.message : '通知请求失败',
      typeof payload.code === 'string' ? payload.code : String(response.status),
      response.status,
    )
  } catch {
    return new ApiError('通知请求失败，请稍后重试', String(response.status), response.status)
  }
}

function waitForRetry(delayMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted || delayMs <= 0) return Promise.resolve()
  return new Promise((resolve) => {
    const timer = window.setTimeout(done, delayMs)
    function done() {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    signal.addEventListener('abort', done, { once: true })
    if (signal.aborted) done()
  })
}

function rememberSignal(deliveredIds: Set<number>, inboxId: number): boolean {
  if (deliveredIds.has(inboxId)) return false
  deliveredIds.add(inboxId)
  if (deliveredIds.size > 1_000) {
    const oldest = deliveredIds.values().next().value
    if (oldest !== undefined) deliveredIds.delete(oldest)
  }
  return true
}

async function pollForNotifications(
  handlers: NotificationStreamHandlers,
  signal: AbortSignal,
  deliveredIds: Set<number>,
  startedAt: number,
  delayMs: number,
): Promise<void> {
  handlers.onPollingFallback?.()
  while (!signal.aborted) {
    if (!getAccessToken()) {
      handlers.onAuthExpired?.()
      return
    }
    try {
      const page = await notificationApi.list({ page_size: 100 })
      const candidates = [...page.items].reverse()
      for (const item of candidates) {
        if (!rememberSignal(deliveredIds, item.id)) continue
        if (Date.parse(item.created_at) < startedAt) continue
        handlers.onNotification({
          inbox_id: item.id,
          category: item.category,
          cursor: item.cursor,
        })
      }
    } catch (error) {
      if (signal.aborted) return
      if (error instanceof ApiError && error.status === 401) {
        handlers.onAuthExpired?.()
        return
      }
      handlers.onError?.(
        error instanceof Error ? error : new Error('通知轮询失败'),
      )
    }
    await waitForRetry(delayMs, signal)
  }
}

export const notificationApi = {
  list(options: {
    cursor?: string
    category?: NotificationCategory
    read_state?: NotificationReadState
    page_size?: number
  } = {}): Promise<NotificationPage> {
    const params = new URLSearchParams()
    if (options.cursor) params.set('cursor', options.cursor)
    if (options.category) params.set('category', options.category)
    if (options.read_state) params.set('read_state', options.read_state)
    if (options.page_size) params.set('page_size', String(options.page_size))
    const query = params.toString()
    return api.get<NotificationPage>(`/notifications${query ? `?${query}` : ''}`)
  },

  getUnreadCount(): Promise<{ count: number }> {
    return api.get<{ count: number }>('/notifications/unread-count')
  },

  getDetail(inboxId: number): Promise<NotificationItem> {
    return api.get<NotificationItem>(`/notifications/${inboxId}`)
  },

  markRead(inboxId: number): Promise<{
    id: number
    read_state: NotificationReadState
    read_at: string | null
  }> {
    return api.post(`/notifications/${inboxId}/read`)
  },

  markAllRead(): Promise<{ updated_count: number }> {
    return api.post('/notifications/read-all')
  },

  async stream(
    handlers: NotificationStreamHandlers,
    options: NotificationStreamOptions = {},
  ): Promise<void> {
    const controller = new AbortController()
    const unregister = trackAuthenticatedStream(controller)
    const externalSignal = options.signal
    const abortFromCaller = () => controller.abort()
    if (externalSignal?.aborted) controller.abort()
    else externalSignal?.addEventListener('abort', abortFromCaller, { once: true })

    let lastEventId: string | undefined
    const deliveredIds = new Set<number>()
    const delay = Math.max(0, options.reconnectDelayMs ?? 5_000)
    const streamStartedAt = Date.now()
    let consecutiveFailures = 0

    try {
      while (!controller.signal.aborted) {
        const token = getAccessToken()
        if (!token) {
          handlers.onAuthExpired?.()
          return
        }
        let failed = false
        try {
          const response = await fetch(endpointUrl('/notifications/events'), {
            method: 'GET',
            headers: {
              Accept: 'text/event-stream',
              Authorization: `Bearer ${token}`,
              ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
            },
            cache: 'no-store',
            signal: controller.signal,
          })
          if (response.status === 401) {
            handleUnauthorizedResponse()
            handlers.onAuthExpired?.()
            return
          }
          if (response.status === 403) {
            handlers.onAuthRevoked?.()
            return
          }
          if (!response.ok) {
            const error = await responseError(response)
            handlers.onError?.(error)
            if (response.status >= 400 && response.status < 500) return
            failed = true
          } else if (!response.body) {
            handlers.onError?.(
              new ApiError('浏览器未提供流式响应能力', 'NOTIFICATION_STREAM_UNAVAILABLE'),
            )
            failed = true
          } else {
            handlers.onConnected?.()
            const reader = response.body.getReader()
            const connectionStartedAt = Date.now()
            try {
              for await (const event of readSseEvents(reader)) {
                if (event.id) lastEventId = event.id
                if (event.event === 'auth_expired') {
                  handleUnauthorizedResponse()
                  handlers.onAuthExpired?.()
                  return
                }
                if (event.event === 'auth_revoked') {
                  handlers.onAuthRevoked?.()
                  return
                }
                if (event.event !== 'notification') continue

                let payload: unknown
                try {
                  payload = JSON.parse(event.data)
                } catch {
                  handlers.onError?.(
                    new ApiError('通知流数据格式无效', 'NOTIFICATION_STREAM_INVALID'),
                  )
                  continue
                }
                if (!payload || typeof payload !== 'object') continue
                const data = payload as { inbox_id?: unknown; category?: unknown }
                if (
                  typeof data.inbox_id !== 'number' ||
                  !Number.isSafeInteger(data.inbox_id) ||
                  !event.id
                ) {
                  continue
                }
                if (!rememberSignal(deliveredIds, data.inbox_id)) continue
                const category = data.category
                handlers.onNotification({
                  inbox_id: data.inbox_id,
                  category:
                    category === 'approval' || category === 'purchase'
                      ? category
                      : 'all',
                  cursor: event.id,
                })
              }
            } catch (error) {
              if (controller.signal.aborted) return
              handlers.onError?.(
                error instanceof Error ? error : new Error('通知流连接中断'),
              )
            } finally {
              reader.releaseLock()
            }
            if (controller.signal.aborted) return
            if (Date.now() - connectionStartedAt >= 30_000) consecutiveFailures = 0
            failed = true
          }
        } catch (error) {
          if (controller.signal.aborted) return
          handlers.onError?.(
            error instanceof Error ? error : new Error('通知流连接失败'),
          )
          failed = true
        }

        if (controller.signal.aborted) return
        if (failed) consecutiveFailures += 1
        if (consecutiveFailures >= 3) {
          await pollForNotifications(
            handlers,
            controller.signal,
            deliveredIds,
            streamStartedAt,
            Math.max(delay, 5_000),
          )
          return
        }
        await waitForRetry(delay, controller.signal)
      }
    } finally {
      externalSignal?.removeEventListener('abort', abortFromCaller)
      unregister()
    }
  },
}
