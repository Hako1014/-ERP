<template>
  <div class="notification-page">
    <div class="notification-page__heading">
      <div>
        <h1>消息中心</h1>
        <p>查看仅对当前账号可见的审批与业务通知。</p>
      </div>
      <div class="notification-page__heading-actions">
        <el-tag :type="connectionTagType">{{ connectionLabel }}</el-tag>
        <el-button :icon="Refresh" aria-label="刷新消息" @click="resetAndLoad">
          刷新
        </el-button>
        <el-button
          :icon="Check"
          :disabled="notifications.unreadCount === 0 || markingAllRead"
          :loading="markingAllRead"
          @click="markAllRead"
        >
          全部已读
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="notifications.connectionState === 'offline' || notifications.loadError"
      class="notification-page__alert"
      :title="notifications.loadError || '当前离线，已加载的消息仍可查看；恢复连接后会自动同步。'"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="notifications.connectionState === 'revoked'"
      class="notification-page__alert"
      title="账号或数据权限已变化，消息已清空。请刷新页面重新校验权限。"
      type="warning"
      :closable="false"
      show-icon
    />

    <section class="notification-panel" aria-label="通知列表">
      <div class="notification-toolbar">
        <el-radio-group
          v-model="category"
          aria-label="按消息分类筛选"
          @change="resetAndLoad"
        >
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="approval">审批</el-radio-button>
          <el-radio-button value="business">业务</el-radio-button>
          <el-radio-button value="warning">预警</el-radio-button>
        </el-radio-group>
        <el-select
          v-model="readState"
          aria-label="按已读状态筛选"
          class="notification-toolbar__read-filter"
          @change="resetAndLoad"
        >
          <el-option label="全部状态" value="" />
          <el-option label="未读" value="UNREAD" />
          <el-option label="已读" value="READ" />
        </el-select>
      </div>

      <el-skeleton v-if="loading && items.length === 0" :rows="5" animated />
      <el-empty
        v-else-if="items.length === 0 && !loading && !pageError"
        :description="category === 'warning' ? '暂无预警消息' : '暂无消息记录'"
      />
      <div v-else-if="items.length" class="notification-list">
        <button
          v-for="item in items"
          :key="item.id"
          class="notification-row"
          :class="{ 'notification-row--unread': item.read_state === 'UNREAD' }"
          type="button"
          :aria-label="`${item.title}，${categoryLabel(item)}，${item.read_state === 'UNREAD' ? '未读' : '已读'}`"
          @click="showDetail(item)"
        >
          <span class="notification-row__status" :aria-hidden="true">
            <i v-if="item.read_state === 'UNREAD'" />
          </span>
          <span class="notification-row__main">
            <span class="notification-row__title-line">
              <strong>{{ item.title }}</strong>
              <el-tag size="small" :type="tagType(item)">{{ categoryLabel(item) }}</el-tag>
            </span>
            <span class="notification-row__body">{{ item.body }}</span>
            <span class="notification-row__meta">
              {{ formatDateTime(item.created_at) }} · {{ businessStateLabel(item.business_state) }}
            </span>
          </span>
          <el-icon class="notification-row__arrow" aria-hidden="true"><ArrowRight /></el-icon>
        </button>
      </div>

      <el-alert
        v-if="pageError"
        :title="pageError"
        type="error"
        :closable="false"
        show-icon
      />
      <div v-if="nextCursor" class="notification-page__load-more">
        <el-button :loading="loadingMore" :disabled="loading" @click="loadMore">
          加载更多
        </el-button>
      </div>
    </section>

    <el-drawer
      v-model="detailVisible"
      title="消息详情"
      size="min(520px, 94vw)"
      :append-to-body="true"
    >
      <el-skeleton v-if="detailLoading" :rows="4" animated />
      <template v-else-if="detail">
        <div class="notification-detail">
          <div class="notification-detail__heading">
            <h2>{{ detail.title }}</h2>
            <el-tag :type="tagType(detail)">{{ categoryLabel(detail) }}</el-tag>
          </div>
          <p class="notification-detail__body">{{ detail.body }}</p>
          <p class="notification-detail__meta">{{ formatDateTime(detail.created_at) }}</p>
          <el-alert
            v-if="detailError"
            :title="detailError"
            type="warning"
            :closable="false"
            show-icon
          />
          <div class="notification-detail__actions">
            <el-button
              v-if="detail.read_state === 'UNREAD'"
              :loading="markingRead"
              @click="markRead"
            >
              标记已读
            </el-button>
            <el-button
              type="primary"
              :disabled="!targetRoute"
              @click="openTarget"
            >
              打开单据 / 待办
            </el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Check, Refresh } from '@element-plus/icons-vue'

import { ApiError } from '@/api/client'
import {
  notificationApi,
  type NotificationItem,
  type NotificationReadState,
} from '@/api/notification'
import { useAuthStore } from '@/stores/auth'
import { useNotificationStore } from '@/stores/notifications'
import { formatDateTime } from '@/utils/datetime'
import { resolveNotificationTarget } from '@/utils/notification-target'

type CategoryFilter = 'all' | 'approval' | 'business' | 'warning'

const authStore = useAuthStore()
const notifications = useNotificationStore()
const router = useRouter()
const category = ref<CategoryFilter>('all')
const readState = ref<NotificationReadState | ''>('')
const items = ref<NotificationItem[]>([])
const nextCursor = ref<string | null>(null)
const loading = ref(false)
const loadingMore = ref(false)
const markingRead = ref(false)
const markingAllRead = ref(false)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<NotificationItem | null>(null)
const detailError = ref('')
const pageError = ref('')
let queryGeneration = 0

const sessionIdentity = computed(() =>
  authStore.token && authStore.profile?.staff_id
    ? `${authStore.profile.staff_id}:${authStore.token}`
    : '',
)
const targetRoute = computed(() =>
  detail.value ? resolveNotificationTarget(detail.value) : null,
)
const connectionLabel = computed(() => {
  const labels = {
    connecting: '连接中',
    connected: '实时在线',
    polling: '轮询同步',
    offline: '离线',
    revoked: '权限已变化',
  }
  return labels[notifications.connectionState]
})
const connectionTagType = computed(() =>
  notifications.connectionState === 'connected'
    ? 'success'
    : notifications.connectionState === 'revoked' ||
        notifications.connectionState === 'offline'
      ? 'warning'
      : 'info',
)

function isWarning(item: NotificationItem): boolean {
  return /(^|[._])(warning|alert|overdue|timeout|low_stock)([._]|$)/i.test(item.event_code)
}

function matchesCategory(item: NotificationItem): boolean {
  if (category.value === 'all') return true
  if (category.value === 'approval') return item.category === 'approval'
  if (category.value === 'warning') return isWarning(item)
  return item.category !== 'approval' && !isWarning(item)
}

function categoryLabel(item: NotificationItem): string {
  if (isWarning(item)) return '预警'
  return item.category === 'approval' ? '审批' : '业务'
}

function tagType(item: NotificationItem): 'warning' | 'primary' | 'success' {
  if (isWarning(item)) return 'warning'
  return item.category === 'approval' ? 'primary' : 'success'
}

function businessStateLabel(state: string): string {
  return (
    {
      OPEN: '待处理',
      HANDLED: '已办理',
      RESOLVED: '已解决',
      SUPERSEDED: '已失效',
      N_A: '通知',
    } as Record<string, string>
  )[state] || '通知'
}

function resetAndLoad(): void {
  queryGeneration += 1
  items.value = []
  nextCursor.value = null
  pageError.value = ''
  void fetchPage(false)
}

async function fetchPage(append: boolean): Promise<void> {
  const identity = sessionIdentity.value
  const revision = queryGeneration
  if (!identity) return
  if (append) loadingMore.value = true
  else loading.value = true
  pageError.value = ''
  let cursor = append ? nextCursor.value : null
  const collected: NotificationItem[] = []
  let rounds = 0
  try {
    do {
      const result = await notificationApi.list({
        cursor: cursor || undefined,
        category: category.value === 'approval' ? 'approval' : 'all',
        read_state: readState.value || undefined,
        page_size: 50,
      })
      if (identity !== sessionIdentity.value || revision !== queryGeneration) return
      collected.push(...result.items.filter(matchesCategory))
      cursor = result.next_cursor
      rounds += 1
    } while (collected.length < 20 && cursor && rounds < 5)

    if (identity !== sessionIdentity.value || revision !== queryGeneration) return
    const unique = collected.filter(
      (item, index, source) => source.findIndex((candidate) => candidate.id === item.id) === index,
    )
    items.value = append ? [...items.value, ...unique] : unique
    nextCursor.value = cursor
  } catch (error) {
    if (identity !== sessionIdentity.value || revision !== queryGeneration) return
    pageError.value =
      error instanceof ApiError && (error.status === 403 || error.status === 404)
        ? '消息不存在或已无权查看。'
        : error instanceof Error
          ? error.message
          : '消息加载失败，请重试。'
  } finally {
    if (identity === sessionIdentity.value && revision === queryGeneration) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

function loadMore(): void {
  if (nextCursor.value && !loading.value && !loadingMore.value) void fetchPage(true)
}

async function showDetail(item: NotificationItem): Promise<void> {
  const identity = sessionIdentity.value
  detail.value = null
  detailError.value = ''
  detailVisible.value = true
  detailLoading.value = true
  try {
    const current = await notificationApi.getDetail(item.id)
    if (identity !== sessionIdentity.value) return
    detail.value = current
  } catch (error) {
    if (identity !== sessionIdentity.value) return
    detailVisible.value = false
    detailError.value = ''
    ElMessage.error(
      error instanceof ApiError && (error.status === 403 || error.status === 404)
        ? '消息不存在或已无权查看。'
        : error instanceof Error
          ? error.message
          : '消息详情暂时无法获取。',
    )
  } finally {
    if (identity === sessionIdentity.value) detailLoading.value = false
  }
}

async function markRead(): Promise<void> {
  if (!detail.value || detail.value.read_state !== 'UNREAD') return
  const identity = sessionIdentity.value
  const inboxId = detail.value.id
  markingRead.value = true
  try {
    await notificationApi.markRead(inboxId)
    if (identity !== sessionIdentity.value || detail.value?.id !== inboxId) return
    detail.value = { ...detail.value, read_state: 'READ', read_at: new Date().toISOString() }
    notifications.markReadChanged()
  } catch (error) {
    if (identity !== sessionIdentity.value) return
    ElMessage.error(error instanceof Error ? error.message : '标记已读失败，请重试。')
  } finally {
    markingRead.value = false
  }
}

async function markAllRead(): Promise<void> {
  if (notifications.unreadCount === 0) return
  const identity = sessionIdentity.value
  markingAllRead.value = true
  try {
    await notificationApi.markAllRead()
    if (identity !== sessionIdentity.value) return
    if (detail.value?.read_state === 'UNREAD') {
      detail.value = { ...detail.value, read_state: 'READ', read_at: new Date().toISOString() }
    }
    notifications.markReadChanged()
    ElMessage.success('所有可见未读消息已标记为已读。')
  } catch (error) {
    if (identity !== sessionIdentity.value) return
    ElMessage.error(error instanceof Error ? error.message : '全部已读操作失败。')
  } finally {
    markingAllRead.value = false
  }
}

async function openTarget(): Promise<void> {
  if (!detail.value) return
  const identity = sessionIdentity.value
  const inboxId = detail.value.id
  try {
    const latest = await notificationApi.getDetail(inboxId)
    if (identity !== sessionIdentity.value || detail.value?.id !== inboxId) return
    const target = resolveNotificationTarget(latest)
    if (!target) {
      detailError.value = '此类通知暂不支持直接跳转，请从对应业务模块查看。'
      return
    }
    await router.push(target)
  } catch (error) {
    if (identity !== sessionIdentity.value || detail.value?.id !== inboxId) return
    detailError.value =
      error instanceof ApiError && (error.status === 403 || error.status === 404)
        ? '目标单据已失效或当前账号无权查看。'
        : error instanceof Error
          ? error.message
          : '目标单据暂时无法打开。'
  }
}

watch(sessionIdentity, (identity) => {
  queryGeneration += 1
  items.value = []
  detail.value = null
  detailVisible.value = false
  nextCursor.value = null
  pageError.value = ''
  if (identity) void fetchPage(false)
}, { immediate: true })

watch(
  () => notifications.syncVersion,
  () => {
    if (sessionIdentity.value) resetAndLoad()
  },
)

onBeforeUnmount(() => {
  queryGeneration += 1
})
</script>

<style scoped>
.notification-page { display: grid; gap: 16px; max-width: 1180px; margin: 0 auto; }
.notification-page__heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.notification-page__heading h1 { margin: 0; font-size: 22px; color: var(--el-text-color-primary); }
.notification-page__heading p { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 13px; }
.notification-page__heading-actions { display: flex; align-items: center; gap: 8px; }
.notification-page__alert { margin-bottom: 0; }
.notification-panel { overflow: hidden; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-bg-color); }
.notification-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--el-border-color-lighter); }
.notification-toolbar__read-filter { width: 130px; }
.notification-list { display: grid; }
.notification-row { display: flex; width: 100%; align-items: center; gap: 10px; padding: 15px 16px; border: 0; border-bottom: 1px solid var(--el-border-color-lighter); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.notification-row:last-child { border-bottom: 0; }
.notification-row:hover, .notification-row:focus-visible { background: var(--el-fill-color-light); outline: none; }
.notification-row:focus-visible { box-shadow: inset 0 0 0 2px var(--el-color-primary); }
.notification-row--unread { background: color-mix(in srgb, var(--el-color-primary-light-9) 40%, transparent); }
.notification-row__status { display: flex; width: 10px; flex: 0 0 10px; justify-content: center; }
.notification-row__status i { width: 7px; height: 7px; border-radius: 50%; background: var(--el-color-primary); }
.notification-row__main { display: grid; min-width: 0; flex: 1; gap: 5px; }
.notification-row__title-line { display: flex; align-items: center; gap: 8px; min-width: 0; }
.notification-row__title-line strong { overflow: hidden; color: var(--el-text-color-primary); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.notification-row__body { overflow: hidden; color: var(--el-text-color-regular); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.notification-row__meta { color: var(--el-text-color-secondary); font-size: 12px; }
.notification-row__arrow { color: var(--el-text-color-placeholder); }
.notification-page__load-more { display: flex; justify-content: center; padding: 16px; }
.notification-detail { display: grid; gap: 16px; }
.notification-detail__heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.notification-detail__heading h2 { margin: 0; color: var(--el-text-color-primary); font-size: 18px; line-height: 1.5; }
.notification-detail__body { margin: 0; color: var(--el-text-color-regular); line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.notification-detail__meta { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; }
.notification-detail__actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 8px; }
@media (max-width: 700px) {
  .notification-page__heading { align-items: flex-start; flex-direction: column; }
  .notification-page__heading-actions { width: 100%; flex-wrap: wrap; }
  .notification-toolbar { align-items: flex-start; flex-direction: column; }
  .notification-toolbar :deep(.el-radio-group) { display: grid; width: 100%; grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .notification-toolbar :deep(.el-radio-button), .notification-toolbar :deep(.el-radio-button__inner) { width: 100%; }
  .notification-toolbar__read-filter { width: 100%; }
  .notification-row { align-items: flex-start; padding: 13px 10px; }
  .notification-row__title-line { flex-wrap: wrap; }
}
</style>
