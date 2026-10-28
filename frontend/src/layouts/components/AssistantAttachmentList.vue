<template>
  <div class="assistant-attachment-list">
    <div v-for="file in attachments" :key="file.id" class="assistant-attachment-list__item">
      <span>{{ file.display_name }} · {{ Math.ceil(file.size / 1024) }} KiB</span>
      <button type="button" @click="open(file)">预览</button>
      <button type="button" @click="download(file)">下载</button>
      <button v-if="recognizable && file.message_id" type="button" :disabled="recognitionBusy" @click="openRecognition(file)">识别单据</button>
      <button v-if="removable" type="button" :disabled="disabled" @click="$emit('remove', file)">移除</button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <el-dialog v-model="visible" title="私有附件预览 · 仅本人" width="min(700px, 95vw)" append-to-body @closed="close">
      <p v-if="loading">正在授权加载…</p>
      <img v-if="imageUrl" :src="imageUrl" alt="附件图片预览" class="assistant-attachment-list__image" />
      <pre v-if="preview?.kind === 'text'">{{ preview.text }}</pre>
      <table v-if="preview?.kind === 'table'"><tbody><tr v-for="(row, index) in preview.rows" :key="index"><td v-for="(cell, col) in row" :key="col">{{ cell }}</td></tr></tbody></table>
      <p v-if="preview?.pages">共 {{ preview.pages }} 页</p>
      <p>{{ preview?.note }}</p><p v-if="preview?.truncated">这是部分预览，未展示内容请下载核对，原文件未截断。</p>
      <small>下载的是原文件，请勿在本地查看软件中启用不可信文件的脚本、宏、外部链接或公式。</small>
    </el-dialog>
    <el-dialog v-model="recognitionVisible" title="附件识别 · 发送前确认" width="min(720px, 95vw)" append-to-body @closed="closeRecognition">
      <p v-if="recognitionLoading" role="status">正在读取本地页码和工作表信息…</p>
      <template v-if="recognitionOptions">
        <el-alert v-if="!recognitionOptions.external_send_allowed" type="warning" :closable="false" title="公司策略未允许附件外发。可在公司设置 → AI 助手配置中启用后再识别。" />
        <template v-if="recognitionOptions.kind === 'pdf' && recognitionOptions.scan_pages.length">
          <p>此 PDF 共 {{ recognitionOptions.page_count }} 页；以下扫描页需要你选择。可提取文字的页面也会一并处理。</p>
          <el-checkbox-group v-model="selectedPages" aria-label="选择要识别的扫描页">
            <el-checkbox v-for="page in recognitionOptions.scan_pages" :key="page" :value="page">第 {{ page }} 页</el-checkbox>
          </el-checkbox-group>
        </template>
        <el-form v-if="recognitionOptions.kind === 'xlsx' && recognitionOptions.sheets.length > 1" label-position="top">
          <el-form-item label="选择一个工作表（公式只读取文件中已有的显示值，不会计算）">
            <el-select v-model="selectedSheet" placeholder="请选择工作表" style="width: 100%">
              <el-option v-for="sheet in recognitionOptions.sheets" :key="sheet" :label="sheet" :value="sheet" />
            </el-select>
          </el-form-item>
        </el-form>
        <p>将使用当前公司启用的 DeepSeek 模型。该操作不会创建或审批业务单据。</p>
        <el-checkbox v-model="externalConfirmed" :disabled="!recognitionOptions.external_send_allowed || recognitionBusy">
          我确认将“{{ recognitionFile?.display_name }}”{{ selectedPages.length ? `的第 ${selectedPages.join('、')} 页及可提取文字` : '' }}发送给 DeepSeek 识别
        </el-checkbox>
        <div class="assistant-extraction__actions">
          <el-button v-if="recognitionBusy" type="warning" @click="cancelRecognition">取消识别</el-button>
          <el-button v-else type="primary" :disabled="!canStartRecognition" @click="runRecognition">确认发送并识别</el-button>
        </div>
        <p v-if="recognitionError" role="alert">{{ recognitionError }}</p>
        <el-divider v-if="recognitionHistory.length" content-position="left">本附件最近识别记录</el-divider>
        <article v-for="item in recognitionHistory" :key="item.id" class="assistant-extraction__result">
          <p><strong>{{ extractionStatus(item.status) }}</strong><small v-if="item.model"> · {{ item.model }}</small></p>
          <p v-if="item.error_category" role="alert">{{ extractionFailure(item.error_category) }}</p>
          <template v-if="item.result">
            <p>识别类型：{{ documentType(item.result.document_type) }}<span v-if="!item.result.document_type_supported">（当前不支持自动生成此类单据草稿）</span><span v-else-if="!item.result.supported_for_draft">（识别信息不足，暂不能生成草稿）</span></p>
            <p v-if="item.result.missing_fields.length">待核对/补充：{{ item.result.missing_fields.join('、') }}</p>
            <p v-if="item.result.extracted_text"><strong>图片/PDF识别文字</strong></p>
            <pre v-if="item.result.extracted_text">{{ item.result.extracted_text }}</pre>
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item v-for="(field, key) in item.result.fields" :key="key" :label="fieldLabel(String(key))">
                {{ field.value || '未识别' }} <small v-if="field.source_ref"> · 来源 {{ sourceLocation(item, field.source_ref) }}</small>
              </el-descriptions-item>
            </el-descriptions>
            <el-table v-if="item.result.items.length" :data="item.result.items" size="small" border class="assistant-extraction__table">
              <el-table-column label="商品" min-width="160"><template #default="{ row }">{{ row.fields.product_name.value || row.fields.product_code.value || '未识别' }}</template></el-table-column>
              <el-table-column label="规格" min-width="120"><template #default="{ row }">{{ row.fields.spec_name.value || row.fields.spec_code.value || '—' }}</template></el-table-column>
              <el-table-column label="单位" width="85"><template #default="{ row }">{{ row.fields.unit_name.value || '—' }}</template></el-table-column>
              <el-table-column label="数量" width="90"><template #default="{ row }">{{ row.fields.quantity.value || '未识别' }}</template></el-table-column>
              <el-table-column label="单价" width="90"><template #default="{ row }">{{ row.fields.purchase_price.value || '—' }}</template></el-table-column>
              <el-table-column label="来源" min-width="130"><template #default="{ row }">{{ sourceLocation(item, row.source_ref) }}</template></el-table-column>
            </el-table>
            <p class="assistant-extraction__notice">{{ item.result.notice }}</p>
            <details v-if="item.result.source_text || item.result.sources.length">
              <summary>查看提取文字与来源</summary>
              <pre v-if="item.result.source_text">{{ item.result.source_text }}</pre>
              <ul><li v-for="source in item.result.sources" :key="source.ref">{{ source.ref }} · {{ source.location }}<span v-if="source.text">：{{ source.text }}</span></li></ul>
            </details>
            <div class="assistant-extraction__actions">
              <el-button
                v-if="allowDraftCreation && item.status === 'COMPLETED' && item.result.supported_for_draft"
                type="primary"
                @click="$emit('create-draft', item)"
              >生成可编辑采购计划草稿</el-button>
            </div>
            <small>用量：{{ item.result.usage.provider_total_tokens ?? '未知' }} token；图片 {{ item.result.usage.image_count }} 张/页。{{ item.result.usage.image_usage_note }}</small>
          </template>
        </article>
      </template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useAssistantConversationsStore } from '@/stores/assistantConversations'
import { useAuthStore } from '@/stores/auth'
import { assistantAttachmentsApi as api, type AssistantAttachment, type AssistantExtraction, type AttachmentPreview, type RecognitionOptions } from '@/api/assistantAttachments'
const props = withDefaults(defineProps<{ attachments: AssistantAttachment[]; removable?: boolean; disabled?: boolean; recognizable?: boolean; allowDraftCreation?: boolean }>(), { allowDraftCreation: false })
defineEmits<{ remove: [file: AssistantAttachment]; 'create-draft': [extraction: AssistantExtraction] }>()
const store = useAssistantConversationsStore(), auth = useAuthStore()
const visible = ref(false), loading = ref(false), error = ref(''), imageUrl = ref('')
const preview = ref<AttachmentPreview | null>(null)
const recognitionVisible = ref(false), recognitionLoading = ref(false), recognitionBusy = ref(false)
const recognitionOptions = ref<RecognitionOptions | null>(null), recognitionHistory = ref<AssistantExtraction[]>([])
const recognitionFile = ref<AssistantAttachment | null>(null), selectedPages = ref<number[]>([]), selectedSheet = ref('')
const externalConfirmed = ref(false), recognitionError = ref(''), activeExtractionId = ref('')
const canStartRecognition = computed(() => {
  const choices = recognitionOptions.value
  return Boolean(choices?.external_send_allowed && externalConfirmed.value && !recognitionBusy.value &&
    !(choices.kind === 'pdf' && choices.scan_pages.length && !selectedPages.value.length) &&
    !(choices.kind === 'xlsx' && choices.sheets.length > 1 && !selectedSheet.value))
})
let controller: AbortController | undefined, revision = 0
const urls = new Set<string>()
function release(): void { for (const url of urls) URL.revokeObjectURL(url); urls.clear(); imageUrl.value = '' }
function close(): void { revision++; controller?.abort(); release(); preview.value = null; loading.value = false; visible.value = false }
function closeRecognition(): void {
  if (recognitionBusy.value) void cancelRecognition()
  recognitionVisible.value = false; recognitionOptions.value = null; recognitionHistory.value = []
  recognitionFile.value = null; selectedPages.value = []; selectedSheet.value = ''; externalConfirmed.value = false
  recognitionError.value = ''; activeExtractionId.value = ''
}
function start() { close(); error.value = ''; controller = new AbortController(); return { current: revision, key: store.owner, signal: controller.signal } }
function valid(run: ReturnType<typeof start>): boolean { return run.current === revision && run.key === store.owner && !run.signal.aborted }
function blobUrl(blob: Blob): string { const url = URL.createObjectURL(blob); urls.add(url); return url }
async function open(file: AssistantAttachment): Promise<void> {
  const run = start(); visible.value = true; loading.value = true
  try {
    const result = await api.preview(file.id, file.conversation_id, run.signal)
    if (!valid(run)) return
    preview.value = result
    if (result.kind === 'image') {
      const blob = await api.content(file.id, file.conversation_id, controller!)
      if (valid(run)) imageUrl.value = blobUrl(blob)
    }
  } catch (failure) { if (valid(run)) error.value = failure instanceof Error ? failure.message : '附件加载失败，请重试' }
  finally { if (valid(run)) loading.value = false }
}
async function download(file: AssistantAttachment): Promise<void> {
  const run = start()
  try {
    const blob = await api.content(file.id, file.conversation_id, controller!)
    if (!valid(run)) return
    const url = blobUrl(blob)
    const link = document.createElement('a'); link.href = url; link.download = file.display_name
    link.click(); setTimeout(() => { URL.revokeObjectURL(url); urls.delete(url) }, 1000)
  } catch (failure) { if (valid(run)) error.value = failure instanceof Error ? failure.message : '下载失败，请重试' }
}
async function openRecognition(file: AssistantAttachment): Promise<void> {
  close(); error.value = ''; recognitionFile.value = file; recognitionVisible.value = true; recognitionLoading.value = true
  const run = start()
  try {
    const [choices, history] = await Promise.all([
      api.recognitionOptions(file.id, file.conversation_id, run.signal),
      api.extractions(file.id, file.conversation_id, run.signal),
    ])
    if (!valid(run)) return
    recognitionOptions.value = choices; recognitionHistory.value = history
    if (choices.sheets.length === 1) selectedSheet.value = choices.sheets[0]
  } catch (failure) { if (valid(run)) recognitionError.value = failure instanceof Error ? failure.message : '识别选项加载失败' }
  finally { if (valid(run)) recognitionLoading.value = false }
}
async function runRecognition(): Promise<void> {
  const file = recognitionFile.value, choices = recognitionOptions.value
  if (!file || !choices || !canStartRecognition.value) return
  const run = start(); recognitionBusy.value = true; recognitionError.value = ''
  const requestId = crypto.randomUUID(); activeExtractionId.value = requestId
  try {
    const item = await api.extract(file.id, { conversation_id: file.conversation_id, request_id: requestId,
      external_send_confirmed: true, pages: selectedPages.value, sheet_name: selectedSheet.value || null }, run.signal)
    if (valid(run)) recognitionHistory.value = [item, ...recognitionHistory.value.filter((row) => row.id !== item.id)]
  } catch (failure) {
    if (valid(run)) {
      recognitionError.value = failure instanceof Error ? failure.message : '识别失败，请核对会话状态或重试'
      try {
        const item = await api.extraction(requestId, run.signal)
        if (valid(run)) recognitionHistory.value = [item, ...recognitionHistory.value.filter((row) => row.id !== item.id)]
      } catch { /* 未收到服务端受理记录时不伪装为已识别。 */ }
    }
  } finally { if (valid(run)) recognitionBusy.value = false }
}
async function cancelRecognition(): Promise<void> {
  const id = activeExtractionId.value
  if (id) {
    try {
      const item = await api.cancelExtraction(id)
      recognitionHistory.value = [item, ...recognitionHistory.value.filter((row) => row.id !== item.id)]
    } catch { /* 取消请求竞争时，AbortSignal 仍会通知服务端中止当前上游调用。 */ }
  }
  controller?.abort(); recognitionBusy.value = false
  recognitionError.value = '已请求取消。未收到完成回执时，可重新打开附件查看服务端状态。'
}
function extractionStatus(status: string): string {
  return ({ RUNNING: '识别中', COMPLETED: '识别完成 · 待人工核对', FAILED: '识别失败', CANCELLED: '已取消', DELETED: '会话已清理' } as Record<string, string>)[status] || status
}
function extractionFailure(code: string): string {
  return ({ provider_timeout: 'DeepSeek 响应超时，可重试。', provider_auth_failed: 'DeepSeek 凭据未通过验证。', provider_insufficient_balance: 'DeepSeek 账户余额不足。', provider_service_error: 'DeepSeek 服务暂不可用。', worker_interrupted: '识别任务中断，可重新发起。' } as Record<string, string>)[code] || '识别未完成，请检查配置或重新发起。'
}
function documentType(value: string): string { return ({ purchase_plan: '采购计划', other: '其他单据', uncertain: '无法确定' } as Record<string, string>)[value] || value }
function fieldLabel(value: string): string { return ({ supplier_name: '供应商', supplier_code: '供应商编码', plan_date: '计划日期', remark: '备注' } as Record<string, string>)[value] || value }
function sourceLocation(item: AssistantExtraction, ref: string | null): string {
  return ref ? (item.result?.sources.find((source) => source.ref === ref)?.location || ref) : '未提供来源'
}
watch(() => [store.owner, store.active?.id, store.active?.version, auth.permissions.join('|')] as const, () => { close(); closeRecognition(); error.value = '' }, { flush: 'sync' })
watch(visible, (value) => { if (!value) close() })
onBeforeUnmount(() => { close(); closeRecognition() })
</script>
<style scoped>
.assistant-attachment-list__item { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 5px 0; font-size: 12px; overflow-wrap: anywhere; }
button { background: transparent; border: 0; cursor: pointer; color: var(--el-color-primary); }
button:disabled { opacity: .5; cursor: not-allowed; }
.assistant-attachment-list__image { display: block; max-width: 100%; max-height: 65vh; object-fit: contain; }
.assistant-extraction__actions { display: flex; justify-content: flex-end; margin: 12px 0; }
.assistant-extraction__result { padding: 12px 0; border-top: 1px solid var(--el-border-color); overflow-wrap: anywhere; }
.assistant-extraction__table { margin: 10px 0; }
.assistant-extraction__notice { color: var(--el-color-warning); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 60vh; overflow: auto; }
table { display: block; max-height: 60vh; overflow: auto; border-collapse: collapse; } td { border: 1px solid #ddd; padding: 4px; white-space: pre-wrap; }
</style>
