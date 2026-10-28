<template>
  <el-dialog
    :model-value="Boolean(active)"
    :title="dialogTitle"
    width="min(560px, calc(100vw - 28px))"
    append-to-body
    destroy-on-close
    :close-on-click-modal="true"
    :close-on-press-escape="true"
    @close="close"
  >
    <section v-if="active === 'announcements'" class="workbench-panel" aria-label="公告面板">
      <el-button v-if="selectedAnnouncement" text type="primary" @click="selectedAnnouncement = null">
        <el-icon><ArrowLeft /></el-icon>返回公告列表
      </el-button>
      <el-skeleton v-if="loading" :rows="4" animated />
      <div v-else-if="error" class="workbench-panel__state">
        <el-alert :title="error" type="error" :closable="false" show-icon />
        <el-button type="primary" plain @click="loadCurrent">重试</el-button>
      </div>
      <template v-else-if="selectedAnnouncement">
        <h3 class="workbench-panel__detail-title">{{ selectedAnnouncement.title }}</h3>
        <p class="workbench-panel__meta">{{ formatDateTime(selectedAnnouncement.published_at) }}</p>
        <div class="workbench-panel__content">{{ selectedAnnouncement.content }}</div>
      </template>
      <template v-else-if="announcements.length">
        <button
          v-for="item in announcements"
          :key="item.id"
          class="workbench-panel__row"
          type="button"
          @click="openAnnouncement(item)"
        >
          <span class="workbench-panel__row-heading">
            <strong>{{ item.title }}</strong>
            <el-icon><ArrowRight /></el-icon>
          </span>
          <span class="workbench-panel__preview">{{ item.preview }}</span>
          <span class="workbench-panel__meta">{{ formatDateTime(item.published_at) }}</span>
        </button>
      </template>
      <el-empty v-else :image-size="72" description="暂无当前可见的有效公告">
        <el-button text type="primary" @click="loadCurrent">刷新公告</el-button>
      </el-empty>
    </section>

    <section v-else-if="active === 'todos'" class="workbench-panel" aria-label="待办面板">
      <el-skeleton v-if="loading" :rows="4" animated />
      <div v-else-if="error" class="workbench-panel__state">
        <el-alert :title="error" type="error" :closable="false" show-icon />
        <el-button type="primary" plain @click="loadCurrent">重试</el-button>
      </div>
      <template v-else-if="!todosAvailable">
        <el-empty :image-size="72" description="当前角色没有审批权限，因此没有审批待办">
          <span class="workbench-panel__hint">如需处理审批，请联系管理员调整角色权限。</span>
        </el-empty>
      </template>
      <template v-else-if="todos.length">
        <button
          v-for="item in todos"
          :key="`${item.doc_type}:${item.doc_id}`"
          class="workbench-panel__row"
          type="button"
          @click="openTodo(item)"
        >
          <span class="workbench-panel__row-heading">
            <strong>{{ item.title }}</strong>
            <el-icon><ArrowRight /></el-icon>
          </span>
          <span class="workbench-panel__preview">当前审批环节：{{ item.current_step ?? '待确认' }}</span>
          <span class="workbench-panel__meta">提交于 {{ formatDateTime(item.submitted_at) }}</span>
        </button>
      </template>
      <el-empty v-else :image-size="72" description="目前没有需要你处理的审批">
        <el-button text type="primary" @click="openApprovalList">前往我的审批</el-button>
      </el-empty>
    </section>

    <section v-else-if="active === 'help'" class="workbench-panel" aria-label="帮助面板">
      <el-skeleton v-if="loading" :rows="4" animated />
      <div v-else-if="error" class="workbench-panel__state">
        <el-alert :title="error" type="error" :closable="false" show-icon />
        <el-button type="primary" plain @click="loadCurrent">重试</el-button>
      </div>
      <template v-else>
        <h3 class="workbench-panel__detail-title">{{ help?.title || '帮助中心' }}</h3>
        <p v-if="help?.guidance" class="workbench-panel__content">{{ help.guidance }}</p>
        <div v-if="help?.sources.length" class="workbench-panel__sources">
          <strong>当前权限下的页面资料</strong>
          <p v-for="source in help.sources" :key="`${source.type}:${source.title}`">
            {{ source.title }}<small>{{ source.scope }}</small>
          </p>
        </div>
        <div v-if="help?.faq" class="workbench-panel__sources">
          <strong>常见问题</strong>
          <p class="workbench-panel__content">{{ help.faq }}</p>
        </div>
        <el-empty
          v-if="!help?.guidance && !help?.sources.length"
          :image-size="56"
          description="暂无本页面专属资料，可先查看通用问题或咨询 AI 助手"
        />
        <el-button
          v-if="help?.assistant_available && help.assistant_path"
          type="primary"
          @click="openAssistant"
        >
          打开 AI 助手
        </el-button>
      </template>
    </section>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import { workbenchApi, type AnnouncementDetail, type AnnouncementSummary, type WorkbenchHelp, type WorkbenchTodo } from '@/api/workbench'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useWorkbenchPanelStore, type WorkbenchPanel } from '@/stores/workbenchPanels'
import { formatDateTime } from '@/utils/datetime'

const props = defineProps<{ active: WorkbenchPanel | null }>()
const emit = defineEmits<{ close: [] }>()

const authStore = useAuthStore()
const panelStore = useWorkbenchPanelStore()
const router = useRouter()
const route = useRoute()
const identity = computed(() => authStore.token && authStore.profile?.staff_id
  ? `${authStore.profile.staff_id}:${authStore.token}`
  : '')
const dialogTitle = computed(() => ({
  announcements: '公告',
  todos: '我的待办',
  help: '帮助',
}[props.active || 'help']))

const loading = ref(false)
const error = ref('')
const announcements = ref<AnnouncementSummary[]>([])
const selectedAnnouncement = ref<AnnouncementDetail | null>(null)
const todos = ref<WorkbenchTodo[]>([])
const todosAvailable = ref(true)
const help = ref<WorkbenchHelp | null>(null)
let controller: AbortController | null = null
let requestSequence = 0

function close(): void {
  controller?.abort()
  selectedAnnouncement.value = null
  emit('close')
}

function requestIsCurrent(sequence: number, expectedIdentity: string, panel: WorkbenchPanel): boolean {
  return sequence === requestSequence
    && expectedIdentity === identity.value
    && panelStore.active === panel
}

async function loadCurrent(): Promise<void> {
  const panel = props.active
  const expectedIdentity = identity.value
  if (!panel || !expectedIdentity) return
  controller?.abort()
  const nextController = new AbortController()
  controller = nextController
  const sequence = ++requestSequence
  loading.value = true
  error.value = ''
  selectedAnnouncement.value = null
  try {
    if (panel === 'announcements') {
      const result = await workbenchApi.listAnnouncements(10, nextController.signal)
      if (!requestIsCurrent(sequence, expectedIdentity, panel)) return
      announcements.value = result.items
      panelStore.updateCounts(expectedIdentity, { announcements: result.total })
    } else if (panel === 'todos') {
      const result = await workbenchApi.listTodos(10, nextController.signal)
      if (!requestIsCurrent(sequence, expectedIdentity, panel)) return
      todosAvailable.value = result.available
      todos.value = result.items
      panelStore.updateCounts(expectedIdentity, { todos: result.total })
    } else {
      const result = await workbenchApi.getHelp(route.path, nextController.signal)
      if (!requestIsCurrent(sequence, expectedIdentity, panel)) return
      help.value = result
    }
  } catch (caught) {
    if (!requestIsCurrent(sequence, expectedIdentity, panel) || nextController.signal.aborted) return
    error.value = caught instanceof Error ? caught.message : '加载失败，请重试。'
    if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) {
      error.value = caught.status === 403 ? '当前账号暂时无权查看，请联系管理员。' : '登录状态已失效，请重新登录。'
    }
  } finally {
    if (requestIsCurrent(sequence, expectedIdentity, panel)) loading.value = false
  }
}

async function openAnnouncement(item: AnnouncementSummary): Promise<void> {
  const expectedIdentity = identity.value
  const sequence = ++requestSequence
  controller?.abort()
  const nextController = new AbortController()
  controller = nextController
  loading.value = true
  error.value = ''
  try {
    const result = await workbenchApi.getAnnouncement(item.id, nextController.signal)
    if (!requestIsCurrent(sequence, expectedIdentity, 'announcements')) return
    selectedAnnouncement.value = result
  } catch (caught) {
    if (!requestIsCurrent(sequence, expectedIdentity, 'announcements') || nextController.signal.aborted) return
    error.value = caught instanceof ApiError && (caught.status === 403 || caught.status === 404)
      ? '公告已撤回、过期或当前账号无权查看。'
      : caught instanceof Error ? caught.message : '公告详情加载失败，请重试。'
  } finally {
    if (requestIsCurrent(sequence, expectedIdentity, 'announcements')) loading.value = false
  }
}

async function openTodo(item: WorkbenchTodo): Promise<void> {
  const expectedIdentity = identity.value
  if (!expectedIdentity || !item.target.path.startsWith('/')) return
  close()
  await router.push({ path: item.target.path, query: item.target.query })
}

async function openApprovalList(): Promise<void> {
  close()
  await router.push('/approval/mine')
}

async function openAssistant(): Promise<void> {
  const target = help.value?.assistant_path
  if (!target || !target.startsWith('/')) return
  const sourcePath = route.path === target ? undefined : route.path
  close()
  await router.push({ path: target, query: sourcePath ? { from: sourcePath } : undefined })
}

watch(
  () => [props.active, identity.value] as const,
  ([panel]) => {
    controller?.abort()
    requestSequence += 1
    loading.value = false
    error.value = ''
    announcements.value = []
    selectedAnnouncement.value = null
    todos.value = []
    todosAvailable.value = true
    help.value = null
    if (panel) void loadCurrent()
  },
  { immediate: true },
)

onBeforeUnmount(() => controller?.abort())
</script>

<style scoped>
.workbench-panel { min-height: 180px; max-height: min(65vh, 560px); overflow: auto; }
.workbench-panel__state { display: grid; justify-items: start; gap: 14px; }
.workbench-panel__row { display: grid; width: 100%; gap: 7px; padding: 13px 4px; border: 0; border-bottom: 1px solid var(--el-border-color-lighter); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.workbench-panel__row:hover, .workbench-panel__row:focus-visible { background: var(--el-fill-color-light); outline: 2px solid var(--el-color-primary-light-5); outline-offset: -2px; }
.workbench-panel__row-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--el-text-color-primary); }
.workbench-panel__preview { overflow: hidden; color: var(--el-text-color-regular); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.workbench-panel__meta, .workbench-panel__hint { color: var(--el-text-color-secondary); font-size: 12px; }
.workbench-panel__detail-title { margin: 8px 0; color: var(--el-text-color-primary); }
.workbench-panel__content { white-space: pre-wrap; line-height: 1.65; color: var(--el-text-color-regular); }
.workbench-panel__sources { margin: 16px 0; }
.workbench-panel__sources > strong { color: var(--el-text-color-primary); }
.workbench-panel__sources p { margin: 8px 0; padding: 9px 10px; background: var(--el-fill-color-light); border-radius: 5px; }
.workbench-panel__sources small { display: block; margin-top: 4px; color: var(--el-text-color-secondary); }
</style>
