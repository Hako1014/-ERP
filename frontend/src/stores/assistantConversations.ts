import { computed, ref, watch, reactive } from 'vue'
import { defineStore } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { ApiError, getAccessToken } from '@/api/client'
import { assistantApi, type AssistantPageContext, type AssistantSessionContract } from '@/api/assistant'
import { assistantConversationsApi as conversationsApi, type Conversation, type ConversationMessage, type ConversationSend } from '@/api/assistantConversations'
import { isConversationId, sourceAllowed } from '@/layouts/components/assistantConversationHelpers'

const terminal = (status: string) => ['COMPLETED', 'FAILED', 'INTERRUPTED'].includes(status)
const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败，请稍后重试'

export const useAssistantConversationsStore = defineStore('assistant-conversations', () => {
  const auth = useAuthStore()
  const conversations = ref<Conversation[]>([])
  const active = ref<Conversation | null>(null)
  const messages = ref<ConversationMessage[]>([])
  const query = ref('')
  const status = ref<'ACTIVE' | 'ARCHIVED' | 'ALL'>('ACTIVE')
  const page = ref(1)
  const total = ref(0)
  const listLoading = ref(false)
  const messagesLoading = ref(false)
  const creating = ref(false)
  const streaming = ref(false)
  const preparing = ref(false)
  const stopping = ref(false)
  const initialized = ref(false)
  const listError = ref('')
  const messageError = ref('')
  const notice = ref('')
  const draft = ref('')
  const stage = ref('')
  const nextBeforeSeq = ref<number | null>(null)
  const latestPage = ref(true)
  const highlight = ref('')
  const matchedSeq = ref<number | null>(null)
  const restoreScrollTop = ref<number | null>(null)
  const runtime = ref<AssistantSessionContract | null>(null)
  const permissions = ref<string[]>([])
  const unresolved = ref(false)
  const changeRevision = ref(0)
  const changedConversationId = ref('')

  const owner = computed(() => auth.token && auth.profile?.staff_id && auth.profile.company_id
    ? `${auth.profile.company_id}:${auth.profile.staff_id}:${auth.token}` : '')
  const storageKey = () => `erp_assistant_last_v1_${auth.profile?.company_id}_${auth.profile?.staff_id}`
  const generatingMessage = computed(() => messages.value.find((row) => row.role === 'assistant' && !terminal(row.status)))
  const generating = computed(() => streaming.value || Boolean(generatingMessage.value))
  const canCompose = computed(() => Boolean(owner.value && auth.token === getAccessToken() && initialized.value &&
    latestPage.value && !messagesLoading.value && !creating.value && !preparing.value && !generating.value && !unresolved.value &&
    (!active.value || active.value.status === 'ACTIVE')))

  let epoch = 0
  let listRevision = 0
  let readRevision = 0
  let listController: AbortController | null = null
  let readController: AbortController | null = null
  let streamController: AbortController | null = null
  let sessionPromise: Promise<AssistantSessionContract | null> | null = null
  let attempt: { conversationId: string; payload: ConversationSend } | null = null
  let initPromise: Promise<void> | null = null
  let currentCursor: number | undefined
  const viewports = new Map<string, { top: number; before?: number }>()

  function valid(key: string, version?: number): boolean {
    return Boolean(key && key === owner.value && auth.token === getAccessToken() && (version === undefined || version === epoch))
  }
  function changed(id: string): void { changedConversationId.value = id; changeRevision.value += 1 }
  function rememberViewport(top: number): void {
    if (!active.value || messagesLoading.value || !Number.isFinite(top)) return
    viewports.set(active.value.id, { top: Math.max(0, top), before: currentCursor })
    if (viewports.size > 100) viewports.delete(viewports.keys().next().value!)
  }

  function remember(id: string | null): void {
    try {
      if (id) window.localStorage.setItem(storageKey(), id)
      else window.localStorage.removeItem(storageKey())
    } catch { /* 存储不可用时，仍能从服务器打开历史。 */ }
  }

  function clearSelection(): void {
    epoch += 1
    readRevision += 1
    readController?.abort()
    streamController?.abort()
    readController = streamController = null
    runtime.value = null
    sessionPromise = null
    attempt = null
    unresolved.value = false
    streaming.value = stopping.value = messagesLoading.value = preparing.value = false
    stage.value = ''
    messages.value = []
    active.value = null
    nextBeforeSeq.value = null
    latestPage.value = true
    highlight.value = ''
    matchedSeq.value = null
    restoreScrollTop.value = null
    currentCursor = undefined
    draft.value = ''
    messageError.value = notice.value = ''
  }

  function reset(): void {
    clearSelection()
    listRevision += 1
    listController?.abort()
    listController = null
    conversations.value = []
    total.value = 0
    listLoading.value = creating.value = false
    initialized.value = false
    query.value = ''
    status.value = 'ACTIVE'
    listError.value = ''
    permissions.value = []
    viewports.clear()
    initPromise = null
  }

  // 同一员工换令牌也清空内存；下一次只从服务端恢复历史。
  watch(() => [owner.value, auth.permissions.join('|')] as const, () => reset(), { flush: 'sync' })

  function hideCachedAnswers(): void {
    for (const row of messages.value) {
      if (row.role === 'assistant') {
        row.content = '正在重新核验此内容，请恢复服务器保存状态。'
        row.hidden = true
        row.sources = []
        row.page_context = null
      }
    }
    for (const row of conversations.value) {
      row.title = '正在核验会话'
      row.summary = ''
    }
    if (active.value) active.value.title = '正在核验会话'
  }

  function setPermissions(codes: string[]): void {
    if (permissions.value.join('|') !== codes.join('|') && initialized.value) {
      hideCachedAnswers()
      streamController?.abort()
      void refresh(true)
    }
    permissions.value = [...codes]
  }

  async function loadList(nextPage = 1): Promise<void> {
    const key = owner.value
    if (!valid(key)) return
    const revision = ++listRevision
    listController?.abort()
    const controller = new AbortController()
    listController = controller
    listLoading.value = true
    listError.value = ''
    try {
      const result = await conversationsApi.list({ page: nextPage, page_size: 20, q: query.value.trim(), status: status.value }, controller.signal)
      if (!valid(key) || revision !== listRevision) return
      conversations.value = nextPage === 1 ? result.items : [...conversations.value, ...result.items.filter((row) => !conversations.value.some((item) => item.id === row.id))]
      total.value = result.total
      page.value = nextPage
    } catch (error) {
      if (valid(key) && revision === listRevision && !controller.signal.aborted) listError.value = errorText(error)
    } finally {
      if (valid(key) && revision === listRevision) listLoading.value = false
    }
  }

  function safeMessages(rows: ConversationMessage[]): ConversationMessage[] {
    return rows.map((row) => ({ ...row,
      content: row.hidden ? '权限已变化，此内容暂不可查看' : row.content,
      sources: row.hidden ? [] : row.sources.filter((source) => sourceAllowed(source, permissions.value)),
      page_context: row.hidden ? null : row.page_context,
      attachments: row.hidden ? [] : row.attachments,
    }))
  }

  async function select(id: string, match?: { seq?: number | null; query?: string }): Promise<boolean> {
    const key = owner.value
    if (!valid(key) || !isConversationId(id)) return false
    clearSelection()
    const version = epoch
    const revision = ++readRevision
    const controller = new AbortController()
    readController = controller
    messagesLoading.value = true
    try {
      const saved = viewports.get(id)
      const cursor = match?.seq ? match.seq + 2 : match?.query ? undefined : saved?.before
      const [record, result] = await Promise.all([
        conversationsApi.get(id, controller.signal), conversationsApi.messages(id, cursor, controller.signal),
      ])
      if (!valid(key, version) || revision !== readRevision) return false
      active.value = { ...record, version: result.version }
      messages.value = safeMessages(result.items)
      nextBeforeSeq.value = result.next_before_seq
      latestPage.value = cursor === undefined
      currentCursor = cursor
      restoreScrollTop.value = match?.query ? null : saved?.top ?? null
      highlight.value = match?.query || ''
      matchedSeq.value = match?.seq || null
      remember(id)
      return true
    } catch (error) {
      if (!valid(key, version) || revision !== readRevision || controller.signal.aborted) return false
      messageError.value = errorText(error)
      if (error instanceof ApiError && [403, 404].includes(error.status || 0)) {
        conversations.value = conversations.value.filter((row) => row.id !== id)
        remember(null)
      }
      return false
    } finally {
      if (valid(key, version) && revision === readRevision) messagesLoading.value = false
    }
  }

  async function initialize(preferredId?: string): Promise<void> {
    if (initPromise) return initPromise
    const key = owner.value
    if (!valid(key)) return
    const promise = (async () => {
      await loadList()
      if (!valid(key)) return
      let saved = preferredId
      try { saved ||= window.localStorage.getItem(storageKey()) || undefined } catch { /* 无本地记忆时按最近会话恢复。 */ }
      if (isConversationId(saved)) await select(saved)
      if (!valid(key)) return
      if (!active.value && conversations.value[0]) await select(conversations.value[0].id)
      if (valid(key)) initialized.value = true
    })()
    initPromise = promise
    try { await promise } finally { if (initPromise === promise) initPromise = null }
  }

  async function refreshCurrent(revalidate = false, cursor?: number): Promise<void> {
    const key = owner.value
    const id = active.value?.id
    if (!valid(key) || !id) return
    if (streaming.value) return
    const version = epoch
    const revision = ++readRevision
    readController?.abort()
    const controller = new AbortController()
    readController = controller
    if (revalidate) hideCachedAnswers()
    messagesLoading.value = true
    messageError.value = ''
    try {
      const [record, result] = await Promise.all([conversationsApi.get(id, controller.signal), conversationsApi.messages(id, cursor, controller.signal)])
      if (!valid(key, version) || revision !== readRevision || active.value?.id !== id) return
      active.value = { ...record, version: result.version }
      messages.value = safeMessages(result.items)
      nextBeforeSeq.value = result.next_before_seq
      latestPage.value = cursor === undefined
      currentCursor = cursor
      restoreScrollTop.value = null
      if (cursor === undefined) { highlight.value = ''; matchedSeq.value = null }
      if (attempt && result.items.some((row) => row.client_message_id === attempt?.payload.client_message_id)) {
        unresolved.value = false
        if (!generatingMessage.value) attempt = null
      }
    } catch (error) {
      if (!valid(key, version) || revision !== readRevision || controller.signal.aborted) return
      messageError.value = errorText(error)
      if (error instanceof ApiError && [403, 404].includes(error.status || 0)) {
        messages.value = []
        active.value = null
        remember(null)
      }
    } finally {
      if (valid(key, version) && revision === readRevision) messagesLoading.value = false
    }
  }

  async function refresh(revalidate = false): Promise<void> {
    if (auth.token !== getAccessToken()) {
      reset()
      notice.value = '登录状态在其他页面已变化，请刷新当前页面。'
      return
    }
    if (revalidate) {
      streamController?.abort()
      // 响应仍按 epoch/身份检查；停止后先恢复服务端状态再继续。
      hideCachedAnswers()
    }
    await Promise.all([loadList(), refreshCurrent(revalidate)])
  }

  async function create(): Promise<boolean> {
    const key = owner.value
    if (!valid(key) || creating.value) return false
    clearSelection()
    const version = epoch
    creating.value = true
    try {
      const timestamp = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
      const record = await conversationsApi.create(`新会话 · ${timestamp}`)
      if (!valid(key, version)) return false
      active.value = record
      changed(record.id)
      messages.value = []
      remember(record.id)
      initialized.value = true
      query.value = ''
      status.value = 'ACTIVE'
      await loadList()
      return true
    } catch (error) {
      if (valid(key, version)) messageError.value = errorText(error)
      return false
    } finally {
      if (valid(key, version)) creating.value = false
    }
  }

  async function ensureRuntime(): Promise<AssistantSessionContract | null> {
    const key = owner.value
    const version = epoch
    if (!valid(key)) return null
    if (runtime.value && Date.parse(runtime.value.expires_at) > Date.now()) return runtime.value
    if (sessionPromise) return sessionPromise
    const promise = assistantApi.createSession().then((created) => {
      if (!valid(key, version)) return null
      runtime.value = created
      return created
    })
    sessionPromise = promise
    try { return await promise } catch (error) {
      if (valid(key, version)) notice.value = errorText(error)
      return null
    } finally { if (sessionPromise === promise) sessionPromise = null }
  }

  async function mutate(record: Conversation, changes: Partial<Pick<Conversation, 'title' | 'is_pinned' | 'status'>> | 'delete' | 'clear'): Promise<boolean> {
    const key = owner.value
    if (!valid(key) || (record.id === active.value?.id && generating.value)) {
      notice.value = '请先停止当前回答，再修改会话。'
      return false
    }
    const current = active.value?.id === record.id ? active.value : record
    try {
      if (changes === 'delete') await conversationsApi.remove(record.id, current.version)
      else if (changes === 'clear') await conversationsApi.clear(record.id, current.version)
      else await conversationsApi.update(record.id, current.version, changes)
      if (!valid(key)) return false
      changed(record.id)
      if (changes === 'delete' || changes === 'clear') viewports.delete(record.id)
      if (changes === 'delete' || (typeof changes === 'object' && changes.status === 'ARCHIVED')) {
        if (active.value?.id === record.id) { clearSelection(); remember(null) }
      } else if (active.value?.id === record.id) {
        runtime.value = null
        await refreshCurrent()
      }
      await loadList()
      return true
    } catch (error) {
      if (valid(key)) {
        if (error instanceof ApiError && error.status === 409) {
          await refresh(true)
          notice.value = '会话在其他页面已更新，已同步服务器状态。请核对后再操作。'
        } else notice.value = errorText(error)
      }
      return false
    }
  }

  function placeholder(role: 'user' | 'assistant', payload: ConversationSend): ConversationMessage {
    return reactive({ id: `pending-${role}-${payload.client_message_id}`, seq: (active.value?.latest_message_seq || 0) + (role === 'user' ? 1 : 2),
      role, content: role === 'user' ? payload.content : '', status: 'PENDING', hidden: false,
      client_message_id: role === 'user' ? payload.client_message_id : null, request_id: payload.request_id,
      sources: [], page_context: role === 'assistant' ? payload.page_context || null : null, error_code: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString() })
  }

  async function submit(payload: ConversationSend): Promise<void> {
    const key = owner.value
    const id = active.value?.id
    if (!valid(key) || !id) return
    const version = epoch
    const controller = new AbortController()
    streamController = controller
    streaming.value = true
    notice.value = ''
    messageError.value = ''
    stage.value = '正在连接 AI 服务…'
    let accepted = false
    let ended = false
    const user = messages.value.find((row) => row.client_message_id === payload.client_message_id) || placeholder('user', payload)
    const answer = messages.value.find((row) => row.role === 'assistant' && row.request_id === payload.request_id) || placeholder('assistant', payload)
    if (!messages.value.includes(user)) messages.value.push(user)
    if (!messages.value.includes(answer)) messages.value.push(answer)
    const current = () => valid(key, version) && active.value?.id === id && streamController === controller
    const publish = () => current() && !controller.signal.aborted
    attempt = { conversationId: id, payload }
    unresolved.value = true
    try {
      await conversationsApi.stream(id, payload, {
        onAccepted: (data) => {
          if (!publish()) return
          accepted = true
          unresolved.value = false
          user.id = data.user_message_id
          user.seq = data.seq
          if (data.assistant_message_id) answer.id = data.assistant_message_id
          answer.seq = data.seq + 1
          active.value!.version = data.conversation_version
          active.value!.latest_message_seq = Math.max(active.value!.latest_message_seq, answer.seq)
          draft.value = ''
        },
        onSnapshot: (message, serverVersion) => {
          if (!publish()) return
          Object.assign(answer, safeMessages([message])[0])
          active.value!.version = serverVersion
        },
        onStatus: (value) => {
          if (publish()) stage.value = ({ thinking: '正在思考…', querying: '正在查询授权信息…', composing: '正在组织回答…' } as Record<string, string>)[value] || '正在生成回答…'
        },
        onSource: (items) => { if (publish() && !answer.hidden) answer.sources.push(...items.filter((source) => sourceAllowed(source, permissions.value))) },
        onDelta: (text) => { if (publish() && !answer.hidden) { answer.content += text; answer.status = 'STREAMING' } },
        onDone: (data) => {
          if (!publish()) return
          ended = true
          answer.status = data.status
          answer.id = data.assistant_message_id
          answer.seq = data.seq
          if (terminal(data.status)) { user.status = 'COMPLETED'; attempt = null }
          if (data.conversation_version) active.value!.version = data.conversation_version
        },
        onError: (data) => {
          if (!publish()) return
          ended = true
          answer.status = data.status || 'FAILED'
          answer.error_code = data.code
          if (data.conversation_version) active.value!.version = data.conversation_version
        },
      }, controller)
      if (current() && ended) notice.value = ''
    } catch (error) {
      if (key === owner.value && version === epoch && auth.token !== getAccessToken()) { reset(); return }
      if (!valid(key, version) || active.value?.id !== id) return
      if (error instanceof ApiError && ['40901', '40902', '40903'].includes(error.code)) runtime.value = null
      if (error instanceof ApiError && ['40100', '40300'].includes(error.code)) hideCachedAnswers()
      if (error instanceof ApiError && error.status === 409) {
        hideCachedAnswers()
        notice.value = '会话版本已变化，请恢复服务器保存状态后再操作。'
      } else notice.value = controller.signal.aborted ? '生成已停止，正在核对服务器保存状态。' : `${errorText(error)}。请恢复保存状态后再决定是否重试。`
      if (!ended) answer.status = 'INTERRUPTED'
      // 未收到 accepted 时仍可能已经保存，保留原幂等键，只允许核对后重试。
      unresolved.value = !accepted
    } finally {
      if (current()) {
        streamController = null
        streaming.value = stopping.value = false
        stage.value = ''
        await refreshCurrent()
        if (valid(key, version)) { changed(id); await loadList() }
      }
    }
  }

  async function send(content: string, context: AssistantPageContext): Promise<void> {
    if (!canCompose.value || !content.trim() || content.length > 4000) return
    const key = owner.value
    let operationEpoch = epoch
    preparing.value = true
    try {
      if (!active.value && !await create()) { if (valid(key)) draft.value = content; return }
      if (!valid(key) || !active.value) return
      preparing.value = true
      const version = epoch
      operationEpoch = version
      const session = await ensureRuntime()
      if (!session || !valid(key, version) || !active.value) return
      await submit({ version: active.value.version, request_id: crypto.randomUUID(), client_message_id: crypto.randomUUID(),
        session_id: session.session_id, session_version: session.version, content: content.trim(), page_context: context })
    } catch (error) { if (valid(key, operationEpoch)) notice.value = errorText(error) }
    finally { if (key === owner.value && operationEpoch === epoch) preparing.value = false }
  }

  async function retryUnresolved(): Promise<void> {
    const key = owner.value
    const pending = attempt
    if (!pending || !valid(key) || active.value?.id !== pending.conversationId || streaming.value || preparing.value) return
    const version = epoch
    preparing.value = true
    try {
      await refreshCurrent()
      if (!valid(key, version) || active.value?.id !== pending.conversationId) return
      if (messages.value.some((row) => row.client_message_id === pending.payload.client_message_id)) {
        unresolved.value = false
        notice.value = generating.value ? '服务器仍有生成中的记录，可等待、刷新或停止。' : '已恢复这条问题的保存状态；需要再次提问时请手动重试问题。'
        return
      }
      if (messageError.value) return
      const session = await ensureRuntime()
      if (session && valid(key, version)) await submit({ ...pending.payload, version: active.value.version, session_id: session.session_id, session_version: session.version })
    } catch (error) { if (valid(key, version)) notice.value = errorText(error) }
    finally { if (key === owner.value && version === epoch) preparing.value = false }
  }

  async function stop(): Promise<void> {
    const key = owner.value
    const version = epoch
    const id = active.value?.id
    const message = generatingMessage.value
    if (!valid(key) || !id || stopping.value) return
    stopping.value = true
    const controller = streamController
    try {
      if (message && isConversationId(message.id)) await conversationsApi.cancel(id, message.id)
      if (!valid(key, version)) return
      controller?.abort()
      if (!controller) await refreshCurrent()
    } catch (error) {
      if (valid(key, version)) notice.value = `停止状态尚未确认：${errorText(error)}。请恢复保存状态。`
      controller?.abort()
    } finally { if (valid(key, version)) stopping.value = false }
  }

  function disposeView(): void {
    clearSelection()
    creating.value = false
    initialized.value = false
  }

  async function attachmentsSaved(id: string, version: number): Promise<void> {
    if (!active.value || active.value.id !== id) return
    active.value.version = version
    changed(id)
    await refresh(true)
  }

  return { owner, conversations, active, messages, query, status, page, total, listLoading, messagesLoading,
    creating, streaming, preparing, stopping, initialized, listError, messageError, notice, draft, stage, nextBeforeSeq,
    latestPage, highlight, matchedSeq, restoreScrollTop, runtime, unresolved, changeRevision, changedConversationId, generatingMessage, generating, canCompose,
    initialize, loadList, select, create, mutate, refresh, refreshCurrent, send, retryUnresolved, stop,
    ensureRuntime, setPermissions, hideCachedAnswers, rememberViewport, attachmentsSaved, reset, disposeView }
})
