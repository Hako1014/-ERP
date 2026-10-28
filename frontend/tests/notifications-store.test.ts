import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  notificationApi,
  type NotificationItem,
  type NotificationPage,
  type NotificationStreamHandlers,
} from '@/api/notification'
import { setAccessToken } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useNotificationStore } from '@/stores/notifications'

const item: NotificationItem = {
  id: 7001,
  event_code: 'approval.task_assigned',
  category: 'approval',
  title: '待审批单据',
  body: '这段正文不应写入本地存储',
  target: { type: 'purchase_plan', id: 81 },
  read_state: 'UNREAD',
  read_at: null,
  business_state: 'OPEN',
  created_at: new Date().toISOString(),
  cursor: 'signed-cursor',
}

let stopCallbacks: Array<() => void> = []

function page(items: NotificationItem[]): NotificationPage {
  return { items, page_size: 6, next_cursor: null }
}

function installStreamMock(): {
  handlers: NotificationStreamHandlers[]
  streamCallCount: () => number
} {
  const handlers: NotificationStreamHandlers[] = []
  const streamSpy = vi
    .spyOn(notificationApi, 'stream')
    .mockImplementation((nextHandlers, options) => {
      handlers.push(nextHandlers)
      return new Promise<void>((resolve) => {
        if (options?.signal?.aborted) resolve()
        else options?.signal?.addEventListener('abort', () => resolve(), { once: true })
      })
    })
  return { handlers, streamCallCount: () => streamSpy.mock.calls.length }
}

describe('notification session state', () => {
  beforeEach(() => {
    window.localStorage.clear()
    setActivePinia(createPinia())
    stopCallbacks = []
  })

  afterEach(() => {
    for (const stop of stopCallbacks) stop()
    useNotificationStore().stop()
    useAuthStore().clearSession()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('ignores a slow response from the previous signed-in account', async () => {
    let resolveFirstPage!: (value: NotificationPage) => void
    const oldPage = new Promise<NotificationPage>((resolve) => {
      resolveFirstPage = resolve
    })
    const listSpy = vi
      .spyOn(notificationApi, 'list')
      .mockReturnValueOnce(oldPage)
      .mockResolvedValueOnce(page([item]))
    vi.spyOn(notificationApi, 'getUnreadCount').mockResolvedValue({ count: 1 })
    const { streamCallCount } = installStreamMock()
    const auth = useAuthStore()
    const store = useNotificationStore()
    stopCallbacks.push(() => store.stop())

    auth.setLoginSession({ token: 'account-a', staff_id: 1, name: '甲', roles: ['PURCHASER'] })
    store.start()
    await vi.waitFor(() => expect(streamCallCount()).toBe(1))

    auth.setLoginSession({ token: 'account-b', staff_id: 2, name: '乙', roles: ['FINANCE'] })
    store.start()
    await vi.waitFor(() => expect(store.recent).toEqual([item]))
    resolveFirstPage(page([{ ...item, id: 12, body: '旧账号正文' }]))
    await Promise.resolve()

    expect(listSpy).toHaveBeenCalledTimes(2)
    expect(store.recent).toEqual([item])
    expect(store.unreadCount).toBe(1)
  })

  it('deduplicates toast IDs across tabs without persisting message bodies', async () => {
    vi.spyOn(notificationApi, 'list').mockResolvedValue(page([]))
    vi.spyOn(notificationApi, 'getUnreadCount').mockResolvedValue({ count: 1 })
    vi.spyOn(notificationApi, 'getDetail').mockResolvedValue(item)
    const { handlers, streamCallCount } = installStreamMock()
    const auth = useAuthStore()
    const store = useNotificationStore()
    stopCallbacks.push(() => store.stop())
    auth.setLoginSession({ token: 'toast-token', staff_id: 7, name: '用户', roles: ['ADMIN'] })
    store.start()
    await vi.waitFor(() => expect(streamCallCount()).toBe(1))

    const secondPinia = createPinia()
    setActivePinia(secondPinia)
    useAuthStore(secondPinia).setLoginSession({
      token: 'toast-token',
      staff_id: 7,
      name: '用户',
      roles: ['ADMIN'],
    })
    const secondStore = useNotificationStore(secondPinia)
    stopCallbacks.push(() => secondStore.stop())
    secondStore.start()
    await vi.waitFor(() => expect(streamCallCount()).toBe(2))

    const signal = { inbox_id: item.id, category: 'approval' as const, cursor: item.cursor }
    handlers[0].onNotification(signal)
    handlers[1].onNotification(signal)
    await vi.waitFor(() =>
      expect(store.toastQueue.length + secondStore.toastQueue.length).toBe(1),
    )

    const dedupeKey = window.localStorage.getItem('erp_notification_toasts_v1_7')
    expect(dedupeKey).toContain(String(item.id))
    expect(dedupeKey).not.toContain(item.body)
    expect([...store.toastQueue, ...secondStore.toastQueue][0]).toEqual(item)
    store.stop()
    secondStore.stop()
  })
})
