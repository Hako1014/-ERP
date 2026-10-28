<template>
  <div v-if="canShowAssistant" class="assistant-float" :class="{ 'is-workspace': isWorkspaceRoute }">
    <button v-if="!open && !isWorkspaceRoute" ref="launcherRef" class="assistant-float__launcher" type="button"
      aria-label="打开 AI 助手" aria-controls="assistant-float-panel" :aria-expanded="open" @click="openPanel">
      <el-icon><ChatDotRound /></el-icon><span>AI 助手</span>
    </button>
    <section v-else id="assistant-float-panel" class="assistant-float__panel"
      :class="{ 'is-compact': compact, 'is-workspace': isWorkspaceRoute }" :style="panelStyle"
      :role="isWorkspaceRoute ? 'region' : 'dialog'" :aria-modal="isWorkspaceRoute ? undefined : 'false'"
      :aria-label="isWorkspaceRoute ? 'AI 助手工作台' : 'AI 助手'" @keydown.esc="onPanelEscape">
      <header class="assistant-float__header" :class="{ 'is-draggable': !compact && !isWorkspaceRoute }"
        :tabindex="compact || isWorkspaceRoute ? undefined : 0"
        :aria-label="compact || isWorkspaceRoute ? undefined : '拖动标题栏移动助手；也可使用方向键调整位置'"
        @mousedown="onDragStart" @touchstart.passive="onDragStart" @keydown="onHeaderKeydown">
        <div class="assistant-float__heading"><span class="assistant-float__title">AI 助手</span><span class="assistant-float__scene">{{ sceneLabel }}</span></div>
        <div class="assistant-float__header-actions">
          <button class="assistant-float__new-session" type="button" title="新建会话，保留旧聊天" aria-label="新建会话"
            :disabled="conversationStore.creating || !conversationStore.owner" @click.stop="newConversation">新建会话</button>
          <button v-if="!compact && !isWorkspaceRoute" class="assistant-float__icon-button" type="button"
            title="恢复默认位置" aria-label="恢复默认位置" @click="resetPosition"><el-icon><Refresh /></el-icon></button>
          <button v-if="!isWorkspaceRoute" class="assistant-float__icon-button" type="button"
            title="收起" aria-label="收起助手" @click="closePanel"><el-icon><Close /></el-icon></button>
        </div>
      </header>
      <AssistantConversationPanel ref="panelRef" :capabilities="activeCapabilities" :capability-failed="capabilityFailed"
        :page-context="pageContext" :suggestions="suggestions" :workspace="isWorkspaceRoute"
        @refresh-capabilities="loadCapabilities" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChatDotRound, Close, Refresh } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
import { assistantApi, featureEnabled, type AssistantCapabilities, type AssistantPageContext } from '@/api/assistant'
import { useAuthStore } from '@/stores/auth'
import { useAssistantConversationsStore } from '@/stores/assistantConversations'
import AssistantConversationPanel from './AssistantConversationPanel.vue'
import {
  buildSuggestions, clampPosition, defaultPosition, isCompactViewport, panelSize,
  readStoredPosition, resolvePageLabel, resolveAssistantRouteModule, resolveRoleLabel,
  resolveAllowedPageFilters, isRegisteredAssistantPath, getAssistantSelectedDocument,
  writeStoredPosition, type PanelPosition, type PanelSize,
  clearAssistantSelectedDocument,
} from './assistantShell'

const authStore = useAuthStore()
const conversationStore = useAssistantConversationsStore()
const route = useRoute()
const open = ref(false)
const compact = ref(false)
const size = ref<PanelSize>(panelSize(1024, 768))
const position = ref<PanelPosition>({ x: 0, y: 0 })
const capabilities = ref<AssistantCapabilities | null>(null)
const capabilityFailed = ref(false)
const launcherRef = ref<HTMLButtonElement | null>(null)
const panelRef = ref<InstanceType<typeof AssistantConversationPanel> | null>(null)
let capabilityRequestId = 0

const isWorkspaceRoute = computed(() => route.name === 'assistant')
const pagePermission = computed(() => route.meta.permission)
const identity = computed(() => [authStore.profile?.company_id, authStore.profile?.staff_id, authStore.token, authStore.permissions.join('|')].join(':'))
const activeCapabilities = computed(() => capabilities.value?.staff_id === authStore.profile?.staff_id ? capabilities.value : null)
const assistantContextPath = computed(() => {
  const permissions = activeCapabilities.value?.permissions ?? authStore.permissions
  const requestedSource = route.query.from
  const candidate = isWorkspaceRoute.value && typeof requestedSource === 'string' && requestedSource !== '/assistant' ? requestedSource : route.path
  return isRegisteredAssistantPath(candidate, permissions) ? candidate : '/dashboard'
})
const canShowAssistant = computed(() => {
  const permission = pagePermission.value
  if (!authStore.isAuthenticated || !authStore.profile?.staff_id || !permission || !authStore.hasPermission(permission)) return false
  const capability = activeCapabilities.value
  if (!capability) return true
  const moduleName = permission.startsWith('module:') ? permission.slice('module:'.length) : null
  return (capability.permissions.includes(permission) || capability.permissions.includes('*')) && (!moduleName || capability.modules.includes(moduleName))
})
const sceneLabel = computed(() => resolveRoleLabel(activeCapabilities.value?.roles ?? []) + ' · ' + resolvePageLabel(assistantContextPath.value))
const pageContext = computed<AssistantPageContext>(() => {
  const path = assistantContextPath.value
  const permissions = activeCapabilities.value?.permissions ?? authStore.permissions
  const staffId = authStore.profile?.staff_id
  const selected = staffId ? getAssistantSelectedDocument(staffId) : null
  const moduleName = resolveAssistantRouteModule(path, permissions)
  const selectedAllowed = selected && (selected.doc_type === 'order' ? moduleName === 'order' : moduleName === 'purchase')
  return { path, filters: isWorkspaceRoute.value ? {} : resolveAllowedPageFilters(route.query), ...(selectedAllowed ? { selected_document: selected } : {}) }
})
const suggestions = computed(() => buildSuggestions(activeCapabilities.value?.modules ?? [], activeCapabilities.value?.permissions ?? [], {
  readQueryEnabled: featureEnabled(activeCapabilities.value, 'read_query'),
  currentModule: resolveAssistantRouteModule(assistantContextPath.value, activeCapabilities.value?.permissions ?? []),
}))
const panelStyle = computed(() => compact.value || isWorkspaceRoute.value ? {} : {
  left: position.value.x + 'px', top: position.value.y + 'px', width: size.value.width + 'px', height: size.value.height + 'px',
})

function syncViewport(): void {
  compact.value = isCompactViewport(window.innerWidth)
  size.value = panelSize(window.innerWidth, window.innerHeight)
  position.value = clampPosition(position.value, size.value, window.innerWidth, window.innerHeight)
  if (open.value && !compact.value && !isWorkspaceRoute.value) persistPosition()
}
function applyInitialPosition(): void {
  const stored = readStoredPosition(authStore.profile?.staff_id)
  position.value = stored ? clampPosition(stored, size.value, window.innerWidth, window.innerHeight) : defaultPosition(size.value, window.innerWidth, window.innerHeight)
}
function persistPosition(): void { if (authStore.profile?.staff_id) writeStoredPosition(authStore.profile.staff_id, position.value) }
function resetPosition(): void { position.value = defaultPosition(size.value, window.innerWidth, window.innerHeight); persistPosition() }
async function openPanel(): Promise<void> { open.value = true; applyInitialPosition(); await nextTick(); panelRef.value?.focusComposer() }
async function closePanel(): Promise<void> { open.value = false; await nextTick(); launcherRef.value?.focus() }
function onPanelEscape(): void { if (!isWorkspaceRoute.value) void closePanel() }
async function newConversation(): Promise<void> { await conversationStore.create(); await nextTick(); panelRef.value?.focusComposer() }
function onHeaderKeydown(event: KeyboardEvent): void {
  if (compact.value || isWorkspaceRoute.value || event.target !== event.currentTarget) return
  const step = event.shiftKey ? 48 : 16
  const next = { ...position.value }
  switch (event.key) {
    case 'ArrowLeft': next.x -= step; break
    case 'ArrowRight': next.x += step; break
    case 'ArrowUp': next.y -= step; break
    case 'ArrowDown': next.y += step; break
    case 'Home': event.preventDefault(); resetPosition(); return
    default: return
  }
  event.preventDefault()
  position.value = clampPosition(next, size.value, window.innerWidth, window.innerHeight)
  persistPosition()
}
interface DragState { startX: number; startY: number; originX: number; originY: number }
let drag: DragState | null = null
function pointOf(event: MouseEvent | TouchEvent): { x: number; y: number } | null {
  if ('touches' in event) {
    const touch = event.touches[0]
    return touch ? { x: touch.clientX, y: touch.clientY } : null
  }
  return { x: event.clientX, y: event.clientY }
}
function onDragStart(event: MouseEvent | TouchEvent): void {
  if (compact.value || isWorkspaceRoute.value || (event.target as HTMLElement).closest('button, a, input, textarea')) return
  if (event instanceof MouseEvent && event.button !== 0) return
  const point = pointOf(event)
  if (!point) return
  drag = { startX: point.x, startY: point.y, originX: position.value.x, originY: position.value.y }
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
  window.addEventListener('touchmove', onDragMove, { passive: false })
  window.addEventListener('touchend', onDragEnd)
  window.addEventListener('touchcancel', onDragEnd)
}
function onDragMove(event: MouseEvent | TouchEvent): void {
  if (!drag) return
  const point = pointOf(event)
  if (!point) return
  if (event.cancelable) event.preventDefault()
  position.value = clampPosition({ x: drag.originX + point.x - drag.startX, y: drag.originY + point.y - drag.startY }, size.value, window.innerWidth, window.innerHeight)
}
function detachDragListeners(): void {
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
  window.removeEventListener('touchmove', onDragMove)
  window.removeEventListener('touchend', onDragEnd)
  window.removeEventListener('touchcancel', onDragEnd)
}
function onDragEnd(): void { drag = null; detachDragListeners(); persistPosition() }
async function loadCapabilities(): Promise<void> {
  const staffId = authStore.profile?.staff_id
  if (!staffId || !authStore.token) return
  const key = identity.value
  const requestId = ++capabilityRequestId
  capabilityFailed.value = false
  try {
    const result = await assistantApi.getCapabilities()
    if (requestId !== capabilityRequestId || key !== identity.value) return
    if (result.staff_id !== staffId) throw new Error('助手能力响应身份不匹配')
    capabilities.value = result
  } catch {
    if (requestId !== capabilityRequestId || key !== identity.value) return
    capabilities.value = null
    capabilityFailed.value = true
  }
}
watch(identity, (_next, previous) => {
  capabilityRequestId += 1
  capabilities.value = null
  capabilityFailed.value = false
  if (previous) clearAssistantSelectedDocument()
  open.value = isWorkspaceRoute.value && canShowAssistant.value
  applyInitialPosition()
  const scheduledIdentity = identity.value
  void nextTick(() => { if (identity.value === scheduledIdentity && canShowAssistant.value) void loadCapabilities() })
}, { flush: 'sync' })
watch(isWorkspaceRoute, (workspace) => { open.value = workspace })
watch(canShowAssistant, (visible) => { if (!visible) open.value = false })
watch(pagePermission, () => { if (canShowAssistant.value && !activeCapabilities.value) void loadCapabilities() })
onMounted(() => {
  syncViewport(); applyInitialPosition()
  open.value = isWorkspaceRoute.value
  window.addEventListener('resize', syncViewport)
  if (canShowAssistant.value) void loadCapabilities()
})
onBeforeUnmount(() => { capabilityRequestId += 1; detachDragListeners(); window.removeEventListener('resize', syncViewport) })
</script>

<style scoped>
.assistant-float__launcher { position: fixed; right: 22px; bottom: 22px; z-index: 1900; display: inline-flex; align-items: center; gap: 6px; padding: 10px 16px; border: 1px solid var(--el-color-primary); border-radius: 999px; background: var(--el-color-primary); color: #fff; font-size: 14px; cursor: pointer; box-shadow: 0 6px 16px rgb(0 0 0 / 16%); }
.assistant-float__panel { position: fixed; z-index: 1900; display: flex; flex-direction: column; min-width: 240px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: var(--el-bg-color); box-shadow: 0 10px 28px rgb(0 0 0 / 16%); overflow: hidden; }
.assistant-float__header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px 8px 14px; border-bottom: 1px solid var(--el-border-color-lighter); background: var(--el-fill-color-light); }
.assistant-float__header.is-draggable { cursor: move; user-select: none; touch-action: none; }
.assistant-float__heading { display: flex; flex-direction: column; min-width: 0; }
.assistant-float__title { font-size: 14px; font-weight: 500; color: var(--el-text-color-primary); }
.assistant-float__scene { font-size: 12px; color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assistant-float__header-actions { display: inline-flex; align-items: center; gap: 2px; }
.assistant-float__new-session { padding: 4px 7px; border: 0; border-radius: 6px; background: transparent; color: var(--el-color-primary); font-size: 12px; cursor: pointer; }
.assistant-float__new-session:hover { background: var(--el-fill-color); }
.assistant-float__new-session:disabled { color: var(--el-text-color-disabled); cursor: not-allowed; }
.assistant-float__icon-button { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: 6px; background: transparent; color: var(--el-text-color-regular); cursor: pointer; }
.assistant-float__icon-button:hover { background: var(--el-fill-color); }
.assistant-float__panel.is-compact { left: 0; right: 0; bottom: 0; width: 100%; height: min(78dvh, 600px); border-radius: 12px 12px 0 0; }
.assistant-float.is-workspace { position: absolute; z-index: 10; inset: 0; display: flex; min-width: 0; min-height: 0; }
.assistant-float__panel.is-workspace { position: absolute; inset: 0; width: 100%; height: 100%; min-width: 0; min-height: 0; border: 0; border-radius: 0; box-shadow: none; }
.assistant-float__panel.is-workspace .assistant-float__header { min-height: 58px; padding: 10px 18px; cursor: default; box-sizing: border-box; }
.assistant-float__panel.is-workspace .assistant-float__title { font-size: 17px; font-weight: 600; }
</style>
