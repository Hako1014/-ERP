<template>
  <header class="app-header">
    <el-button
      text
      class="app-header__toggle"
      :aria-label="collapsed ? '展开菜单' : '收起菜单'"
      @click="emit('toggle')"
    >
      <el-icon size="20">
        <Expand v-if="collapsed" />
        <Fold v-else />
      </el-icon>
    </el-button>

    <div class="app-header__spacer" />

    <div class="app-header__actions">
      <el-popover
        v-model:visible="notificationPopoverVisible"
        placement="bottom-end"
        :width="360"
        trigger="click"
        popper-class="notification-preview-popper"
        @show="refreshNotifications"
      >
        <template #reference>
          <el-button
            text
            circle
            class="app-header__notification"
            :aria-label="`消息中心，${notificationStore.unreadCount} 条未读`"
            aria-haspopup="dialog"
            :aria-expanded="notificationPopoverVisible"
          >
            <el-badge
              :value="notificationStore.unreadCount"
              :max="99"
              :hidden="notificationStore.unreadCount === 0"
            >
              <el-icon><Bell /></el-icon>
            </el-badge>
          </el-button>
        </template>
        <section class="notification-preview" aria-label="最近消息">
          <div class="notification-preview__heading">
            <div>
              <strong>最近消息</strong>
              <span>{{ notificationStore.unreadCount }} 条未读</span>
            </div>
            <el-button link type="primary" @click="goToCenter">查看全部</el-button>
          </div>
          <el-skeleton v-if="previewLoading" :rows="3" animated />
          <el-empty
            v-else-if="notificationStore.recent.length === 0"
            :image-size="56"
            description="暂无消息"
          />
          <div v-else class="notification-preview__list">
            <button
              v-for="item in notificationStore.recent"
              :key="item.id"
              type="button"
              class="notification-preview__item"
              @click="openTarget(item)"
            >
              <span class="notification-preview__item-title">
                <i v-if="item.read_state === 'UNREAD'" aria-label="未读" />
                <strong>{{ item.title }}</strong>
              </span>
              <span class="notification-preview__item-body">{{ item.body }}</span>
              <span class="notification-preview__item-date">{{ formatDateTime(item.created_at) }}</span>
            </button>
          </div>
          <p v-if="notificationStore.connectionState === 'offline'" class="notification-preview__offline">
            当前离线，最近已加载消息仍可查看。
          </p>
        </section>
      </el-popover>
      <el-tooltip content="公告" placement="bottom">
        <el-badge :value="workbenchStore.announcementCount" :hidden="workbenchStore.announcementCount === 0" :max="99">
          <el-button
            text circle class="app-header__secondary-action" aria-label="公告"
            aria-haspopup="dialog" :aria-expanded="workbenchStore.active === 'announcements'"
            @click="openWorkbench('announcements')"
          ><el-icon><ChatDotRound /></el-icon></el-button>
        </el-badge>
      </el-tooltip>
      <el-tooltip content="待办" placement="bottom">
        <el-badge :value="workbenchStore.todoCount" :hidden="workbenchStore.todoCount === 0" :max="99">
          <el-button
            text circle class="app-header__secondary-action" aria-label="待办"
            aria-haspopup="dialog" :aria-expanded="workbenchStore.active === 'todos'"
            @click="openWorkbench('todos')"
          ><el-icon><List /></el-icon></el-button>
        </el-badge>
      </el-tooltip>
      <el-tooltip content="帮助" placement="bottom">
        <el-button
          text circle class="app-header__secondary-action" aria-label="帮助"
          aria-haspopup="dialog" :aria-expanded="workbenchStore.active === 'help'"
          @click="openWorkbench('help')"
        ><el-icon><QuestionFilled /></el-icon></el-button>
      </el-tooltip>
      <el-dropdown class="app-header__mobile-more" trigger="click" @command="openWorkbench">
        <el-button text circle aria-label="更多功能" aria-haspopup="menu">
          <el-icon><MoreFilled /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="announcements">
              公告{{ workbenchStore.announcementCount ? `（${workbenchStore.announcementCount}）` : '' }}
            </el-dropdown-item>
            <el-dropdown-item command="todos">
              我的待办{{ workbenchStore.todoCount ? `（${workbenchStore.todoCount}）` : '' }}
            </el-dropdown-item>
            <el-dropdown-item command="help">帮助</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <el-dropdown trigger="click" @command="handleCommand">
      <button class="app-header__user" type="button">
        <el-avatar :size="30" :src="avatarUrl || undefined">{{ avatarText }}</el-avatar>
        <span>{{ authStore.displayName }}</span>
        <el-icon><ArrowDown /></el-icon>
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="profile">个人资料</el-dropdown-item>
          <el-dropdown-item command="organization">组织人员</el-dropdown-item>
          <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
    <ProfileDialog v-model="profileDialogVisible" />
    <OrganizationDirectoryDialog v-model="organizationDialogVisible" />
    <WorkbenchPanels :active="workbenchStore.active" @close="workbenchStore.close()" />
  </header>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import {
  ArrowDown,
  Bell,
  ChatDotRound,
  Expand,
  Fold,
  List,
  MoreFilled,
  QuestionFilled,
} from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { notificationApi, type NotificationItem } from '@/api/notification'
import { useNotificationStore } from '@/stores/notifications'
import { formatDateTime } from '@/utils/datetime'
import { resolveNotificationTarget } from '@/utils/notification-target'
import { ApiError } from '@/api/client'
import { profileApi } from '@/api/profile'
import ProfileDialog from './ProfileDialog.vue'
import OrganizationDirectoryDialog from './OrganizationDirectoryDialog.vue'
import WorkbenchPanels from './WorkbenchPanels.vue'
import { workbenchApi } from '@/api/workbench'
import { useWorkbenchPanelStore, type WorkbenchPanel } from '@/stores/workbenchPanels'

defineProps<{
  collapsed: boolean
}>()

const emit = defineEmits<{
  toggle: []
}>()

const router = useRouter()
const authStore = useAuthStore()
const notificationStore = useNotificationStore()
const workbenchStore = useWorkbenchPanelStore()
const notificationPopoverVisible = ref(false)
const previewLoading = ref(false)
const toastHandles = new Map<number, { close: () => void }>()
const avatarText = computed(() => authStore.displayName.slice(0, 1))
const profileDialogVisible = ref(false)
const organizationDialogVisible = ref(false)
const avatarUrl = ref('')
let countRequestSequence = 0

const sessionIdentity = computed(() =>
  authStore.token && authStore.profile?.staff_id
    ? `${authStore.profile.staff_id}:${authStore.token}`
    : '',
)

watch(
  sessionIdentity,
  (identity) => {
    for (const handle of toastHandles.values()) handle.close()
    toastHandles.clear()
    workbenchStore.resetForIdentity(identity)
    notificationStore.start()
    if (identity) void refreshWorkbenchCounts(identity)
    void refreshHeaderAvatar(identity)
  },
  { immediate: true },
)

async function refreshWorkbenchCounts(expectedIdentity: string): Promise<void> {
  const sequence = ++countRequestSequence
  const [announcementResult, todoResult] = await Promise.allSettled([
    workbenchApi.listAnnouncements(1),
    workbenchApi.listTodos(1),
  ])
  if (
    sequence !== countRequestSequence
    || expectedIdentity !== sessionIdentity.value
    || expectedIdentity !== workbenchStore.identity
  ) return
  workbenchStore.updateCounts(expectedIdentity, {
    announcements: announcementResult.status === 'fulfilled' ? announcementResult.value.total : 0,
    todos: todoResult.status === 'fulfilled' ? todoResult.value.total : 0,
  })
}

function openWorkbench(panel: string): void {
  if (panel === 'announcements' || panel === 'todos' || panel === 'help') {
    workbenchStore.open(panel as WorkbenchPanel)
  }
}

watch(() => authStore.profile?.avatar_version, () => {
  void refreshHeaderAvatar(sessionIdentity.value)
})

async function refreshHeaderAvatar(expectedIdentity: string): Promise<void> {
  if (avatarUrl.value) URL.revokeObjectURL(avatarUrl.value)
  avatarUrl.value = ''
  const expectedVersion = authStore.profile?.avatar_version
  if (!expectedIdentity || !expectedVersion) return
  try {
    const image = await profileApi.getAvatar()
    if (sessionIdentity.value !== expectedIdentity || authStore.profile?.avatar_version !== expectedVersion) return
    avatarUrl.value = URL.createObjectURL(image)
  } catch {
    // 头像不可用时保留首字母，不影响导航和个人资料编辑。
  }
}

watch(
  () => notificationStore.toastQueue.length,
  () => {
    let item = notificationStore.popToast()
    while (item) {
      const current = item
      const identity = sessionIdentity.value
      const handle = ElNotification({
        title: current.title,
        message: current.body,
        type: 'info',
        duration: 8_000,
        onClick: () => {
          if (identity === sessionIdentity.value) void openTarget(current, identity, true)
        },
        onClose: () => toastHandles.delete(current.id),
      })
      toastHandles.set(current.id, handle)
      item = notificationStore.popToast()
    }
  },
)

async function refreshNotifications(): Promise<void> {
  previewLoading.value = true
  try {
    await notificationStore.refreshSnapshot()
  } finally {
    previewLoading.value = false
  }
}

function goToCenter(): void {
  notificationPopoverVisible.value = false
  void router.push({ name: 'notifications' })
}

async function openTarget(
  item: NotificationItem,
  expectedIdentity = sessionIdentity.value,
  fromToast = false,
): Promise<void> {
  if (!expectedIdentity || expectedIdentity !== sessionIdentity.value) return
  if (!fromToast && !notificationStore.recent.some((entry) => entry.id === item.id)) return
  notificationPopoverVisible.value = false
  try {
    const current = await notificationApi.getDetail(item.id)
    if (expectedIdentity !== sessionIdentity.value) return
    const target = resolveNotificationTarget(current)
    if (!target) {
      ElMessage.warning('此类消息暂无可用的单据跳转入口。')
      void router.push({ name: 'notifications' })
      return
    }
    await router.push(target)
  } catch (error) {
    if (expectedIdentity !== sessionIdentity.value) return
    ElMessage.error(
      error instanceof ApiError && (error.status === 403 || error.status === 404)
        ? '消息不存在或已无权查看。'
        : error instanceof Error
          ? error.message
          : '目标单据暂时无法打开。',
    )
  }
}

function handleCommand(command: string): void {
  if (command === 'profile') {
    profileDialogVisible.value = true
    return
  }
  if (command === 'organization') {
    organizationDialogVisible.value = true
    return
  }
  if (command !== 'logout') {
    return
  }
  if (avatarUrl.value) URL.revokeObjectURL(avatarUrl.value)
  avatarUrl.value = ''
  countRequestSequence += 1
  workbenchStore.close()
  authStore.clearSession()
  notificationStore.stop()
  void router.push('/login')
}

onBeforeUnmount(() => {
  if (avatarUrl.value) URL.revokeObjectURL(avatarUrl.value)
  for (const handle of toastHandles.values()) handle.close()
  toastHandles.clear()
  notificationStore.stop()
})
</script>

<style scoped>
.app-header {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 18px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}

.app-header__toggle {
  font-size: 18px;
}

.app-header__spacer {
  flex: 1;
}

.app-header__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.app-header__mobile-more { display: none; }

.app-header__user {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--el-text-color-primary);
  cursor: pointer;
}

.app-header__user:hover {
  background: var(--el-fill-color-light);
}

@media (max-width: 720px) {
  .app-header__actions { gap: 0; }
  .app-header__secondary-action { display: none; }
  .app-header__mobile-more { display: inline-flex; }
  .app-header__user > span { display: none; }
  .app-header { gap: 6px; padding: 0 10px; }
}

.notification-preview { width: min(360px, 84vw); }
.notification-preview__heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.notification-preview__heading > div { display: grid; gap: 3px; }
.notification-preview__heading strong { color: var(--el-text-color-primary); }
.notification-preview__heading span, .notification-preview__item-date { color: var(--el-text-color-secondary); font-size: 12px; }
.notification-preview__list { display: grid; max-height: min(440px, 65vh); overflow: auto; }
.notification-preview__item { display: grid; gap: 5px; padding: 10px 4px; border: 0; border-top: 1px solid var(--el-border-color-lighter); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.notification-preview__item:hover, .notification-preview__item:focus-visible { background: var(--el-fill-color-light); outline: none; }
.notification-preview__item-title { display: flex; align-items: center; gap: 7px; min-width: 0; }
.notification-preview__item-title strong { overflow: hidden; color: var(--el-text-color-primary); text-overflow: ellipsis; white-space: nowrap; }
.notification-preview__item-title i { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: var(--el-color-primary); }
.notification-preview__item-body { overflow: hidden; color: var(--el-text-color-regular); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.notification-preview__offline { margin: 8px 0 0; color: var(--el-color-warning); font-size: 12px; }
:global(.notification-preview-popper) { max-width: calc(100vw - 16px); }
@media (max-width: 420px) {
  .notification-preview { width: min(330px, 80vw); }
  .app-header__user { gap: 4px; }
  .app-header__user :deep(.el-avatar) { --el-avatar-size: 28px; }
}
</style>
