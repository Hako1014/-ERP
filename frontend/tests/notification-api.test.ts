import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { notificationApi } from '@/api/notification'
import {
  getAccessToken,
  setAccessToken,
  setUnauthorizedHandler,
} from '@/api/client'

describe('notification API stream', () => {
  beforeEach(() => {
    window.localStorage.clear()
    setUnauthorizedHandler(null)
    setAccessToken('stream-token')
  })

  afterEach(() => {
    setAccessToken(null)
    setUnauthorizedHandler(null)
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('uses Bearer auth, resumes by signed event cursor, and deduplicates IDs', async () => {
    const signal =
      'id: signed-cursor-1\nevent: notification\ndata: {"inbox_id":17,"category":"approval"}\n\n'
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(signal + signal, {
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      )
      .mockResolvedValueOnce(
        new Response('event: auth_expired\ndata: {"code":"TOKEN_EXPIRED"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const onNotification = vi.fn()
    const onAuthExpired = vi.fn()
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)

    await notificationApi.stream(
      { onNotification, onAuthExpired },
      { reconnectDelayMs: 0 },
    )

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [firstUrl, firstInit] = fetchMock.mock.calls[0]
    const [secondUrl, secondInit] = fetchMock.mock.calls[1]
    expect(String(firstUrl)).not.toContain('stream-token')
    expect(String(secondUrl)).not.toContain('stream-token')
    expect(new Headers(firstInit?.headers).get('Authorization')).toBe(
      'Bearer stream-token',
    )
    expect(new Headers(secondInit?.headers).get('Last-Event-ID')).toBe(
      'signed-cursor-1',
    )
    expect(onNotification).toHaveBeenCalledOnce()
    expect(onNotification).toHaveBeenCalledWith({
      inbox_id: 17,
      category: 'approval',
      cursor: 'signed-cursor-1',
    })
    expect(onAuthExpired).toHaveBeenCalledOnce()
    expect(unauthorized).toHaveBeenCalledOnce()
    expect(getAccessToken()).toBeNull()
  })

  it('aborts an active stream when the login token is cleared', async () => {
    let requestSignal: AbortSignal | undefined
    const fetchMock = vi.fn<typeof fetch>((_input, init) => {
      requestSignal = init?.signal as AbortSignal
      return new Promise((_resolve, reject) => {
        requestSignal?.addEventListener(
          'abort',
          () => reject(new DOMException('Aborted', 'AbortError')),
          { once: true },
        )
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const stream = notificationApi.stream({ onNotification: vi.fn() })
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    setAccessToken(null)
    await expect(stream).resolves.toBeUndefined()
    expect(requestSignal?.aborted).toBe(true)
  })

  it('falls back to inbox polling after repeated SSE failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response('temporarily unavailable', { status: 503 }),
      ),
    )
    const item = {
      id: 29,
      event_code: 'approval.task_assigned',
      category: 'approval' as const,
      title: '审批待办',
      body: '测试消息',
      target: { type: 'purchase_plan', id: 100 },
      read_state: 'UNREAD' as const,
      read_at: null,
      business_state: 'OPEN',
      created_at: new Date(Date.now() + 1_000).toISOString(),
      cursor: 'signed-cursor-29',
    }
    vi.spyOn(notificationApi, 'list').mockResolvedValue({
      items: [item],
      page_size: 100,
      next_cursor: null,
    })
    const onPollingFallback = vi.fn()
    const onNotification = vi.fn(() => setAccessToken(null))

    await notificationApi.stream(
      { onNotification, onPollingFallback },
      { reconnectDelayMs: 0 },
    )

    expect(onPollingFallback).toHaveBeenCalledOnce()
    expect(onNotification).toHaveBeenCalledWith({
      inbox_id: 29,
      category: 'approval',
      cursor: 'signed-cursor-29',
    })
  })
})
