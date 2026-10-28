import { afterEach, describe, expect, it, vi } from 'vitest'

import { ACCESS_TOKEN_KEY, api } from '@/api/client'
import { assistantApi } from '@/api/assistant'

const REQUEST = {
  request_id: 'ba43af5a-4f6f-4af6-b204-c9471db8d720',
  session_id: 'b60ae67f-809a-48a2-853e-2c22c92f4221',
  session_version: 1,
  message: '请解释这个页面',
  history: [],
  page_context: { path: '/dashboard' },
}

function responseWithChunks(chunks: Uint8Array[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('assistant stream API', () => {
  it('creates and clears versioned assistant session metadata', async () => {
    const contract = {
      session_id: 'b60ae67f-809a-48a2-853e-2c22c92f4221',
      version: 1,
      expires_at: '2026-10-10T04:00:00Z',
    }
    const post = vi.spyOn(api, 'post').mockResolvedValue(contract)

    await expect(assistantApi.createSession()).resolves.toEqual(contract)
    await assistantApi.clearSession(contract.session_id, 1)

    expect(post).toHaveBeenNthCalledWith(1, '/assistant/sessions', {})
    expect(post).toHaveBeenNthCalledWith(
      2,
      `/assistant/sessions/${contract.session_id}/clear`,
      { version: 1 },
    )
  })

  it('uses the protected admin configuration and usage endpoints', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({
      enabled: true,
      provider: 'deepseek',
      model: 'deepseek-flash',
      api_key_configured: true,
      api_key_overrides_environment: true,
      requests_per_minute: 10,
      concurrent_requests_per_user: 1,
      daily_requests_per_user: null,
      monthly_budget: null,
      input_price_per_million: null,
      output_price_per_million: null,
      secret_store_ready: true,
      base_url: 'https://api.deepseek.com',
    })
    const put = vi.spyOn(api, 'put').mockResolvedValue({ api_key_configured: true })
    const config = await assistantApi.getAdminConfig()
    await assistantApi.updateAdminConfig({
      enabled: true,
      allow_attachment_external_send: false,
      provider: 'deepseek',
      model: 'deepseek-flash',
      api_key: 'test-secret',
      clear_api_key: false,
      requests_per_minute: 10,
      concurrent_requests_per_user: 1,
      daily_requests_per_user: null,
      monthly_budget: null,
      input_price_per_million: null,
      output_price_per_million: null,
    })
    await assistantApi.getAdminUsage(30)
    await assistantApi.getMyUsage(30)

    expect(config.api_key_configured).toBe(true)
    expect(get).toHaveBeenCalledWith('/assistant/admin/config')
    expect(put).toHaveBeenCalledWith('/assistant/admin/config', expect.objectContaining({
      api_key: 'test-secret',
      clear_api_key: false,
    }))
    expect(get).toHaveBeenCalledWith('/assistant/admin/usage?days=30')
    expect(get).toHaveBeenCalledWith('/assistant/usage/me?days=30')
  })

  it('parses fragmented UTF-8 SSE and sends the bearer token once', async () => {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, 'session-token')
    const encoder = new TextEncoder()
    const data = encoder.encode(
      'event: status\r\ndata: {"request_id":"ba43af5a-4f6f-4af6-b204-c9471db8d720","stage":"composing"}\r\n\r\nevent: source\ndata: {"items":[{"type":"guide","title":"采购计划帮助","href":"/purchase/plans"}]}\n\nevent: delta\ndata: {"text":"采购计划可核对状态"}\n\nevent: done\ndata: {"request_id":"ba43af5a-4f6f-4af6-b204-c9471db8d720","finish_reason":"completed"}\n\n',
    )
    const fetchMock = vi.fn().mockResolvedValue(
      responseWithChunks([data.slice(0, 79), data.slice(79, 93), data.slice(93)]),
    )
    vi.stubGlobal('fetch', fetchMock)
    const handlers = {
      onStatus: vi.fn(),
      onSource: vi.fn(),
      onDelta: vi.fn(),
    }

    await assistantApi.streamMessage(REQUEST, handlers, new AbortController().signal)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(options.headers).toMatchObject({ Authorization: 'Bearer session-token' })
    expect(handlers.onStatus).toHaveBeenCalledWith('composing')
    expect(handlers.onSource).toHaveBeenCalledWith([
      { type: 'guide', title: '采购计划帮助', href: '/purchase/plans' },
    ])
    expect(handlers.onDelta).toHaveBeenCalledWith('采购计划可核对状态')
  })

  it('clears an expired token when the pre-stream response is 401', async () => {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, 'expired-token')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ code: '40100', message: '未登录', data: null }),
          { status: 401, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    await expect(
      assistantApi.streamMessage(REQUEST, { onDelta: vi.fn() }, new AbortController().signal),
    ).rejects.toThrow('未登录')
    expect(window.localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull()
  })

  it('propagates cancellation without retrying the request', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn((_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const pending = assistantApi.streamMessage(
      REQUEST,
      { onDelta: vi.fn() },
      controller.signal,
    )
    controller.abort()

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
