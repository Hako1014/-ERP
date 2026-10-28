<template>
  <section class="assistant-attachments" aria-label="私有附件" @dragover.prevent @drop.prevent.stop="drop" @paste="paste">
    <input ref="picker" type="file" multiple :accept="limits?.extensions.join(',')" aria-label="选择附件" :disabled="disabled || !limits || busy" @change="choose" />
    <button type="button" :disabled="disabled || !limits || busy" @click="picker?.click()">上传文件 / 图片</button>
    <small v-if="limits">可拖放文件或粘贴图片；每个 {{ mib(limits.max_file_bytes) }} MiB，最多 {{ limits.max_files }} 个、合计 {{ mib(limits.max_total_bytes) }} MiB；PDF ≤{{ limits.max_pdf_pages }} 页，图片 ≤{{ limits.max_image_pixels.toLocaleString() }} 像素；XLSX 解压 ≤{{ mib(limits.max_uncompressed_bytes) }} MiB / {{ limits.max_zip_entries }} 项。</small>
    <p v-if="error" role="alert">{{ error }} <button v-if="!limits" type="button" @click="reload">重试加载附件能力</button></p>
    <p v-if="busy" role="status">{{ stage }} <button v-if="uploading" type="button" @click="cancelUpload">取消上传</button></p>
    <AssistantAttachmentList :attachments="pending" removable :disabled="busy || Boolean(attempt)" @remove="remove" />
    <template v-if="pending.length">
      <p>附件仅保存在 ERP 私有会话，尚未发给模型。保存不会调用 AI；保存后可在会话消息中单独选择“识别单据”，届时会再次提示外发内容并要求确认。</p>
      <button type="button" :disabled="disabled || busy" @click="save">{{ attempt ? '核对并重试原保存请求' : '保存文字和附件到会话（不外发）' }}</button>
    </template>
    <small v-if="limits">本人容量 {{ mib(limits.owner_quota_bytes) }} MiB；未保存附件 {{ limits.orphan_hours }} 小时后清理；删除会话后 {{ limits.deleted_retention_days }} 天内清理聊天独占附件。预览不执行公式、宏或 HTML。</small>
  </section>
</template>
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ApiError, getAccessToken } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useAssistantConversationsStore } from '@/stores/assistantConversations'
import { assistantAttachmentsApi as api, type AssistantAttachment, type AttachmentPolicy, type SaveAttachments } from '@/api/assistantAttachments'
import AssistantAttachmentList from './AssistantAttachmentList.vue'
const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ pending: [value: boolean] }>()
const store = useAssistantConversationsStore(), auth = useAuthStore()
const picker = ref<HTMLInputElement | null>(null), limits = ref<AttachmentPolicy | null>(null)
const pending = ref<AssistantAttachment[]>([]), busy = ref(false), uploading = ref(false), stage = ref(''), error = ref('')
const attempt = ref<SaveAttachments | null>(null)
const saving = ref(false)
let controller: AbortController | undefined, revision = 0
let creatingForUpload = false
const mib = (value: number) => Math.round(value / 1024 / 1024)
const identity = () => `${store.owner}:${auth.permissions.join('|')}`
function valid(key: string, rev: number) { return key === identity() && rev === revision && auth.token === getAccessToken() && !controller?.signal.aborted }
function reset() { revision++; controller?.abort(); pending.value = []; busy.value = false; uploading.value = false; saving.value = false; stage.value = ''; error.value = ''; attempt.value = null }
async function reload(): Promise<void> {
  reset(); limits.value = null
  const key = identity(), rev = revision
  if (!store.owner) return
  controller = new AbortController()
  try {
    const result = await api.policy(controller.signal)
    if (!valid(key, rev)) return
    limits.value = result
    if (store.active) {
      const files = await api.pending(store.active.id, controller.signal)
      if (valid(key, rev)) pending.value = files
    }
  } catch (failure) { if (valid(key, rev)) error.value = failure instanceof Error ? failure.message : '附件加载失败' }
}
async function add(files: File[]): Promise<void> {
  if (!files.length || props.disabled || busy.value || !limits.value || attempt.value) return
  const rule = limits.value
  const existingSize = pending.value.reduce((sum, file) => sum + file.size, 0)
  if (files.some((file) => !rule.extensions.includes(`.${file.name.split('.').at(-1)?.toLowerCase()}`) || file.size === 0 || file.size > rule.max_file_bytes) ||
      files.length + pending.value.length > rule.max_files || files.reduce((sum, file) => sum + file.size, existingSize) > rule.max_total_bytes) {
    error.value = '文件格式、数量或大小超限，请按显示的限制选择；文件尚未上传。'; return
  }
  error.value = ''; busy.value = true; uploading.value = true
  const key = identity(), rev = revision
  controller?.abort(); controller = new AbortController()
  try {
    if (!store.active) {
      creatingForUpload = true
      const created = await store.create()
      creatingForUpload = false
      if (!created) throw new Error('请先创建或恢复会话后上传')
    }
    if (!valid(key, rev) || !store.active) return
    const conversation = store.active.id
    for (const file of files) {
      stage.value = `正在上传 ${file.name}：0%`
      const result = await api.upload(conversation, file, controller, (value) => { if (valid(key, rev)) stage.value = `正在上传 ${file.name}：${value}%（服务端校验后才算成功）` })
      if (!valid(key, rev) || store.active?.id !== conversation) return
      pending.value.push(result)
    }
    stage.value = '上传完成，尚未外发模型'
  } catch (failure) {
    if (valid(key, rev)) error.value = failure instanceof Error ? failure.message : '上传失败，请重试'
  } finally { creatingForUpload = false; if (rev === revision) { busy.value = false; uploading.value = false } }
}
function choose(event: Event) { const target = event.target as HTMLInputElement; void add(Array.from(target.files || [])); target.value = '' }
function drop(event: DragEvent) { void add(Array.from(event.dataTransfer?.files || [])) }
function paste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith('image/'))
  if (files.length) { event.preventDefault(); void add(files) }
}
function cancelUpload() { revision++; controller?.abort(); busy.value = false; uploading.value = false; stage.value = ''; error.value = '上传已取消。服务器可能已完成最后一个文件，恢复会话可核对；未关联文件将自动清理。' }
async function remove(file: AssistantAttachment): Promise<void> {
  if (busy.value || attempt.value) return
  const key = identity(), rev = revision
  busy.value = true; controller = new AbortController()
  try { await api.remove(file.id, file.conversation_id, controller.signal); if (valid(key, rev)) pending.value = pending.value.filter((row) => row.id !== file.id) }
  catch (failure) { if (valid(key, rev)) error.value = failure instanceof Error ? failure.message : '移除失败' }
  finally { if (rev === revision) busy.value = false }
}
async function save(): Promise<void> {
  if (!store.active || !pending.value.length || busy.value || props.disabled) return
  const key = identity(), rev = revision
  attempt.value ||= { conversation_id: store.active.id, version: store.active.version, request_id: crypto.randomUUID(),
    client_message_id: crypto.randomUUID(), attachment_ids: pending.value.map((file) => file.id), content: store.draft.trim() }
  const payload = attempt.value
  busy.value = true; saving.value = true; error.value = ''; stage.value = '正在保存到会话，不调用模型…'; controller = new AbortController()
  try {
    const result = await api.save(payload, controller.signal)
    if (!valid(key, rev)) return
    if (store.draft.trim() === payload.content) store.draft = ''
    pending.value = []; attempt.value = null
    await store.attachmentsSaved(payload.conversation_id, result.version)
  } catch (failure) {
    if (!valid(key, rev)) return
    if (failure instanceof ApiError && [404, 409, 422].includes(failure.status || 0)) {
      // 服务端明确拒绝而非网络未知，不把陈旧版本锁死为永远不能重试。
      attempt.value = null
      await store.refresh(true)
      await reload()
      error.value = `${failure.message}。已恢复保存状态，请核对后重新保存。`
    } else error.value = `${failure instanceof Error ? failure.message : '保存失败'}。结果未确认，可用原请求重试，或恢复历史核对。`
  }
  finally { if (rev === revision) { busy.value = false; saving.value = false } }
}
watch(() => [identity(), store.active?.id] as const, (value, old) => { if (!creatingForUpload || value[0] !== old?.[0]) void reload() }, { immediate: true, flush: 'sync' })
watch(() => [store.active?.id, store.active?.version] as const, (value, old) => {
  if (value[0] && value[0] === old[0] && value[1] !== old[1] && !saving.value) void reload()
}, { flush: 'sync' })
watch(() => pending.value.length > 0 || busy.value, (value) => emit('pending', value), { immediate: true })
onBeforeUnmount(reset)
defineExpose({ paste, drop })
</script>
<style scoped>
.assistant-attachments { padding: 6px 12px; border-top: 1px dashed var(--el-border-color); max-height: 220px; overflow-y: auto; font-size: 11px; }
input { display: none; } small { display: block; color: var(--el-text-color-secondary); line-height: 1.6; }
button { cursor: pointer; background: transparent; border: 1px solid var(--el-border-color); border-radius: 4px; padding: 4px 7px; color: var(--el-color-primary); }
button:disabled { opacity: .5; cursor: not-allowed; } p { margin: 5px 0; line-height: 1.6; overflow-wrap: anywhere; }
</style>
