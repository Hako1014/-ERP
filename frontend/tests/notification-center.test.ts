import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { notificationApi, type NotificationItem } from '@/api/notification'
import NotificationCenterView from '@/views/notifications/NotificationCenterView.vue'
import { useAuthStore } from '@/stores/auth'

const message: NotificationItem = {
  id: 55,
  event_code: 'approval.task_assigned',
  category: 'approval',
  title: '待我审批采购计划',
  body: '采购计划已提交，请处理。',
  target: { type: 'purchase_plan', id: 9 },
  read_state: 'UNREAD',
  read_at: null,
  business_state: 'OPEN',
  created_at: '2026-10-10T02:00:00Z',
  cursor: 'cursor-55',
}

let wrapper: VueWrapper | null = null

async function mountCenter() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore(pinia).setLoginSession({
    token: 'center-test-token',
    staff_id: 55,
    name: '测试员工',
    roles: ['PURCHASER'],
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/notifications', name: 'notifications', component: NotificationCenterView },
      { path: '/approval/mine', name: 'approval-mine', component: { template: '<div />' } },
      { path: '/purchase/orders', name: 'purchase-orders', component: { template: '<div />' } },
    ],
  })
  await router.push('/notifications')
  await router.isReady()
  const view = mount(NotificationCenterView, {
    global: { plugins: [pinia, router, ElementPlus] },
    attachTo: document.body,
  })
  wrapper = view
  await flushPromises()
  return { view, router }
}

describe('notification center', () => {
  beforeEach(() => {
    window.localStorage.clear()
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
    vi.spyOn(notificationApi, 'list').mockResolvedValue({
      items: [message],
      page_size: 50,
      next_cursor: null,
    })
    vi.spyOn(notificationApi, 'getUnreadCount').mockResolvedValue({ count: 1 })
    vi.spyOn(notificationApi, 'getDetail').mockResolvedValue(message)
    vi.spyOn(notificationApi, 'markRead').mockResolvedValue({
      id: message.id,
      read_state: 'READ',
      read_at: '2026-10-10T03:00:00Z',
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.restoreAllMocks()
    window.localStorage.clear()
    document.body.innerHTML = ''
  })

  it('loads persistent messages and requires an explicit read action', async () => {
    const { view } = await mountCenter()
    expect(view.text()).toContain(message.title)
    expect(view.text()).toContain('审批')

    await view.get('.notification-row').trigger('click')
    await flushPromises()
    expect(notificationApi.getDetail).toHaveBeenCalledWith(message.id)
    expect(notificationApi.markRead).not.toHaveBeenCalled()

    const markReadButton = [...document.body.querySelectorAll('button')].find((button) =>
      button.textContent?.includes('标记已读'),
    )
    expect(markReadButton).toBeDefined()
    markReadButton?.click()
    await flushPromises()
    expect(notificationApi.markRead).toHaveBeenCalledWith(message.id)
  })

  it('rechecks message access before opening its allowlisted target route', async () => {
    const { view, router } = await mountCenter()
    const routeSpy = vi.spyOn(router, 'push').mockResolvedValue(undefined)
    await view.get('.notification-row').trigger('click')
    await flushPromises()

    const openButton = [...document.body.querySelectorAll('button')].find((button) =>
      button.textContent?.includes('打开单据 / 待办'),
    )
    expect(openButton).toBeDefined()
    openButton?.click()
    await flushPromises()

    expect(notificationApi.getDetail).toHaveBeenCalledWith(message.id)
    expect(routeSpy).toHaveBeenCalledWith({
      name: 'approval-mine',
      query: { notificationDocType: 'purchase_plan', notificationDocId: '9' },
    })
  })
})
