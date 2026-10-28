import { afterEach, describe, expect, it, vi } from 'vitest'
import { assistantConversationsApi as api, type ConversationSend, type ConversationStreamHandlers } from '@/api/assistantConversations'
import { setAccessToken, stopAuthenticatedStreams } from '@/api/client'

const id = '00000000-0000-4000-8000-000000000001'
const payload: ConversationSend = { version: 1, request_id: id, client_message_id: id, session_id: id, session_version: 1, content: '本页操作说明', page_context: { path: '/dashboard' } }
const handlers = (): ConversationStreamHandlers => ({ onAccepted: vi.fn(), onSnapshot: vi.fn(), onStatus: vi.fn(), onSource: vi.fn(), onDelta: vi.fn(), onDone: vi.fn(), onError: vi.fn() })
const frame = (event: string, data: object) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
const accepted = { request_id: id, user_message_id: id, assistant_message_id: id, seq: 1, conversation_version: 2, replayed: false }
function response(content: string): Response {
  const bytes = new TextEncoder().encode(content)
  return new Response(new ReadableStream({ start(controller) { controller.enqueue(bytes.slice(0, 47)); controller.enqueue(bytes.slice(47)); controller.close() } }), { headers: { 'Content-Type': 'text/event-stream' } })
}
afterEach(() => { stopAuthenticatedStreams(); window.localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('持久会话 SSE 协议', () => {
  it('接收持久消息凭据与终态，发送不包含客户端 history', async () => {
    setAccessToken('current-token')
    const fetcher = vi.fn().mockResolvedValue(response(frame('accepted', accepted) + frame('delta', { text: '合法回答' }) + frame('done', { request_id: id, assistant_message_id: id, seq: 2, status: 'COMPLETED', conversation_version: 3 })))
    vi.stubGlobal('fetch', fetcher)
    const callbacks = handlers()
    const controller = new AbortController()
    await api.stream(id, payload, callbacks, controller)
    expect(callbacks.onAccepted).toHaveBeenCalledWith(accepted)
    expect(callbacks.onDelta).toHaveBeenCalledWith('合法回答')
    expect(callbacks.onDone).toHaveBeenCalledWith(expect.objectContaining({ status: 'COMPLETED', conversation_version: 3 }))
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit]
    expect(url).toContain(`/assistant/conversations/${id}/messages/stream`)
    expect(JSON.parse(init.body as string)).not.toHaveProperty('history')
    expect(init.headers).toMatchObject({ Authorization: 'Bearer current-token' })
    stopAuthenticatedStreams()
    expect(controller.signal.aborted).toBe(false)
  })

  it('幂等重放恢复 snapshot 及其终态，不假定回答成功', async () => {
    const record = { id, seq: 2, role: 'assistant', content: '权限已变化，此内容暂不可查看', status: 'INTERRUPTED', hidden: true, sources: [] }
    const fetcher = vi.fn().mockResolvedValue(response(frame('accepted', { ...accepted, replayed: true }) + frame('snapshot', { message: record, conversation_version: 3 }) + frame('done', { request_id: id, assistant_message_id: id, seq: 2, status: 'INTERRUPTED', conversation_version: 3, finish_reason: 'replayed' })))
    vi.stubGlobal('fetch', fetcher)
    const callbacks = handlers()
    await api.stream(id, payload, callbacks, new AbortController())
    expect(callbacks.onSnapshot).toHaveBeenCalledWith(record, 3)
    expect(callbacks.onDelta).not.toHaveBeenCalled()
    expect(callbacks.onDone).toHaveBeenCalledWith(expect.objectContaining({ status: 'INTERRUPTED' }))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('断流或错误请求 ID 明确报错且不自动重试', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(frame('accepted', accepted) + frame('delta', { text: '部分' })))
      .mockResolvedValueOnce(response(frame('accepted', { ...accepted, request_id: 'other-request' })))
    vi.stubGlobal('fetch', fetcher)
    await expect(api.stream(id, payload, handlers(), new AbortController())).rejects.toMatchObject({ code: 'AI_STREAM_INTERRUPTED' })
    await expect(api.stream(id, payload, handlers(), new AbortController())).rejects.toMatchObject({ code: 'AI_STREAM_INVALID' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('登录令牌变化会取消正在等待的持久流', async () => {
    setAccessToken('old-login')
    vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })))
    const pending = api.stream(id, payload, handlers(), new AbortController())
    setAccessToken('new-login')
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  })
})
