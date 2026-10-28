import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  notificationApi,
  type NotificationItem,
  type NotificationSignal,
} from '@/api/notification'
import { useAuthStore } from '@/stores/auth'

type ConnectionState = 'connecting' | 'connected' | 'polling' | 'offline' | 'revoked'
type NotificationChannelMessage = { type: 'refresh'; staffId: number }

const TOAST_DEDUPE_TTL_MS = 4 * 60 * 60 * 1_000
const TOAST_DEDUPE_LIMIT = 200

function sessionKey(): string {
  const auth = useAuthStore()
  const token = auth.token
  const staffId = auth.profile?.staff_id
  return token && staffId ? `${staffId}:${token}` : ''
}

function claimToast(staffId: number, inboxId: number): boolean {
  const key = `erp_notification_toasts_v1_${staffId}`
  const now = Date.now()
  try {
    const raw = window.localStorage.getItem(key)
    const saved = raw ? (JSON.parse(raw) as Array<[number, number]>) : []
    const recent = Array.isArray(saved)
      ? saved.filter(
          (entry) =>
            Array.isArray(entry) &&
            Number.isSafeInteger(entry[0]) &&
            typeof entry[1] === 'number' &&
            now - entry[1] < TOAST_DEDUPE_TTL_MS,
        )
      : []
    if (recent.some(([id]) => id === inboxId)) return false
    recent.push([inboxId, now])
    window.localStorage.setItem(
      key,
      JSON.stringify(recent.slice(-TOAST_DEDUPE_LIMIT)),
    )
    return true
  } catch {
    // 存储不可用时仍可在当前标签页内去重；这里只保存消息 ID 与时间，不保存正文。
    return true
  }
}

export const useNotificationStore = defineStore('notifications', () => {
  const authStore = useAuthStore()
  const recent = ref<NotificationItem[]>([])
  const unreadCount = ref(0)
  const syncVersion = ref(0)
  const connectionState = ref<ConnectionState>('offline')
  const loadError = ref('')
  const toastQueue = ref<NotificationItem[]>([])

  let activeIdentity = ''
  let generation = 0
  let streamController: AbortController | null = null
  let channel: BroadcastChannel | null = null
  let activeStaffId: number | null = null
  let onOnline: (() => void) | null = null
  let onOffline: (() => void) | null = null
  let onVisibility: (() => void) | null = null

  function isCurrent(identity: string, revision: number): boolean {
    return activeIdentity === identity && generation === revision && sessionKey() === identity
  }

  function clearMemory(): void {
    recent.value = []
    unreadCount.value = 0
    toastQueue.value = []
    loadError.value = ''
  }

  function stop(): void {
    generation += 1
    activeIdentity = ''
    streamController?.abort()
    streamController = null
    channel?.close()
    channel = null
    activeStaffId = null
    if (onOnline) window.removeEventListener('online', onOnline)
    if (onOffline) window.removeEventListener('offline', onOffline)
    if (onVisibility) document.removeEventListener('visibilitychange', onVisibility)
    onOnline = null
    onOffline = null
    onVisibility = null
    connectionState.value = 'offline'
    clearMemory()
  }

  async function refreshSnapshot(identity = activeIdentity, revision = generation): Promise<void> {
    if (!identity || !isCurrent(identity, revision)) return
    try {
      const [page, count] = await Promise.all([
        notificationApi.list({ page_size: 6 }),
        notificationApi.getUnreadCount(),
      ])
      if (!isCurrent(identity, revision)) return
      recent.value = page.items
      unreadCount.value = count.count
      syncVersion.value += 1
      loadError.value = ''
    } catch (error) {
      if (!isCurrent(identity, revision)) return
      loadError.value = error instanceof Error ? error.message : '消息同步失败'
    }
  }

  function broadcastRefresh(staffId: number): void {
    try {
      channel?.postMessage({ type: 'refresh', staffId } satisfies NotificationChannelMessage)
    } catch {
      // 标签页同步失败不影响当前标签页和持久化消息中心。
    }
  }

  async function receiveSignal(
    signal: NotificationSignal,
    identity: string,
    revision: number,
    staffId: number,
  ): Promise<void> {
    connectionState.value = 'connected'
    await refreshSnapshot(identity, revision)
    broadcastRefresh(staffId)
    try {
      const item = await notificationApi.getDetail(signal.inbox_id)
      if (!isCurrent(identity, revision)) return
      if (claimToast(staffId, item.id)) toastQueue.value.push(item)
    } catch {
      // 消息可能已失效或撤权；详情接口拒绝时不展示标题、正文或目标信息。
    }
  }

  function start(): void {
    const nextIdentity = sessionKey()
    if (!nextIdentity) {
      if (activeIdentity || recent.value.length || unreadCount.value) stop()
      return
    }
    if (nextIdentity === activeIdentity && streamController) return

    stop()
    activeIdentity = nextIdentity
    const revision = generation
    const staffId = authStore.profile?.staff_id
    if (!staffId) return
    activeStaffId = staffId
    connectionState.value = navigator.onLine === false ? 'offline' : 'connecting'

    if (typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel(`erp-notifications-${staffId}`)
      channel.onmessage = (event: MessageEvent<NotificationChannelMessage>) => {
        if (
          event.data?.type === 'refresh' &&
          event.data.staffId === staffId &&
          isCurrent(nextIdentity, revision)
        ) {
          void refreshSnapshot(nextIdentity, revision)
        }
      }
    }

    const current = () => isCurrent(nextIdentity, revision)
    onOnline = () => {
      if (!current()) return
      start()
      void refreshSnapshot(nextIdentity, revision)
    }
    onOffline = () => {
      if (!current()) return
      connectionState.value = 'offline'
      streamController?.abort()
      streamController = null
    }
    onVisibility = () => {
      if (document.visibilityState === 'visible' && current()) {
        void refreshSnapshot(nextIdentity, revision)
        if (!streamController && navigator.onLine !== false) start()
      }
    }
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    document.addEventListener('visibilitychange', onVisibility)

    void refreshSnapshot(nextIdentity, revision)
    if (navigator.onLine === false) return

    const controller = new AbortController()
    streamController = controller
    void notificationApi
      .stream(
        {
          onNotification: (signal) => {
            if (current()) void receiveSignal(signal, nextIdentity, revision, staffId)
          },
          onConnected: () => {
            if (current()) connectionState.value = 'connected'
          },
          onAuthExpired: () => {
            if (current()) authStore.clearSession()
          },
          onAuthRevoked: () => {
            if (!current()) return
            clearMemory()
            connectionState.value = 'revoked'
          },
          onPollingFallback: () => {
            if (current()) connectionState.value = 'polling'
          },
          onError: () => {
            if (current() && navigator.onLine !== false) {
              connectionState.value = 'connecting'
            }
          },
        },
        { signal: controller.signal },
      )
      .catch(() => {
        if (current()) connectionState.value = 'offline'
      })
      .finally(() => {
        if (streamController === controller) streamController = null
      })
  }

  function markReadChanged(): void {
    if (activeStaffId) broadcastRefresh(activeStaffId)
    void refreshSnapshot()
  }

  function popToast(): NotificationItem | undefined {
    return toastQueue.value.shift()
  }

  return {
    recent,
    unreadCount,
    syncVersion,
    connectionState,
    loadError,
    toastQueue,
    start,
    stop,
    refreshSnapshot,
    markReadChanged,
    popToast,
  }
})
