import { nextTick } from 'vue'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ACCESS_TOKEN_KEY, ApiError } from '@/api/client'
import { assistantApi, type AssistantCapabilities } from '@/api/assistant'
import AssistantFloat from '@/layouts/components/AssistantFloat.vue'
import AssistantConversationPanel from '@/layouts/components/AssistantConversationPanel.vue'
import { assistantConversationsApi } from '@/api/assistantConversations'
enableAutoUnmount(afterEach)
import {
  buildSuggestions,
  clampPosition,
  clearAssistantSelectedDocument,
  defaultPosition,
  getAssistantSelectedDocument,
  isCompactViewport,
  panelSize,
  positionStorageKey,
  readStoredPosition,
  resolvePageLabel,
  resolveRoleLabel,
  setAssistantSelectedDocument,
  writeStoredPosition,
} from '@/layouts/components/assistantShell'
import { useAuthStore } from '@/stores/auth'

// 浮窗只依赖 route.path 与 menuTree，不需要加载真实页面组件。
const TEST_ROUTES = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'dashboard', component: { template: '<div />' }, meta: { title: '首页看板', permission: 'module:dashboard' } },
  { path: '/assistant', name: 'assistant', component: { template: '<div />' }, meta: { title: 'AI 助手', permission: 'module:dashboard' } },
  { path: '/purchase/plans', name: 'purchase-plans', component: { template: '<div />' }, meta: { title: '采购计划', permission: 'module:purchase' } },
]

const VIEWPORT = { width: 1024, height: 768 }

function capabilities(overrides: Partial<AssistantCapabilities> = {}): AssistantCapabilities {
  return {
    staff_id: 1,
    service_status: 'ready',
    provider_mode: 'deepseek',
    roles: ['PURCHASER'],
    permissions: ['module:dashboard', 'module:purchase', 'purchase:create'],
    modules: ['dashboard', 'purchase'],
    features: [
      { code: 'guide', enabled: true },
      { code: 'read_query', enabled: true },
      { code: 'draft_entry', enabled: false, reason: 'planned_v2' },
    ],
    ...overrides,
  }
}

async function mountFloat(
  options: {
    staffId?: number
    roles?: string[]
    permissions?: string[]
    payload?: AssistantCapabilities | null
    fail?: boolean
  } = {},
) {
  const {
    staffId = 1,
    roles = ['PURCHASER'],
    permissions = ['module:dashboard', 'module:purchase', 'purchase:create'],
    payload = capabilities(),
    fail = false,
  } = options

  vi.spyOn(assistantApi, 'createSession').mockResolvedValue({
    session_id: `00000000-0000-4000-8000-${staffId.toString().padStart(12, '0')}`,
    version: 1,
    expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  })
  vi.spyOn(assistantApi, 'clearSession').mockImplementation(
    async (sessionId, version) => ({
      session_id: sessionId,
      version: version + 1,
      expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    }),
  )

  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.setLoginSession({ token: 'test-token', staff_id: staffId, name: '张三', roles })
  authStore.setPermissionSnapshot({
    staff_id: staffId,
    company_id: 1,
    company_name: '测试公司',
    roles,
    permissions,
    modules: permissions
      .filter((item) => item.startsWith('module:'))
      .map((item) => item.replace('module:', '')),
  })

  if (fail) {
    vi.spyOn(assistantApi, 'getCapabilities').mockRejectedValue(new Error('boom'))
  } else {
    vi.spyOn(assistantApi, 'getCapabilities').mockResolvedValue(payload as AssistantCapabilities)
  }

  const router = createRouter({ history: createMemoryHistory(), routes: TEST_ROUTES })
  await router.push('/dashboard')
  await router.isReady()

  const wrapper = mount(AssistantFloat, {
    global: { plugins: [pinia, router, ElementPlus] },
  })
  await flush()
  return { wrapper, authStore, router }
}

async function flush(): Promise<void> {
  await flushPromises()
  await Promise.resolve()
}

function dragTo(x: number, y: number): void {
  window.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y }))
}

function mouseDown(element: Element, x: number, y: number): void {
  element.dispatchEvent(
    new MouseEvent('mousedown', { clientX: x, clientY: y, bubbles: true }),
  )
}

function mouseUp(): void {
  window.dispatchEvent(new MouseEvent('mouseup'))
}

beforeEach(() => {
  vi.stubGlobal('BroadcastChannel', undefined)
  vi.spyOn(assistantConversationsApi, 'list').mockResolvedValue({ page: 1, page_size: 20, total: 0, items: [] })
  vi.spyOn(assistantConversationsApi, 'create').mockResolvedValue({
    id: '00000000-0000-4000-8000-000000000001', title: '新会话', summary: '', is_pinned: false,
    status: 'ACTIVE', version: 1, latest_message_seq: 0, message_count: 0,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(), retention_days: null,
  })
  vi.spyOn(assistantConversationsApi, 'messages').mockResolvedValue({ items: [], next_before_seq: null, version: 1 })
  window.localStorage.clear()
  window.innerWidth = VIEWPORT.width
  window.innerHeight = VIEWPORT.height
})

afterEach(() => {
  clearAssistantSelectedDocument()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('assistant shell geometry', () => {
  it('keeps the panel inside the viewport', () => {
    const size = panelSize(VIEWPORT.width, VIEWPORT.height)
    expect(clampPosition({ x: 5000, y: 5000 }, size, VIEWPORT.width, VIEWPORT.height)).toEqual({
      x: VIEWPORT.width - size.width,
      y: VIEWPORT.height - size.height,
    })
    expect(clampPosition({ x: -80, y: -80 }, size, VIEWPORT.width, VIEWPORT.height)).toEqual({
      x: 0,
      y: 0,
    })
  })

  it('falls back to the top-left when the panel is larger than the viewport', () => {
    const size = panelSize(320, 300)
    expect(clampPosition({ x: 90, y: 90 }, size, 200, 150)).toEqual({ x: 0, y: 0 })
  })

  it('defaults to the bottom-right corner with a margin', () => {
    const size = panelSize(VIEWPORT.width, VIEWPORT.height)
    expect(defaultPosition(size, VIEWPORT.width, VIEWPORT.height)).toEqual({
      x: VIEWPORT.width - size.width - 16,
      y: VIEWPORT.height - size.height - 16,
    })
  })

  it('shrinks the panel on narrow viewports and switches to the compact layout', () => {
    expect(isCompactViewport(VIEWPORT.width)).toBe(false)
    expect(isCompactViewport(480)).toBe(true)
    expect(panelSize(360, 640).width).toBe(328)
  })

  it('stores the position per user and ignores malformed values', () => {
    writeStoredPosition(7, { x: 40, y: 60 })
    expect(positionStorageKey(7)).toBe('erp_assistant_position_7')
    expect(readStoredPosition(7)).toEqual({ x: 40, y: 60 })
    expect(readStoredPosition(8)).toBeNull()

    window.localStorage.setItem(positionStorageKey(7), '{"x":"nope"}')
    expect(readStoredPosition(7)).toBeNull()
    window.localStorage.setItem(positionStorageKey(7), 'not-json')
    expect(readStoredPosition(7)).toBeNull()
  })

  it('maps routes to menu titles and roles to Chinese labels', () => {
    expect(resolvePageLabel('/purchase/plans')).toBe('采购计划')
    expect(resolvePageLabel('/unknown-page')).toBe('/unknown-page')
    expect(resolveRoleLabel(['PURCHASER'])).toBe('采购')
    expect(resolveRoleLabel(['VP', 'FINANCE'])).toBe('副总 / 财务')
    expect(resolveRoleLabel([])).toBe('当前账号')
  })
})

describe('assistant suggestions', () => {
  it('only offers modules the account can see', () => {
    const labels = buildSuggestions(
      ['dashboard'],
      ['module:dashboard'],
    ).map((item) => item.label)

    expect(labels).toContain('本页怎么操作')
    expect(labels).toContain('首页看板')
    expect(labels).not.toContain('采购计划')
    expect(labels).not.toContain('财务管理')
  })

  it('filters action recommendations by both module and action permission', () => {
    const purchaser = buildSuggestions(
      ['purchase', 'finance'],
      ['module:purchase', 'purchase:create', 'module:finance'],
      { currentModule: 'purchase' },
    )
    expect(purchaser.some((item) => item.code === 'action.purchase.create')).toBe(true)
    expect(purchaser.some((item) => item.code === 'action.finance.manage')).toBe(false)

    const withoutModule = buildSuggestions(
      ['purchase'],
      ['purchase:create'],
      { currentModule: 'purchase' },
    )
    expect(withoutModule.some((item) => item.code === 'action.purchase.create')).toBe(false)
  })

  it('hides the query entry until the read-only capability is enabled', () => {
    const withoutQuery = buildSuggestions(['dashboard'], ['module:dashboard'])
    expect(withoutQuery.some((item) => item.code === 'query.current_page')).toBe(false)

    const withQuery = buildSuggestions(['dashboard'], ['module:dashboard'], {
      readQueryEnabled: true,
    })
    expect(withQuery.some((item) => item.code === 'query.current_page')).toBe(true)
  })
})

describe('assistant float', () => {
  it('renders nothing when the account is not authenticated', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const authStore = useAuthStore()
    authStore.clearSession()
    window.localStorage.removeItem(ACCESS_TOKEN_KEY)

    const router = createRouter({ history: createMemoryHistory(), routes: TEST_ROUTES })
    await router.push('/dashboard')
    await router.isReady()

    const wrapper = mount(AssistantFloat, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.find('.assistant-float__launcher').exists()).toBe(false)
    expect(wrapper.find('.assistant-float__panel').exists()).toBe(false)
  })

  it('does not render on routes without the current module permission', async () => {
    const { wrapper } = await mountFloat({
      permissions: ['module:order', 'after_sale:manage'],
      payload: capabilities({
        modules: ['order'],
        permissions: ['module:order', 'after_sale:manage'],
      }),
    })

    expect(wrapper.find('.assistant-float__launcher').exists()).toBe(false)
  })

  it('hides the assistant when the server capability no longer grants the current module', async () => {
    const { wrapper } = await mountFloat({
      permissions: ['module:dashboard'],
      payload: capabilities({
        modules: ['order'],
        permissions: ['module:order', 'after_sale:manage'],
      }),
    })

    expect(wrapper.find('.assistant-float__launcher').exists()).toBe(false)
  })

  it('opens from the launcher and shows the role and page scene', async () => {
    const { wrapper } = await mountFloat()

    expect(wrapper.find('.assistant-float__panel').exists()).toBe(false)
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    expect(wrapper.find('.assistant-float__panel').exists()).toBe(true)
    expect(wrapper.find('.assistant-float__scene').text()).toBe('采购 · 首页看板')
  })

  it('opens as the workspace and keeps a permitted source page as the scene context', async () => {
    const { wrapper, router } = await mountFloat()
    await router.push({ path: '/assistant', query: { from: '/purchase/plans' } })

    expect(wrapper.find('.assistant-float.is-workspace').exists()).toBe(true)
    expect(wrapper.find('.assistant-float__launcher').exists()).toBe(false)
    expect(wrapper.find('.assistant-float__scene').text()).toBe('采购 · 采购计划')
  })

  it('同一路由改变所选单据时更新工作台场景，未授权场景不带旧单据', async () => {
    const { wrapper, router } = await mountFloat()
    await router.push({ path: '/assistant', query: { from: '/purchase/orders' } }); await flush()
    setAssistantSelectedDocument(1, { doc_type: 'purchase_order', doc_id: 101 }); await nextTick()
    expect(wrapper.findComponent(AssistantConversationPanel).props('pageContext').selected_document?.doc_id).toBe(101)
    setAssistantSelectedDocument(1, { doc_type: 'purchase_order', doc_id: 102 }); await nextTick()
    expect(wrapper.findComponent(AssistantConversationPanel).props('pageContext').selected_document?.doc_id).toBe(102)
    await router.push({ path: '/assistant', query: { from: 'https://example.invalid/' } }); await flush()
    expect(wrapper.findComponent(AssistantConversationPanel).props('pageContext').selected_document).toBeUndefined()
  })

  it('uses the role returned by the server rather than the cached role label', async () => {
    const { wrapper } = await mountFloat({
      roles: ['ADMIN'],
      payload: capabilities({ roles: ['AFTER_SALE'] }),
    })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    expect(wrapper.find('.assistant-float__scene').text()).toBe('售后 · 首页看板')
  })

  it('tells the user when the assistant service is not configured and cannot send', async () => {
    const { wrapper } = await mountFloat({
      payload: capabilities({ service_status: 'unconfigured' }),
    })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    expect(wrapper.find('.assistant-float__notice').text()).toContain('AI 服务未配置')
    expect(wrapper.find('.assistant-float__send').attributes('disabled')).toBeDefined()
  })

  it('reports a capability load failure instead of faking an answer', async () => {
    const { wrapper } = await mountFloat({ fail: true })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    expect(wrapper.find('.assistant-float__notice').text()).toContain('助手能力加载失败')
    expect(wrapper.text()).not.toContain('AI 服务未配置')
  })

  it('never shows the draft entry in the first version', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    expect(wrapper.find('.assistant-float__draft-entry').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('快捷录单')
  })

  it('shows only the server-authorized purchase-plan quick-entry launcher', async () => {
    const payload = capabilities({
      features: [
        { code: 'guide', enabled: true },
        { code: 'read_query', enabled: true },
        { code: 'draft_entry', enabled: true, document_types: ['purchase_plan'] },
      ],
    })
    vi.spyOn(assistantApi, 'getCurrentDraft').mockResolvedValue(null)
    const { wrapper } = await mountFloat({ payload })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()
    expect(wrapper.find('.assistant-draft-entry__launch').exists()).toBe(true)
    await wrapper.find('.assistant-draft-entry__launch').trigger('click')
    await flush()
    expect(document.body.textContent).toContain('AI 快捷录入 · 采购计划')
    expect(document.body.textContent).toContain('不会创建采购计划')
  })

  it('keeps provider parse failures visible and retryable in the draft dialog', async () => {
    vi.spyOn(assistantApi, 'getCurrentDraft').mockResolvedValue(null)
    vi.spyOn(assistantApi, 'parseDraft').mockRejectedValue(
      new ApiError('DeepSeek 暂时不可用，请稍后重试', 'AI_PROVIDER_ERROR', 502),
    )
    const payload = capabilities({
      features: [
        { code: 'guide', enabled: true },
        { code: 'read_query', enabled: true },
        { code: 'draft_entry', enabled: true, document_types: ['purchase_plan'] },
      ],
    })
    const { wrapper } = await mountFloat({ payload })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()
    await wrapper.find('.assistant-float__new-session').trigger('click')
    await flush()
    await wrapper.find('.assistant-draft-entry__launch').trigger('click')
    await flush()

    const dialogs = document.body.querySelectorAll('.assistant-draft-entry__dialog')
    const dialog = dialogs[dialogs.length - 1] as HTMLElement
    const textarea = dialog.querySelector(
      'textarea[aria-label="采购计划原始内容"]',
    ) as HTMLTextAreaElement
    textarea.value = '采购计划，采购示例商品 2 箱'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    const parseButton = [...dialog.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('解析为临时草稿'))
    expect(parseButton).toBeDefined()
    expect(parseButton?.disabled).toBe(false)
    parseButton?.click()
    await flush()

    expect(assistantApi.parseDraft).toHaveBeenCalledTimes(1)
    expect(dialog.textContent).toContain('DeepSeek 暂时不可用，请稍后重试')
    expect(parseButton?.disabled).toBe(false)
  })

  it('filters recommended entries by the account permissions', async () => {
    const { wrapper } = await mountFloat({
      payload: capabilities({ modules: ['dashboard'] }),
      permissions: ['module:dashboard'],
    })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    const labels = wrapper.findAll('.assistant-float__suggestion').map((item) => item.text())
    expect(labels).toContain('首页看板')
    expect(labels).not.toContain('采购计划')
  })

  it('drags the panel and keeps it inside the viewport', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    const size = panelSize(VIEWPORT.width, VIEWPORT.height)
    const panel = wrapper.find('.assistant-float__panel')
    expect(panel.attributes('style')).toContain(
      `left: ${defaultPosition(size, VIEWPORT.width, VIEWPORT.height).x}px`,
    )

    mouseDown(wrapper.find('.assistant-float__header').element, 100, 100)
    dragTo(300, 300)
    mouseUp()
    await nextTick()

    // 拖到视口外，应被钳制在边界内。
    const style = wrapper.find('.assistant-float__panel').attributes('style') ?? ''
    expect(style).toContain(`left: ${VIEWPORT.width - size.width}px`)
    expect(style).toContain(`top: ${VIEWPORT.height - size.height}px`)
    expect(readStoredPosition(1)).toEqual({
      x: VIEWPORT.width - size.width,
      y: VIEWPORT.height - size.height,
    })
  })

  it('does not start dragging from the header buttons', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    const before = wrapper.find('.assistant-float__panel').attributes('style')
    mouseDown(wrapper.find('[aria-label="收起助手"]').element, 10, 10)
    dragTo(400, 400)
    mouseUp()
    await nextTick()

    // 收起按钮的 mousedown 不进入拖动：面板未移动，也没有写入位置。
    expect(wrapper.find('.assistant-float__panel').attributes('style')).toBe(before)
    expect(readStoredPosition(1)).toBeNull()
  })

  it('restores the default position on demand', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    mouseDown(wrapper.find('.assistant-float__header').element, 200, 200)
    dragTo(400, 400)
    mouseUp()
    await nextTick()

    await wrapper.find('[aria-label="恢复默认位置"]').trigger('click')

    const size = panelSize(VIEWPORT.width, VIEWPORT.height)
    const expected = defaultPosition(size, VIEWPORT.width, VIEWPORT.height)
    expect(wrapper.find('.assistant-float__panel').attributes('style')).toContain(
      `left: ${expected.x}px`,
    )
    expect(readStoredPosition(1)).toEqual(expected)
  })

  it('keeps each account position separate after switching user', async () => {
    writeStoredPosition(1, { x: 40, y: 60 })
    const { wrapper, authStore } = await mountFloat({ staffId: 1 })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()
    expect(wrapper.find('.assistant-float__panel').attributes('style')).toContain('left: 40px')

    authStore.setPermissionSnapshot({
      staff_id: 2,
      roles: ['PURCHASER'],
      permissions: ['module:dashboard', 'module:purchase'],
      modules: ['dashboard', 'purchase'],
    })
    await flush()

    // 切换账号后浮窗收起，重新打开时使用新用户自己的默认位置。
    expect(wrapper.find('.assistant-float__panel').exists()).toBe(false)
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    const size = panelSize(VIEWPORT.width, VIEWPORT.height)
    const expected = defaultPosition(size, VIEWPORT.width, VIEWPORT.height)
    expect(wrapper.find('.assistant-float__panel').attributes('style')).toContain(
      `left: ${expected.x}px`,
    )
    expect(readStoredPosition(1)).toEqual({ x: 40, y: 60 })
  })

  it('ignores a late capability response from the previous account', async () => {
    let resolveOld!: (value: AssistantCapabilities) => void
    vi.spyOn(assistantApi, 'getCapabilities')
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce(capabilities({
        staff_id: 2,
        roles: ['AFTER_SALE'],
        modules: ['dashboard', 'order'],
        permissions: ['module:dashboard', 'module:order', 'after_sale:manage'],
      }))

    const { wrapper, authStore } = await mountFloat({ staffId: 1 })
    authStore.setLoginSession({ token: 'next-token', staff_id: 2, name: '李四', roles: ['AFTER_SALE'] })
    authStore.setPermissionSnapshot({
      staff_id: 2,
      roles: ['AFTER_SALE'],
      permissions: ['module:dashboard', 'module:order', 'after_sale:manage'],
      modules: ['dashboard', 'order'],
    })
    await flush()

    resolveOld(capabilities())
    await flush()
    expect(wrapper.find('.assistant-float__launcher').exists()).toBe(true)
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()
    expect(wrapper.find('.assistant-float__scene').text()).toBe('售后 · 首页看板')
  })

  it('supports keyboard position adjustment and preserves Shift+Enter/IME input', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()
    const header = wrapper.find('.assistant-float__header')
    const startX = Number(
      wrapper.find('.assistant-float__panel').element.getAttribute('style')?.match(/left: (\d+)px/)?.[1],
    )

    await header.trigger('keydown', { key: 'ArrowLeft' })
    const movedX = Number(
      wrapper.find('.assistant-float__panel').element.getAttribute('style')?.match(/left: (\d+)px/)?.[1],
    )
    expect(movedX).toBe(startX - 16)

    const input = wrapper.find('.assistant-float__input').element
    const shiftEnter = new KeyboardEvent('keydown', {
      key: 'Enter', shiftKey: true, bubbles: true, cancelable: true,
    })
    input.dispatchEvent(shiftEnter)
    expect(shiftEnter.defaultPrevented).toBe(false)

    const composingEnter = new KeyboardEvent('keydown', {
      key: 'Enter', bubbles: true, cancelable: true,
    })
    Object.defineProperty(composingEnter, 'isComposing', { value: true })
    input.dispatchEvent(composingEnter)
    expect(composingEnter.defaultPrevented).toBe(false)
  })

  it('supports touch dragging and clamps the panel to the viewport', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()
    const header = wrapper.find('.assistant-float__header').element

    const makeTouchEvent = (type: string, x: number, y: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true })
      const touch = { clientX: x, clientY: y }
      Object.defineProperties(event, {
        touches: { value: type === 'touchend' ? [] : [touch] },
        changedTouches: { value: [touch] },
      })
      return event
    }

    header.dispatchEvent(makeTouchEvent('touchstart', 100, 100))
    window.dispatchEvent(makeTouchEvent('touchmove', 5000, 5000))
    window.dispatchEvent(makeTouchEvent('touchend', 5000, 5000))
    await nextTick()

    const size = panelSize(VIEWPORT.width, VIEWPORT.height)
    expect(readStoredPosition(1)).toEqual({
      x: VIEWPORT.width - size.width,
      y: VIEWPORT.height - size.height,
    })
  })

  it('uses the bottom panel layout on small screens', async () => {
    window.innerWidth = 480
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    expect(wrapper.find('.assistant-float__panel').classes()).toContain('is-compact')
    expect(wrapper.find('.assistant-float__panel').attributes('style')).toBeUndefined()
  })

  it('reclamps and persists the position after the viewport changes', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()
    mouseDown(wrapper.find('.assistant-float__header').element, 100, 100)
    dragTo(5000, 5000)
    mouseUp()

    window.innerWidth = 800
    window.innerHeight = 600
    window.dispatchEvent(new Event('resize'))
    await nextTick()

    const resizedSize = panelSize(800, 600)
    const expected = clampPosition(
      { x: 5000, y: 5000 },
      resizedSize,
      800,
      600,
    )
    expect(wrapper.find('.assistant-float__panel').attributes('style')).toContain(
      `left: ${expected.x}px`,
    )
    expect(readStoredPosition(1)).toEqual(expected)
  })

  it('labels explicit mock mode and still makes clear that replies are simulated', async () => {
    const { wrapper } = await mountFloat({
      payload: capabilities({ provider_mode: 'mock' }),
    })
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    expect(wrapper.find('.assistant-float__notice').text()).toContain('本地模拟模式')
    expect(wrapper.find('.assistant-float__notice').text()).toContain('不是 DeepSeek 内容')
  })

  it('fills the composer from a recommended entry', async () => {
    const { wrapper } = await mountFloat()
    await wrapper.find('.assistant-float__launcher').trigger('click')
    await flush()

    await wrapper.find('.assistant-float__suggestion').trigger('click')
    expect((wrapper.find('.assistant-float__input').element as HTMLTextAreaElement).value).toBe(
      '这个页面怎么操作？',
    )
  })
})
