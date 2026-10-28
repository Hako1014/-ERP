<template>
  <div class="assistant-conversation-panel" :class="{ 'has-sidebar': wide }">
    <AssistantConversationList v-if="wide" @select="selectConversation" @action="conversationAction" />
    <el-drawer v-else v-model="historyOpen" title="我的会话 · 仅本人可见" direction="ltr" size="min(320px, 90vw)" :append-to-body="true">
      <AssistantConversationList @select="selectConversation" @action="conversationAction" />
    </el-drawer>
    <div class="assistant-conversation-panel__chat" @dragover.prevent @drop.prevent="attachments?.drop($event)">
      <div class="assistant-conversation-panel__toolbar">
        <el-button v-if="!wide" size="small" aria-label="打开历史会话" @click="historyOpen = true">历史会话</el-button>
        <span class="assistant-conversation-panel__name">{{ store.active?.title || '新会话' }}</span>
        <el-button size="small" :disabled="store.streaming || store.messagesLoading" @click="store.refresh(true)">恢复保存状态</el-button>
        <el-button v-if="store.active" size="small" :disabled="store.generating" @click="conversationAction(store.active, 'clear')">清空</el-button>
      </div>
      <p v-if="serviceNotice" class="assistant-float__notice" role="status">{{ serviceNotice }}</p>
      <p v-if="store.notice" class="assistant-float__notice" role="status">{{ store.notice }}</p>
      <p v-if="store.messageError" class="assistant-float__notice" role="alert">{{ store.messageError }} <el-button link @click="store.active ? store.refreshCurrent() : store.initialize()">重试加载</el-button></p>
      <p v-if="store.active?.status === 'ARCHIVED'" class="assistant-float__notice">本会话已归档，可查看历史。<el-button link @click="conversationAction(store.active!, 'archive')">恢复后继续对话</el-button></p>
      <p v-if="store.highlight" class="assistant-conversation-panel__search-note">搜索关键词“{{ store.highlight }}” <el-button link @click="store.refreshCurrent(true)">回到最新</el-button></p>
      <div ref="messageList" class="assistant-float__messages" role="log" :aria-live="store.streaming ? 'polite' : 'off'" :aria-busy="store.messagesLoading" @scroll="saveViewport">
        <el-button v-if="store.nextBeforeSeq" size="small" :disabled="store.generating || store.messagesLoading" @click="store.refreshCurrent(true, store.nextBeforeSeq!)">更早一页</el-button>
        <el-button v-if="!store.latestPage && !store.highlight" size="small" :disabled="store.messagesLoading" @click="store.refreshCurrent(true)">返回最新消息</el-button>
        <p v-if="store.messagesLoading" role="status">正在加载已保存的消息…</p>
        <p v-if="!store.messages.length && !store.messagesLoading" class="assistant-float__empty">{{ store.active ? '这段会话还没有消息，可以从推荐问题开始。' : '选择历史会话，或新建会话开始提问。聊天仅本人可见。' }}</p>
        <article v-for="message in store.messages" :key="message.id" :data-message-seq="message.seq" class="assistant-float__message" :class="[`is-${message.role}`, { 'is-hidden': message.hidden, 'is-match': message.seq === store.matchedSeq }]" tabindex="-1">
          <div class="assistant-conversation-panel__message-header"><strong>{{ message.role === 'user' ? '我' : 'AI 助手' }}</strong><time>{{ assistantTime(message.created_at) }}</time></div>
          <small v-if="message.page_context && !message.hidden" class="assistant-float__message-scene">{{ resolvePageLabel(message.page_context.path) }}</small>
          <p><template v-for="(part, index) in highlightedText(message.content || (store.streaming && message.role === 'assistant' ? store.stage : ''), message.hidden ? '' : store.highlight)" :key="index"><mark v-if="part.matched">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></p>
          <small v-if="message.role === 'assistant' && message.status !== 'COMPLETED'" class="assistant-float__stopped">{{ messageStatus(message) }}</small>
          <template v-if="!message.hidden">
            <AssistantAttachmentList v-if="message.attachments?.length" :attachments="message.attachments" recognizable :allow-draft-creation="featureEnabled(capabilities, 'draft_entry')" @create-draft="createDraftFromExtraction" />
            <template v-for="source in message.sources" :key="`${source.type}-${source.title}-${source.href}`">
              <router-link v-if="source.href" class="assistant-float__source" :to="source.href">{{ sourceLabel(source) }}</router-link>
              <span v-else class="assistant-float__source">{{ sourceLabel(source) }}</span>
            </template>
            <button v-if="message.content" class="assistant-float__copy" type="button" aria-label="复制这条消息" @click="copy(message.content)">复制</button>
            <button v-if="message.role === 'assistant' && ['FAILED', 'INTERRUPTED'].includes(message.status)" class="assistant-float__copy" type="button" :disabled="store.generating" @click="retryQuestion(message)">重试问题</button>
          </template>
        </article>
      </div>
      <p v-if="store.unresolved" class="assistant-float__notice">发送结果尚未确认。先恢复状态；仍未保存时使用原请求重试。<el-button link :disabled="store.streaming || store.preparing || store.messagesLoading" @click="store.retryUnresolved()">核对并重试提交</el-button></p>
      <p v-else-if="store.generating && !store.streaming" class="assistant-float__notice">服务器保存了生成中的记录。可等待、恢复状态或停止，不会自动重发问题。</p>
      <div v-if="suggestions.length" class="assistant-float__suggestions">
        <button v-for="item in suggestions" :key="item.code" class="assistant-float__suggestion" type="button" :disabled="!store.canCompose" @click="store.draft = item.prompt">{{ item.label }}</button>
      </div>
      <AssistantAttachments ref="attachments" :disabled="!store.canCompose" @pending="hasAttachments = $event" />
      <p v-if="hasAttachments" class="assistant-conversation-panel__hint">请先保存或移除待处理附件；普通“发送”不会自动读取或外发文件。</p>
      <footer class="assistant-float__composer">
        <div class="assistant-conversation-panel__input-wrap">
          <textarea ref="input" v-model="store.draft" class="assistant-float__input" rows="2" aria-label="输入问题" maxlength="4000" placeholder="输入问题；Enter 发送，Shift+Enter 换行" :disabled="!store.canCompose" @keydown.enter="onEnter" @paste="attachments?.paste($event)" />
          <small>{{ store.draft.length }}/4000 · {{ store.active?.retention_days ? `会话保存 ${store.active.retention_days} 天` : '会话保存至本人删除' }}</small>
        </div>
        <button class="assistant-float__send" type="button" :disabled="store.generating ? store.stopping : !canSend" @click="onSend">{{ store.generating ? (store.stopping ? '正在停止…' : '停止') : '发送' }}</button>
      </footer>
      <p v-if="!store.latestPage" class="assistant-conversation-panel__hint">正在查看历史页；返回最新消息后可继续提问。</p>
      <div v-if="featureEnabled(capabilities, 'draft_entry')" class="assistant-float__draft-entry">
        <AssistantDraftEntry ref="draftEntry" :session="store.runtime" :ensure-session="store.ensureRuntime" :conversation-id="store.active?.id || null" :ensure-conversation="ensureDraftConversation" :service-status="capabilities?.service_status || 'unavailable'" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAssistantConversationsStore } from '@/stores/assistantConversations'
import { ACCESS_TOKEN_KEY } from '@/api/client'
import { featureEnabled, type AssistantCapabilities, type AssistantPageContext } from '@/api/assistant'
import type { Conversation, ConversationMessage } from '@/api/assistantConversations'
import { copyText, resolvePageLabel, type AssistantSuggestion } from './assistantShell'
import { assistantTime, highlightedText, isConversationId, sourceLabel } from './assistantConversationHelpers'
import AssistantDraftEntry from './AssistantDraftEntry.vue'
import AssistantConversationList from './AssistantConversationList.vue'
import AssistantAttachments from './AssistantAttachments.vue'
import AssistantAttachmentList from './AssistantAttachmentList.vue'
import type { AssistantExtraction } from '@/api/assistantAttachments'

const props = defineProps<{ capabilities: AssistantCapabilities | null; capabilityFailed: boolean; pageContext: AssistantPageContext; suggestions: AssistantSuggestion[]; workspace: boolean }>()
const emit = defineEmits<{ refreshCapabilities: [] }>()
const store = useAssistantConversationsStore()
const attachments = ref<InstanceType<typeof AssistantAttachments> | null>(null)
const draftEntry = ref<InstanceType<typeof AssistantDraftEntry> | null>(null)
const hasAttachments = ref(false)
const auth = useAuthStore()
const route = useRoute()
const historyOpen = ref(false)
const narrow = ref(window.innerWidth < 1100)
const wide = computed(() => props.workspace && !narrow.value)
const input = ref<HTMLTextAreaElement | null>(null)
const messageList = ref<HTMLElement | null>(null)
const followTail = ref(true)
const canSend = computed(() => !hasAttachments.value && props.capabilities?.service_status === 'ready' && store.canCompose && Boolean(store.draft.trim()))
const serviceNotice = computed(() => {
  if (props.capabilityFailed) return '助手能力加载失败，请稍后重试。'
  if (!props.capabilities) return '正在加载助手能力…'
  if (props.capabilities.provider_mode === 'mock') return '本地模拟模式：回答由 MockProvider 生成，不是 DeepSeek 内容，也不会查询 ERP 数据。'
  if (props.capabilities.service_status === 'unconfigured') return 'AI 服务未配置，请联系管理员；仍可查阅已保存的历史。'
  if (props.capabilities.service_status === 'unavailable') return 'AI 服务暂不可用，仍可查阅已保存的历史。'
  if (!store.owner) return '正在加载当前账号的公司信息…'
  return store.streaming ? store.stage : ''
})

function onSend(): void { if (store.generating) void store.stop(); else if (canSend.value) void store.send(store.draft, props.pageContext) }
async function ensureDraftConversation(): Promise<string | null> {
  if (store.active?.id) return store.active.id
  return await store.create() ? store.active?.id || null : null
}
async function createDraftFromExtraction(extraction: AssistantExtraction): Promise<void> {
  if (store.active?.id !== extraction.conversation_id) {
    store.notice = '请打开附件所属的会话后再生成单据草稿。'
    return
  }
  await draftEntry.value?.createFromExtraction(extraction)
}
function onEnter(event: KeyboardEvent): void {
  if (event.isComposing || event.keyCode === 229 || event.shiftKey) return
  event.preventDefault()
  onSend()
}
function messageStatus(message: ConversationMessage): string {
  return message.status === 'FAILED' ? '生成失败，问题已保留。' : message.status === 'INTERRUPTED' ? '生成中断，已保存收到的内容。' : store.streaming ? store.stage : '生成状态尚未结束，可恢复保存状态或停止。'
}
async function copy(text: string): Promise<void> { if (await copyText(text)) ElMessage.success('已复制') }
async function retryQuestion(message: ConversationMessage): Promise<void> {
  const question = store.messages.find((row) => row.role === 'user' && row.request_id === message.request_id)
  if (!question) { store.notice = '请打开包含原问题的历史页，再手动提问。'; return }
  if (!store.latestPage) await store.refreshCurrent(true)
  store.draft = question.content
  store.notice = '已填入原问题，请核对当前页面场景后点击发送。'
  await nextTick(); input.value?.focus()
}
async function selectConversation(record: Conversation): Promise<void> {
  saveViewport()
  historyOpen.value = false
  await store.select(record.id, { seq: record.matched_message_seq, query: store.query.trim() })
}
async function conversationAction(record: Conversation, command: string): Promise<void> {
  try {
    let success = false
    if (command === 'rename') {
      const result = await ElMessageBox.prompt('请输入新的会话名称', '重命名会话', { inputValue: record.title, inputValidator: (value) => Boolean(value?.trim() && value.trim().length <= 120) || '名称须为 1～120 个字符', confirmButtonText: '保存', cancelButtonText: '取消' })
      success = await store.mutate(record, { title: result.value.trim() })
    } else if (command === 'pin') success = await store.mutate(record, { is_pinned: !record.is_pinned })
    else if (command === 'archive') success = await store.mutate(record, { status: record.status === 'ARCHIVED' ? 'ACTIVE' : 'ARCHIVED' })
    else if (command === 'delete' || command === 'clear') {
      await ElMessageBox.confirm(command === 'delete' ? '删除后会话与消息立即从历史隐藏。确认删除？' : '清空后消息从本会话隐藏，此操作无法在界面撤销。确认清空？', command === 'delete' ? '删除会话' : '清空会话', { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' })
      success = await store.mutate(record, command)
    }
    if (success) ElMessage.success(command === 'delete' ? '会话已删除' : command === 'clear' ? '消息已清空' : command === 'archive' ? '会话归档状态已更新' : '会话信息已更新')
  } catch (error) { if (error !== 'cancel' && error !== 'close') store.notice = error instanceof Error ? error.message : '会话操作失败，请重试' }
}
async function focusMessages(): Promise<void> {
  await nextTick()
  if (store.messagesLoading) return
  const target = store.matchedSeq ? messageList.value?.querySelector<HTMLElement>(`[data-message-seq="${store.matchedSeq}"]`) : null
  if (target) { target.scrollIntoView?.({ block: 'center' }); target.focus({ preventScroll: true }) }
  else if (messageList.value && store.restoreScrollTop !== null) {
    messageList.value.scrollTop = store.restoreScrollTop
    store.restoreScrollTop = null
    followTail.value = messageList.value.scrollTop + messageList.value.clientHeight >= messageList.value.scrollHeight - 32
  } else if (messageList.value && store.latestPage && followTail.value) messageList.value.scrollTop = messageList.value.scrollHeight
}
function saveViewport(): void {
  if (messageList.value) {
    followTail.value = messageList.value.scrollTop + messageList.value.clientHeight >= messageList.value.scrollHeight - 32
    store.rememberViewport(messageList.value.scrollTop)
  }
}

let channel: BroadcastChannel | null = null
let timer: ReturnType<typeof setTimeout> | undefined
const channelKey = computed(() => auth.profile?.company_id && auth.profile.staff_id ? `erp-assistant-${auth.profile.company_id}-${auth.profile.staff_id}` : '')
function broadcast(): void {
  const event = { type: 'refresh', company: auth.profile?.company_id, staff: auth.profile?.staff_id, conversation: store.changedConversationId }
  channel?.postMessage(event)
  if (!channel) {
    try { window.localStorage.setItem(`${channelKey.value}-changed`, JSON.stringify({ ...event, revision: crypto.randomUUID() })) } catch { /* 同步不可用时仍可手动恢复。 */ }
  }
}
function refreshVisible(): void { emit('refreshCapabilities'); void store.refresh(true) }
function resized(): void { narrow.value = window.innerWidth < 1100 }
function visibilityChanged(): void { if (document.visibilityState === 'visible') refreshVisible() }
function storageChanged(event: StorageEvent): void { if ([ACCESS_TOKEN_KEY, 'erp_auth_profile', `${channelKey.value}-changed`].includes(event.key || '')) refreshVisible() }
watch(() => props.capabilities?.permissions || auth.permissions, (codes) => store.setPermissions(codes), { immediate: true })
watch(() => [store.owner, store.initialized] as const, () => {
  if (store.owner && !store.initialized) {
    void store.initialize(isConversationId(route.query.conversation) ? route.query.conversation : undefined)
  }
}, { immediate: true })
watch(() => [store.messages.length, store.matchedSeq, store.messages.at(-1)?.content] as const, () => { void focusMessages() })
watch(() => store.active?.id, () => { followTail.value = true })
watch(() => store.streaming, (running) => { if (running) followTail.value = true })
watch(() => store.changeRevision, () => broadcast())
watch(() => [store.generating, store.streaming, store.messagesLoading] as const, () => {
  clearTimeout(timer)
  if (store.generating && !store.streaming && !store.messagesLoading) timer = setTimeout(() => { void store.refreshCurrent(true) }, 5000)
})
watch(channelKey, (key) => {
  channel?.close(); channel = null
  if (key && typeof BroadcastChannel !== 'undefined') {
    channel = new BroadcastChannel(key)
    channel.onmessage = (event: MessageEvent) => {
      if (event.data?.type === 'refresh' && event.data.company === auth.profile?.company_id && event.data.staff === auth.profile?.staff_id) refreshVisible()
    }
  }
}, { immediate: true })
onMounted(() => {
  window.addEventListener('resize', resized)
  window.addEventListener('focus', refreshVisible)
  window.addEventListener('storage', storageChanged)
  document.addEventListener('visibilitychange', visibilityChanged)
  input.value?.focus()
})
onBeforeUnmount(() => {
  saveViewport()
  clearTimeout(timer); channel?.close()
  window.removeEventListener('resize', resized)
  window.removeEventListener('focus', refreshVisible)
  window.removeEventListener('storage', storageChanged)
  document.removeEventListener('visibilitychange', visibilityChanged)
  store.disposeView()
})
defineExpose({ focusComposer: () => input.value?.focus() })
</script>

<style scoped>
.assistant-conversation-panel { flex: 1; display: flex; min-height: 0; min-width: 0; overflow: hidden; }
.assistant-conversation-panel.has-sidebar > :first-child { flex: 0 0 250px; width: 250px; box-sizing: border-box; border-right: 1px solid var(--el-border-color-lighter); }
.assistant-conversation-panel__chat { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.assistant-conversation-panel__toolbar { display: flex; align-items: center; gap: 6px; padding: 8px 12px; border-bottom: 1px solid var(--el-border-color-lighter); }
.assistant-conversation-panel__name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.assistant-float__messages { flex: 1; min-height: 0; padding: 14px; overflow-y: auto; }
.assistant-float__notice, .assistant-conversation-panel__search-note { margin: 0; padding: 6px 12px; background: var(--el-color-warning-light-9); color: var(--el-text-color-regular); font-size: 12px; line-height: 1.7; }
.assistant-float__empty { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.8; }
.assistant-float__message { margin: 12px 0; padding: 12px; border-radius: 8px; font-size: 13px; line-height: 1.7; background: var(--el-fill-color-light); }
.assistant-float__message.is-user { background: var(--el-color-primary-light-9); }
.assistant-float__message.is-hidden { color: var(--el-text-color-secondary); border: 1px dashed var(--el-border-color); }
.assistant-float__message.is-match { outline: 2px solid var(--el-color-warning); }
.assistant-float__message p { margin: 6px 0; white-space: pre-wrap; word-break: break-word; }
.assistant-conversation-panel__message-header { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; color: var(--el-text-color-secondary); }
.assistant-float__message-scene, .assistant-float__stopped { display: block; color: var(--el-text-color-secondary); font-size: 11px; }
.assistant-float__source { display: block; color: var(--el-color-primary); font-size: 12px; margin-top: 5px; }
.assistant-float__copy, .assistant-float__suggestion { cursor: pointer; color: var(--el-color-primary); background: transparent; border: 0; font: inherit; font-size: 12px; }
.assistant-float__copy { margin-right: 12px; }
.assistant-float__suggestions { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px; }
.assistant-float__suggestion { border: 1px solid var(--el-border-color); border-radius: 16px; padding: 4px 9px; color: var(--el-text-color-regular); }
.assistant-float__composer { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--el-border-color-lighter); }
.assistant-conversation-panel__input-wrap { flex: 1; min-width: 0; }
.assistant-conversation-panel__input-wrap small { color: var(--el-text-color-secondary); font-size: 10px; }
.assistant-float__input { width: 100%; resize: vertical; min-height: 52px; max-height: 140px; box-sizing: border-box; padding: 8px; border: 1px solid var(--el-border-color); border-radius: 6px; font: inherit; font-size: 13px; background: var(--el-bg-color); color: var(--el-text-color-primary); }
.assistant-float__send { align-self: center; cursor: pointer; padding: 8px 12px; border: 0; border-radius: 6px; background: var(--el-color-primary); color: white; white-space: nowrap; }
button:disabled, textarea:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible, textarea:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.assistant-float__draft-entry { padding: 0 12px 8px; }
.assistant-conversation-panel__hint { margin: 0; padding: 0 12px 8px; font-size: 11px; color: var(--el-text-color-secondary); }
@media (max-width: 640px) { .assistant-conversation-panel__toolbar { flex-wrap: wrap; } .assistant-conversation-panel__name { width: 100%; } .assistant-float__composer { padding-bottom: max(10px, env(safe-area-inset-bottom)); } }
</style>
