import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import ElementPlus, { ElMessageBox } from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import { useAssistantConversationsStore } from '@/stores/assistantConversations'
import { ApiError, setAccessToken } from '@/api/client'
import { assistantApi, type AssistantCapabilities } from '@/api/assistant'
import { assistantConversationsApi as api, type Conversation, type ConversationMessage, type ConversationSend, type ConversationStreamHandlers } from '@/api/assistantConversations'
import AssistantConversationPanel from '@/layouts/components/AssistantConversationPanel.vue'
import { assistantDate, conversationGroup } from '@/layouts/components/assistantConversationHelpers'

enableAutoUnmount(afterEach)
const A = '00000000-0000-4000-8000-000000000001'
const B = '00000000-0000-4000-8000-000000000002'
const permissions = ['module:dashboard', 'module:purchase', 'purchase:create']
const capabilities: AssistantCapabilities = { staff_id: 1, roles: ['PURCHASER'], permissions, modules: ['dashboard', 'purchase'], service_status: 'ready', provider_mode: 'deepseek', features: [{ code: 'read_query', enabled: true }] }

function conversation(id: string, title = '采购沟通'): Conversation {
  return { id, title, summary: '已有的问题', status: 'ACTIVE', is_pinned: false, version: 1,
    latest_message_seq: 2, message_count: 2, created_at: '2026-10-14T01:00:00', updated_at: '2026-10-14T01:00:00', retention_days: null }
}
function message(role: 'user' | 'assistant', content: string, seq: number, extra: Partial<ConversationMessage> = {}): ConversationMessage {
  return { id: crypto.randomUUID(), seq, role, content, status: 'COMPLETED', hidden: false,
    client_message_id: role === 'user' ? crypto.randomUUID() : null, request_id: A, sources: [],
    page_context: role === 'assistant' ? { path: '/purchase/plans' } : null, error_code: null,
    created_at: '2026-10-14T01:00:00', updated_at: '2026-10-14T01:00:00', ...extra }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('BroadcastChannel', undefined)
  window.innerWidth = 1366
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function setup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.setLoginSession({ staff_id: 1, name: '采购员', token: 'token1', roles: ['PURCHASER'] })
  auth.setPermissionSnapshot({ staff_id: 1, company_id: 1, company_name: '公司一', roles: ['PURCHASER'], permissions, modules: ['dashboard', 'purchase'] })
  const records = new Map([[A, conversation(A)], [B, conversation(B, '其他会话')]])
  const rows = new Map<string, ConversationMessage[]>([[A, [message('user', '已保存的采购问题', 1), message('assistant', '已保存的回答', 2)]], [B, []]])
  const clone = <T>(value: T) => structuredClone(value)
  const list = vi.spyOn(api, 'list').mockImplementation(async (params) => {
    const items = [...records.values()].filter((row) => params.status === 'ALL' || row.status === (params.status || 'ACTIVE'))
    return { page: params.page, page_size: 20, total: items.length, items: clone(items) }
  })
  const get = vi.spyOn(api, 'get').mockImplementation(async (id) => clone(records.get(id)!))
  const messages = vi.spyOn(api, 'messages').mockImplementation(async (id, before) => ({
    items: clone((rows.get(id) || []).filter((row) => before === undefined || row.seq < before).slice(-50)),
    next_before_seq: null, version: records.get(id)!.version,
  }))
  const create = vi.spyOn(api, 'create').mockImplementation(async () => {
    const row = { ...conversation(crypto.randomUUID(), '新会话'), latest_message_seq: 0, message_count: 0 }
    records.set(row.id, row); rows.set(row.id, [])
    return clone(row)
  })
  const update = vi.spyOn(api, 'update').mockImplementation(async (id, version, changes) => {
    const row = records.get(id)!
    if (row.version !== version) throw new ApiError('版本冲突', '40900', 409)
    Object.assign(row, changes); row.version += 1
    return clone(row)
  })
  const remove = vi.spyOn(api, 'remove').mockImplementation(async (id) => { records.delete(id); return { id, version: 2, status: 'DELETED' } })
  vi.spyOn(api, 'clear').mockImplementation(async (id) => { rows.set(id, []); records.get(id)!.version += 1; return { id, version: records.get(id)!.version } })
  const cancel = vi.spyOn(api, 'cancel').mockImplementation(async (id, messageId) => {
    const row = rows.get(id)!.find((item) => item.id === messageId)!
    row.status = 'INTERRUPTED'; records.get(id)!.version += 1
    return { id: messageId, status: 'INTERRUPTED', version: records.get(id)!.version }
  })
  const session = vi.spyOn(assistantApi, 'createSession').mockImplementation(async () => ({ session_id: crypto.randomUUID(), version: 1, expires_at: new Date(Date.now() + 3600_000).toISOString() }))
  const successfulStream = async (id: string, payload: ConversationSend, handlers: ConversationStreamHandlers) => {
    const record = records.get(id)!
    const user = message('user', payload.content, record.latest_message_seq + 1, { request_id: payload.request_id, client_message_id: payload.client_message_id })
    const reply = message('assistant', '服务器完成的回答', user.seq + 1, { request_id: payload.request_id })
    rows.get(id)!.push(user, reply)
    record.latest_message_seq = reply.seq; record.version += 1
    handlers.onAccepted({ request_id: payload.request_id, user_message_id: user.id, assistant_message_id: reply.id, seq: user.seq, conversation_version: record.version, replayed: false })
    handlers.onDelta(reply.content)
    record.version += 1
    handlers.onDone({ request_id: payload.request_id, assistant_message_id: reply.id, seq: reply.seq, status: 'COMPLETED', conversation_version: record.version })
  }
  const stream = vi.spyOn(api, 'stream').mockImplementation(successfulStream)
  const store = useAssistantConversationsStore()
  store.setPermissions(permissions)
  return { pinia, auth, store, records, rows, list, get, messages, create, update, remove, cancel, session, stream, successfulStream }
}

async function panel(ctx: ReturnType<typeof setup>, workspace = true) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/assistant', component: { template: '<div />' } }] })
  await router.push('/assistant'); await router.isReady()
  const wrapper = mount(AssistantConversationPanel, { props: { capabilities, capabilityFailed: false, pageContext: { path: '/dashboard' }, suggestions: [], workspace }, global: { plugins: [ctx.pinia, router, ElementPlus] } })
  await flushPromises(); await nextTick()
  return wrapper
}

describe('持久会话状态', () => {
  it('恢复最近会话只读取历史，本地只记录归属范围内的 UUID', async () => {
    const ctx = setup()
    window.localStorage.setItem('erp_assistant_last_v1_1_1', B)
    await ctx.store.initialize()
    expect(ctx.store.active?.id).toBe(B)
    expect(ctx.stream).not.toHaveBeenCalled()
    await ctx.store.select(A)
    const saved = window.localStorage.getItem('erp_assistant_last_v1_1_1')
    expect(saved).toBe(A)
    expect([...Array(window.localStorage.length)].map((_, index) => window.localStorage.getItem(window.localStorage.key(index)!)).join(' ')).not.toContain('已保存的采购问题')
  })

  it('新建保留旧历史，并为切换后的会话创建不同运行 session', async () => {
    const ctx = setup(); await ctx.store.initialize()
    await ctx.store.send('第一问', { path: '/dashboard' })
    await ctx.store.create()
    await ctx.store.send('新会话问题', { path: '/dashboard' })
    expect(ctx.rows.get(A)?.some((row) => row.content === '第一问')).toBe(true)
    expect(ctx.stream.mock.calls[0][1].session_id).not.toBe(ctx.stream.mock.calls[1][1].session_id)
    expect(ctx.stream.mock.calls[0][1]).not.toHaveProperty('history')
  })

  it('发送准备阶段禁止双击产生两次模型提交', async () => {
    const ctx = setup(); await ctx.store.initialize()
    const waiting = deferred<Awaited<ReturnType<typeof assistantApi.createSession>>>()
    ctx.session.mockReturnValueOnce(waiting.promise)
    const first = ctx.store.send('只发送一次', { path: '/dashboard' })
    const second = ctx.store.send('双击重复', { path: '/dashboard' })
    waiting.resolve({ session_id: B, version: 1, expires_at: new Date(Date.now() + 60_000).toISOString() })
    await Promise.all([first, second])
    expect(ctx.stream).toHaveBeenCalledTimes(1)
  })

  it('旧会话的 session 迟到结果不会解除新会话的发送锁', async () => {
    const ctx = setup(); await ctx.store.initialize()
    const oldSession = deferred<Awaited<ReturnType<typeof assistantApi.createSession>>>()
    const newSession = deferred<Awaited<ReturnType<typeof assistantApi.createSession>>>()
    ctx.session.mockReturnValueOnce(oldSession.promise).mockReturnValueOnce(newSession.promise)
    const oldSend = ctx.store.send('旧问题', { path: '/dashboard' })
    await ctx.store.select(B)
    const newSend = ctx.store.send('新会话问题', { path: '/dashboard' })
    oldSession.resolve({ session_id: A, version: 1, expires_at: new Date(Date.now() + 60_000).toISOString() })
    await oldSend
    expect(ctx.store.preparing).toBe(true)
    expect(ctx.stream).not.toHaveBeenCalled()
    newSession.resolve({ session_id: B, version: 1, expires_at: new Date(Date.now() + 60_000).toISOString() })
    await newSend
    expect(ctx.stream).toHaveBeenCalledTimes(1)
    expect(ctx.stream.mock.calls[0][0]).toBe(B)
  })

  it('重新登录后可停止服务器保存的未结束回答，不调用模型', async () => {
    const ctx = setup()
    ctx.rows.get(A)![1].status = 'STREAMING'
    await ctx.store.initialize()
    expect(ctx.store.generating).toBe(true)
    await ctx.store.stop()
    expect(ctx.cancel).toHaveBeenCalledWith(A, ctx.rows.get(A)![1].id)
    expect(ctx.store.generating).toBe(false)
    expect(ctx.stream).not.toHaveBeenCalled()
  })

  it('会话切换取消旧流，迟到回答不会进入新会话', async () => {
    const ctx = setup(); await ctx.store.initialize()
    let handlers!: ConversationStreamHandlers
    let controller!: AbortController
    const done = deferred<void>()
    ctx.stream.mockImplementationOnce(async (_id, _payload, currentHandlers, currentController) => { handlers = currentHandlers; controller = currentController; await done.promise })
    const running = ctx.store.send('旧会话问题', { path: '/dashboard' })
    await flushPromises()
    await ctx.store.select(B)
    expect(controller.signal.aborted).toBe(true)
    handlers.onDelta('迟到的私人回答')
    done.resolve(); await running
    expect(ctx.store.active?.id).toBe(B)
    expect(ctx.store.messages).toEqual([])
  })

  it('退出和同员工更换令牌立即清空内存，恢复由服务器完成', async () => {
    const ctx = setup(); await ctx.store.initialize()
    expect(ctx.store.messages[1].content).toBe('已保存的回答')
    ctx.auth.setLoginSession({ staff_id: 1, token: 'token2', name: '采购员', roles: ['PURCHASER'] })
    expect(ctx.store.messages).toEqual([])
    ctx.auth.setPermissionSnapshot({ staff_id: 1, company_id: 1, company_name: '公司一', permissions, roles: ['PURCHASER'], modules: ['dashboard', 'purchase'] })
    ctx.store.setPermissions(permissions); await ctx.store.initialize()
    expect(ctx.store.messages[1].content).toBe('已保存的回答')
    ctx.auth.clearSession()
    expect(ctx.store.active).toBeNull(); expect(ctx.store.messages).toEqual([])
    expect(ctx.stream).not.toHaveBeenCalled()
  })

  it('用户/公司改变后不读取另一归属的本地会话指针', async () => {
    const ctx = setup(); await ctx.store.initialize()
    window.localStorage.setItem('erp_assistant_last_v1_2_1', B)
    ctx.auth.setPermissionSnapshot({ staff_id: 1, company_id: 2, roles: ['PURCHASER'], permissions, modules: ['dashboard', 'purchase'] })
    expect(ctx.store.messages).toEqual([])
    ctx.store.setPermissions(permissions); await ctx.store.initialize()
    expect(ctx.store.active?.id).toBe(B)
    expect(window.localStorage.getItem('erp_assistant_last_v1_1_1')).toBe(A)
  })

  it('搜索定位通过授权匹配序号加载历史页，恢复不调用模型', async () => {
    const ctx = setup(); await ctx.store.initialize()
    await ctx.store.select(A, { seq: 1, query: '采购' })
    expect(ctx.messages).toHaveBeenLastCalledWith(A, 3, expect.any(AbortSignal))
    expect(ctx.store.matchedSeq).toBe(1)
    expect(ctx.store.latestPage).toBe(false)
    expect(ctx.stream).not.toHaveBeenCalled()
  })

  it('返回会话恢复内存中的历史页和滚动位置，正文与位置均不落本地存储', async () => {
    const ctx = setup(); await ctx.store.initialize()
    await ctx.store.refreshCurrent(false, 101)
    ctx.store.rememberViewport(88)
    await ctx.store.select(B)
    await ctx.store.select(A)
    expect(ctx.messages).toHaveBeenLastCalledWith(A, 101, expect.any(AbortSignal))
    expect(ctx.store.restoreScrollTop).toBe(88)
    expect(window.localStorage.getItem('erp_assistant_last_v1_1_1')).toBe(A)
  })

  it('忽略同一会话历史分页的迟到响应', async () => {
    const ctx = setup(); await ctx.store.initialize()
    const waiting = deferred<Awaited<ReturnType<typeof api.messages>>>()
    ctx.messages.mockReturnValueOnce(waiting.promise)
    const old = ctx.store.refreshCurrent(false, 2)
    await ctx.store.refreshCurrent()
    waiting.resolve({ items: [message('user', '迟到的旧页', 1)], next_before_seq: null, version: 1 })
    await old
    expect(ctx.store.messages[0].content).toBe('已保存的采购问题')
    expect(ctx.store.latestPage).toBe(true)
  })

  it('不确定发送结果的重试复用原幂等键，不自动重发', async () => {
    const ctx = setup(); await ctx.store.initialize()
    ctx.stream.mockRejectedValueOnce(new ApiError('断连', 'AI_STREAM_INTERRUPTED'))
    await ctx.store.send('可能已收到的问题', { path: '/dashboard' })
    const original = ctx.stream.mock.calls[0][1]
    expect(ctx.store.unresolved).toBe(true)
    expect(ctx.stream).toHaveBeenCalledTimes(1)
    await ctx.store.retryUnresolved()
    const retried = ctx.stream.mock.calls[1][1]
    expect(retried.client_message_id).toBe(original.client_message_id)
    expect(retried.request_id).toBe(original.request_id)
  })

  it('服务器已有原问题时只恢复状态，不再次请求模型', async () => {
    const ctx = setup(); await ctx.store.initialize()
    ctx.stream.mockRejectedValueOnce(new ApiError('断连', 'AI_STREAM_INTERRUPTED'))
    await ctx.store.send('已收但未确认的问题', { path: '/dashboard' })
    const sent = ctx.stream.mock.calls[0][1]
    ctx.rows.get(A)!.push(message('user', sent.content, 3, { client_message_id: sent.client_message_id, request_id: sent.request_id }), message('assistant', '后台保存的中断回答', 4, { request_id: sent.request_id, status: 'INTERRUPTED' }))
    await ctx.store.retryUnresolved()
    expect(ctx.stream).toHaveBeenCalledTimes(1)
    expect(ctx.store.unresolved).toBe(false)
  })

  it('过期运行 session 的错误保留持久历史，允许手工再提问', async () => {
    const ctx = setup(); await ctx.store.initialize()
    ctx.stream.mockRejectedValueOnce(new ApiError('会话过期', '40901', 409))
    await ctx.store.send('session 过期的问题', { path: '/dashboard' })
    expect(ctx.store.runtime).toBeNull()
    expect(ctx.store.messages[0].content).toBe('已保存的采购问题')
    expect(ctx.store.notice).toContain('会话')
  })

  it('归档、恢复、重命名和置顶按可见版本提交，版本冲突只刷新不重写', async () => {
    const ctx = setup(); await ctx.store.initialize()
    await ctx.store.mutate(ctx.store.active!, { title: '新名称', is_pinned: true })
    expect(ctx.records.get(A)?.title).toBe('新名称')
    await ctx.store.mutate(ctx.store.active!, { status: 'ARCHIVED' })
    expect(ctx.store.active).toBeNull()
    ctx.store.status = 'ARCHIVED'; await ctx.store.loadList()
    expect(ctx.store.conversations[0].id).toBe(A)
    const visible = ctx.store.conversations[0]
    ctx.records.get(A)!.version += 1
    expect(await ctx.store.mutate(visible, { status: 'ACTIVE' })).toBe(false)
    expect(ctx.update).toHaveBeenCalledTimes(3)
    expect(ctx.store.notice).toContain('已同步服务器状态')
    expect(ctx.records.get(A)?.status).toBe('ARCHIVED')
  })

  it('加载故障保留已加载历史，显式权限核验时立即遮蔽回答和来源', async () => {
    const ctx = setup(); await ctx.store.initialize()
    ctx.messages.mockRejectedValueOnce(new ApiError('网络故障', '50301', 503))
    await ctx.store.refreshCurrent()
    expect(ctx.store.messages[1].content).toBe('已保存的回答')
    const waiting = deferred<Awaited<ReturnType<typeof api.messages>>>()
    ctx.messages.mockReturnValueOnce(waiting.promise)
    const restoring = ctx.store.refreshCurrent(true)
    expect(ctx.store.messages[1].hidden).toBe(true)
    expect(ctx.store.messages[1].sources).toEqual([])
    waiting.resolve({ items: [message('user', '已保存的采购问题', 1), message('assistant', '后端意外夹带的旧秘密', 2, { hidden: true })], next_before_seq: null, version: 1 })
    await restoring
    expect(ctx.store.messages[1].content).not.toContain('旧秘密')
    expect(ctx.stream).not.toHaveBeenCalled()
  })

  it('其他标签页修改登录令牌后立即遮蔽旧身份数据', async () => {
    const ctx = setup(); await ctx.store.initialize()
    setAccessToken('another-login')
    await ctx.store.refresh(true)
    expect(ctx.store.messages).toEqual([])
    expect(ctx.store.notice).toContain('登录状态在其他页面已变化')
  })
})

describe('会话工作台交互', () => {
  it('会话切换恢复用户滚动位置，不被重复自动滚动覆盖', async () => {
    const ctx = setup(); const wrapper = await panel(ctx)
    const log = wrapper.find('.assistant-float__messages').element as HTMLElement
    Object.defineProperties(log, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 200, configurable: true } })
    log.scrollTop = 88; log.dispatchEvent(new Event('scroll'))
    const buttons = wrapper.findAll('.conversation-list__select')
    await buttons[1].trigger('click'); await flushPromises(); await nextTick()
    await buttons[0].trigger('click'); await flushPromises(); await nextTick()
    expect(log.scrollTop).toBe(88)
  })
  it('跨标签通知仅携带归属和会话 ID，读取不会回广播形成循环', async () => {
    const ctx = setup()
    const post = vi.fn()
    let channel!: { onmessage: ((event: MessageEvent) => void) | null }
    vi.stubGlobal('BroadcastChannel', class {
      onmessage: ((event: MessageEvent) => void) | null = null
      constructor() { channel = this }
      postMessage = post
      close() {}
    })
    await panel(ctx)
    await ctx.store.refreshCurrent(); await nextTick()
    expect(post).not.toHaveBeenCalled()
    channel.onmessage?.({ data: { type: 'refresh', company: 1, staff: 1, conversation: A } } as MessageEvent)
    await flushPromises(); await nextTick()
    expect(post).not.toHaveBeenCalled()
    await ctx.store.mutate(ctx.store.active!, { is_pinned: true }); await nextTick()
    expect(post).toHaveBeenCalledTimes(1)
    expect(JSON.stringify(post.mock.calls[0])).not.toContain('已保存的采购问题')
  })
  it('宽屏显示私人历史栏与关键词高亮，消息内容不会作为 HTML 执行', async () => {
    const ctx = setup()
    ctx.rows.set(A, [message('user', '定位采购关键词 <img onerror="alert(1)">', 1), message('assistant', '文字回答', 2)])
    const wrapper = await panel(ctx)
    expect(wrapper.find('.conversation-list').exists()).toBe(true)
    await ctx.store.select(A, { seq: 1, query: '采购' }); await nextTick()
    expect(wrapper.find('mark').text()).toBe('采购')
    expect(wrapper.find('.assistant-float__messages img').exists()).toBe(false)
  })

  it('窄屏历史抽屉可打开，搜索无结果有清空入口', async () => {
    const ctx = setup(); window.innerWidth = 360
    const wrapper = await panel(ctx)
    await wrapper.find('[aria-label="打开历史会话"]').trigger('click'); await flushPromises()
    expect(wrapper.findComponent({ name: 'ElDrawer' }).props('modelValue')).toBe(true)
    ctx.store.query = '没有的关键词'; ctx.store.conversations = []; ctx.store.total = 0; await nextTick()
    expect(wrapper.text()).toContain('历史会话')
  })

  it('权限变化占位不提供复制、重试或来源链接', async () => {
    const ctx = setup()
    ctx.rows.get(A)![1] = message('assistant', '私密旧文本', 2, { hidden: true, sources: [{ type: 'guide', title: '旧来源', href: '/purchase/plans' }] })
    const wrapper = await panel(ctx)
    const hidden = wrapper.find('.is-hidden')
    expect(hidden.text()).toContain('权限已变化')
    expect(hidden.find('a').exists()).toBe(false)
    expect(hidden.find('button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('私密旧文本')
  })

  it('清空确认取消时不发送写请求', async () => {
    const ctx = setup(); const wrapper = await panel(ctx)
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel')
    const clear = wrapper.findAll('button').find((button) => button.text() === '清空')!
    await clear.trigger('click'); await flushPromises()
    expect(api.clear).not.toHaveBeenCalled()
    expect(ctx.store.messages).toHaveLength(2)
  })

  it('时间无偏移量按 UTC 解析，最近分组按上海日期', () => {
    expect(assistantDate('2026-10-13T16:30:00').toISOString()).toBe('2026-10-13T16:30:00.000Z')
    expect(conversationGroup('2026-10-13T16:30:00', false, new Date('2026-10-14T01:00:00Z'))).toBe('今天')
    expect(conversationGroup('2026-10-12T16:30:00', false, new Date('2026-10-14T01:00:00Z'))).toBe('昨天')
  })
})
