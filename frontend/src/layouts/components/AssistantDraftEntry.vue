<template>
  <div class="assistant-draft-entry">
    <el-button
      class="assistant-draft-entry__launch"
      type="primary"
      plain
      :disabled="serviceStatus !== 'ready'"
      @click="openEntry"
    >
      <el-icon><EditPen /></el-icon>
      快捷录入采购计划
    </el-button>

    <el-dialog
      v-model="visible"
      title="AI 快捷录入 · 采购计划"
      width="min(980px, 94vw)"
      append-to-body
      :destroy-on-close="false"
      class="assistant-draft-entry__dialog"
    >
      <p class="assistant-draft-entry__privacy">
        草稿保存在当前私人会话中，7 天后过期；只有点击采购计划表单中的保存才会创建单据，不会自动审批或改动库存。
      </p>
      <el-alert
        v-if="serviceStatus !== 'ready'"
        title="AI 服务当前不可用，请联系管理员检查配置。"
        type="warning"
        :closable="false"
        show-icon
      />

      <template v-else>
        <el-select
          v-if="drafts.length"
          v-model="selectedDraftId"
          class="assistant-draft-entry__draft-select"
          aria-label="选择本会话中的单据草稿"
          placeholder="本会话的草稿"
          @change="selectStoredDraft"
        >
          <el-option
            v-for="item in drafts"
            :key="item.draft_id"
            :value="item.draft_id"
            :label="`${item.source_display_name || '采购计划草稿'} · ${draftStatusLabel(item.draft_status)}`"
          />
        </el-select>
        <div class="assistant-draft-entry__toolbar">
          <el-radio-group v-model="mode" :disabled="busy || Boolean(currentDraft)">
            <el-radio-button value="natural">自然语言</el-radio-button>
            <el-radio-button value="table">粘贴表格</el-radio-button>
          </el-radio-group>
          <span class="assistant-draft-entry__counter">{{ inputText.length }}/4000</span>
        </div>
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="5"
          maxlength="4000"
          show-word-limit
          resize="vertical"
          :placeholder="inputPlaceholder"
          :disabled="busy"
          aria-label="采购计划原始内容"
        />
        <p v-if="mode === 'table'" class="assistant-draft-entry__hint">
          首行为表头，至少包含“商品名称”或“商品编码”以及“数量”；支持 Tab 分列和多行明细。
        </p>
        <div class="assistant-draft-entry__actions">
          <el-button
            type="primary"
            :disabled="busy || !inputText.trim() || inputText.length > 4000 || ['CREATED', 'CANCELLED', 'EXPIRED', 'CREATING'].includes(currentDraft?.draft_status || '')"
            :loading="busy"
            @click="parseInput"
          >
            {{ currentDraft ? '重新解析并比较差异' : '解析并保存草稿' }}
          </el-button>
          <el-button v-if="currentDraft && ['EDITING', 'READY', 'FAILED'].includes(currentDraft.draft_status || '')" :disabled="busy" @click="cancelDraft">
            取消草稿
          </el-button>
        </div>
        <el-alert
          v-if="errorMessage"
          class="assistant-draft-entry__alert"
          :title="errorMessage"
          type="error"
          :closable="false"
          show-icon
        />

        <section v-if="currentDraft" class="assistant-draft-entry__preview" aria-label="采购计划草稿预览">
          <div class="assistant-draft-entry__preview-header">
            <div>
              <h3>草稿预览</h3>
              <span>版本 {{ currentDraft.version }} · {{ currentDraft.ready ? '字段齐全，可交由采购计划表单继续确认' : '仍有待补或待确认字段' }}</span>
            </div>
            <el-tag :type="currentDraft.ready ? 'success' : 'warning'">
              {{ currentDraft.ready ? '可预填' : '待补充' }}
            </el-tag>
          </div>

          <div v-if="currentDraft.table_mapping" class="assistant-draft-entry__mapping">
            <strong>表格列映射：</strong>
            <span v-for="column in currentDraft.table_mapping.columns" :key="column.column">
              {{ column.column }} 列“{{ column.header }}” → {{ fieldLabel(column.field) }}；
            </span>
            <span>共识别 {{ currentDraft.table_mapping.row_count }} 行。</span>
          </div>
          <el-alert
            v-for="(item, index) in currentDraft.errors"
            :key="`error-${index}`"
            class="assistant-draft-entry__alert"
            :title="item.row ? `第 ${item.row} 行：${item.message}` : item.message"
            type="error"
            :closable="false"
          />

          <div class="assistant-draft-entry__columns">
          <aside class="assistant-draft-entry__source" aria-label="原始单据内容">
            <h4>原文与识别来源</h4>
            <p v-if="currentDraft.source_display_name">文件：{{ currentDraft.source_display_name }}</p>
            <el-checkbox v-if="currentDraft.source_attachment_id" v-model="attachSourceToPlan" :disabled="busy || currentDraft.draft_status === 'CREATED'">
              创建时复制源文件为采购计划附件
            </el-checkbox>
            <p v-if="currentDraft.source_attachment_id" class="assistant-draft-entry__hint">
              不勾选则文件仅保留在本人会话；勾选后副本按采购计划权限访问，删除会话不会删除业务副本。
            </p>
            <img v-if="sourceImageUrl" :src="sourceImageUrl" alt="原始单据图片" />
            <pre v-if="currentDraft.source_text">{{ currentDraft.source_text }}</pre>
            <p v-else class="assistant-draft-entry__hint">当前草稿没有可显示的文本来源。</p>
            <ul v-if="currentDraft.sources?.length">
              <li v-for="source in currentDraft.sources" :key="source.ref">{{ source.ref }} · {{ source.location }}</li>
            </ul>
          </aside>
          <div class="assistant-draft-entry__form">
          <div class="assistant-draft-entry__header-fields">
            <el-form-item label="供应商" :error="fieldHint(currentDraft.supplier.status)">
              <el-select
                :model-value="currentDraft.supplier.value?.id"
                filterable
                clearable
                placeholder="选择授权范围内的供应商"
                :disabled="busy"
                @change="selectSupplier"
              >
                <el-option
                  v-for="candidate in currentDraft.supplier.candidates"
                  :key="candidate.id"
                  :value="candidate.id"
                  :label="`${candidate.code || ''} · ${candidate.name}`"
                />
              </el-select>
              <span class="assistant-draft-entry__state">{{ statusLabel(currentDraft.supplier.status) }}</span>
            </el-form-item>
            <el-form-item label="计划采购时间" :error="fieldHint(currentDraft.plan_date.status)">
              <el-date-picker
                :model-value="currentDraft.plan_date.value"
                type="datetime"
                value-format="YYYY-MM-DDTHH:mm:ss"
                format="YYYY-MM-DD HH:mm:ss"
                placeholder="请补充计划日期"
                :disabled="busy"
                @change="selectDate"
              />
              <span class="assistant-draft-entry__state">{{ statusLabel(currentDraft.plan_date.status) }}</span>
            </el-form-item>
            <el-form-item label="备注">
              <el-input
                :model-value="currentDraft.remark.value || ''"
                maxlength="255"
                :disabled="busy"
                @change="changeRemark"
              />
              <span class="assistant-draft-entry__state">{{ statusLabel(currentDraft.remark.status) }}</span>
            </el-form-item>
          </div>

          <el-table :data="currentDraft.items" border size="small" max-height="340" row-key="row">
            <el-table-column label="商品 / 规格" min-width="220">
              <template #default="{ row }">
                <el-select
                  :model-value="row.product.value?.id"
                  filterable
                  placeholder="选择商品"
                  :disabled="busy"
                  @change="(value: number) => selectProduct(row.row - 1, value)"
                >
                  <el-option
                    v-for="candidate in row.product.candidates"
                    :key="candidate.id"
                    :value="candidate.id"
                    :label="`${candidate.code} · ${candidate.name}`"
                  />
                </el-select>
                <div class="assistant-draft-entry__subfield">
                  <el-select
                    :model-value="row.sku.value?.id ?? null"
                    clearable
                    placeholder="规格（如有）"
                    :disabled="busy || !row.product.value"
                    @change="(value: number | null) => selectSku(row.row - 1, value)"
                  >
                    <el-option
                      v-for="candidate in row.sku.candidates"
                      :key="candidate.id"
                      :value="candidate.id"
                      :label="`${candidate.code || ''} ${candidate.name || ''}`.trim()"
                    />
                  </el-select>
                  <span>{{ statusLabel(row.product.status) }} / {{ statusLabel(row.sku.status) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="单位" width="135">
              <template #default="{ row }">
                <el-select
                  :model-value="row.unit.value?.id"
                  placeholder="单位"
                  :disabled="busy || !row.product.value"
                  @change="(value: number) => selectUnit(row.row - 1, value)"
                >
                  <el-option
                    v-for="candidate in row.unit.candidates"
                    :key="candidate.id"
                    :value="candidate.id"
                    :label="candidate.name"
                  />
                  <el-option
                    v-if="row.unit.value"
                    :key="row.unit.value.id"
                    :value="row.unit.value.id"
                    :label="row.unit.value.name"
                  />
                </el-select>
                <small>{{ statusLabel(row.unit.status) }}</small>
              </template>
            </el-table-column>
            <el-table-column label="计划数量（主单位）" width="165">
              <template #default="{ row }">
                <el-input
                  :model-value="row.quantity.value || row.quantity.input_value || ''"
                  inputmode="decimal"
                  placeholder="必填"
                  :disabled="busy"
                  @change="(value: string) => changeQuantity(row.row - 1, value)"
                />
                <small>{{ statusLabel(row.quantity.status) }}</small>
              </template>
            </el-table-column>
            <el-table-column label="采购价（可选）" width="150">
              <template #default="{ row }">
                <el-input
                  :model-value="row.purchase_price.value || ''"
                  inputmode="decimal"
                  :placeholder="row.purchase_price.preview_value ? `系统参考 ${row.purchase_price.preview_value}` : '按现行规则取价'"
                  :disabled="busy"
                  @change="(value: string) => changePrice(row.row - 1, value)"
                />
                <small>
                  {{ row.purchase_price.source || statusLabel(row.purchase_price.status) }}
                  <span v-if="row.calculation.amount"> · 预估金额 {{ row.calculation.amount }}</span>
                </small>
              </template>
            </el-table-column>
          </el-table>
          <p v-if="currentDraft.estimated_total" class="assistant-draft-entry__estimate">
            参考合计：{{ currentDraft.estimated_total }}（采购计划现行四舍五入规则；提交时仍由后端重新校验）
          </p>

          <el-alert
            v-if="currentDraft.missing.length"
            class="assistant-draft-entry__alert"
            title="待补 / 待确认"
            type="warning"
            :closable="false"
          >
            <ul>
              <li v-for="item in currentDraft.missing" :key="item">{{ item }}</li>
            </ul>
          </el-alert>
          <el-alert
            v-for="(item, index) in currentDraft.diff"
            :key="`diff-${index}`"
            class="assistant-draft-entry__alert"
            :title="`${item.user_edit_preserved ? '重新解析检测到变化，已保留你的修改' : '重新解析差异'}：${fieldLabel(item.field)}（${displayValue(item.before)} → ${displayValue(item.after)}）`"
            type="info"
            :closable="false"
          />
          <p class="assistant-draft-entry__footnote">
            {{ currentDraft.ready ? '数量已按商品主单位规范；草稿尚未保存为 ERP 单据。' : '解决歧义并补齐必填项后可形成完整草稿；这里不会创建业务单据。' }}
          </p>
          </div>
          </div>
        </section>
      </template>

      <template #footer>
        <el-button @click="visible = false">关闭</el-button>
        <el-button
          v-if="canFillPurchasePlan"
          type="primary"
          @click="fillPurchasePlanForm"
        >
          填入采购计划表单
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { EditPen } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'

import {
  assistantApi,
  type AssistantDraftEdits,
  type AssistantDraftParsePayload,
  type AssistantDraftStatus,
  type AssistantPurchasePlanDraft,
  type AssistantSessionContract,
  type AssistantServiceStatus,
} from '@/api/assistant'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useAssistantPurchasePlanDraftStore } from '@/stores/assistantPurchasePlanDraft'
import { assistantAttachmentsApi } from '@/api/assistantAttachments'
import type { AssistantExtraction } from '@/api/assistantAttachments'

const props = defineProps<{
  session: AssistantSessionContract | null
  ensureSession: () => Promise<AssistantSessionContract | null>
  conversationId: string | null
  ensureConversation: () => Promise<string | null>
  serviceStatus: AssistantServiceStatus
}>()

const authStore = useAuthStore()
const router = useRouter()
const formDraftStore = useAssistantPurchasePlanDraftStore()
const visible = ref(false)
const mode = ref<'natural' | 'table'>('natural')
const inputText = ref('')
const currentDraft = ref<AssistantPurchasePlanDraft | null>(null)
const drafts = ref<AssistantPurchasePlanDraft[]>([])
const selectedDraftId = ref('')
const ensuredConversationId = ref('')
const sourceImageUrl = ref('')
const attachSourceToPlan = ref(false)
const busy = ref(false)
const errorMessage = ref('')
let operation = 0
let loadedConversationId = ''

const inputPlaceholder = computed(() => mode.value === 'natural'
  ? '例如：为华东供应商安排 2026-10-15 的采购计划，采购 24 个蓝色收纳箱，规格 L，单价 36.50 元，备注急需。未提到的信息会留空待确认。'
  : '供应商名称\t计划日期\t商品编码\t商品名称\t规格\t单位\t数量\t采购价\n示例供应商\t2026-10-15\tP-001\t蓝色收纳箱\tL\t个\t24\t36.50')

const canFillPurchasePlan = computed(() =>
  currentDraft.value?.ready === true &&
  Boolean(currentDraft.value.payload) &&
  !['CREATING', 'CREATED', 'FAILED', 'CANCELLED', 'EXPIRED'].includes(currentDraft.value.draft_status || '') &&
  authStore.hasPermission('purchase:create'),
)

function identityKey(): string {
  return `${authStore.profile?.staff_id ?? ''}\u0000${authStore.token ?? ''}`
}

function conversationId(): string {
  return props.conversationId || ensuredConversationId.value
}

function isCurrent(
  op: number,
  identity: string,
  sessionId: string,
  sessionVersion: number,
): boolean {
  return op === operation &&
    identityKey() === identity &&
    props.session?.session_id === sessionId &&
    props.session?.version === sessionVersion
}

function isCurrentConversation(op: number, identity: string, conversation: string): boolean {
  return op === operation && identityKey() === identity && conversationId() === conversation
}

function requestPayload(session: AssistantSessionContract): AssistantDraftParsePayload {
  return {
    request_id: crypto.randomUUID(),
    session_id: session.session_id,
    session_version: session.version,
    mode: mode.value,
    input_text: inputText.value.trim(),
  }
}

async function loadDraft(): Promise<void> {
  const conversation = conversationId()
  if (!conversation || !visible.value) return
  const identity = identityKey()
  const op = ++operation
  try {
    const loaded = await assistantApi.listDocumentDrafts(conversation)
    if (op !== operation || identityKey() !== identity || conversationId() !== conversation) return
    drafts.value = loaded
    const selected = loaded.find((item) => item.draft_id === selectedDraftId.value)
      || loaded.find((item) => !['CANCELLED', 'EXPIRED'].includes(item.draft_status || ''))
      || loaded[0]
      || null
    currentDraft.value = selected
    selectedDraftId.value = selected?.draft_id || ''
    attachSourceToPlan.value = false
    mode.value = selected?.source_kind === 'table' ? 'table' : 'natural'
    inputText.value = selected?.source_text || ''
    await loadSourceImage(selected)
    errorMessage.value = ''
  } catch (error) {
    if (op === operation && identityKey() === identity) {
      errorMessage.value = error instanceof Error ? error.message : '临时草稿加载失败'
    }
  }
}

async function openEntry(): Promise<void> {
  if (!conversationId()) {
    ensuredConversationId.value = await props.ensureConversation() || ''
  }
  if (!conversationId()) {
    errorMessage.value = '无法创建或打开私人会话，请重试'
    return
  }
  visible.value = true
  errorMessage.value = ''
  void loadDraft()
}

async function parseInput(): Promise<void> {
  if (busy.value || !inputText.value.trim()) return
  const identity = identityKey()
  const conversation = conversationId()
  if (!conversation) return
  const session = await props.ensureSession()
  if (!session || identityKey() !== identity) return
  const op = ++operation
  busy.value = true
  errorMessage.value = ''
  try {
    const payload = requestPayload(session)
    const parsed = currentDraft.value
      ? await assistantApi.reparseDocumentDraft(currentDraft.value.draft_id, {
          ...payload,
          conversation_id: conversation,
          draft_version: currentDraft.value.version,
        })
      : await assistantApi.parseDocumentDraft({ ...payload, conversation_id: conversation })
    if (!isCurrent(op, identity, session.session_id, session.version)) return
    currentDraft.value = parsed
    selectedDraftId.value = parsed.draft_id
    await loadDraftListOnly(conversation)
    await loadSourceImage(parsed)
    ElMessage.success(parsed.ready ? '采购计划草稿已就绪' : '解析完成，请补充或确认标记字段')
  } catch (error) {
    if (isCurrent(op, identity, session.session_id, session.version)) {
      errorMessage.value = error instanceof ApiError ? error.message : '解析失败，请检查内容后重试'
    }
  } finally {
    if (op === operation) busy.value = false
  }
}

async function saveEdits(edits: AssistantDraftEdits): Promise<void> {
  const draft = currentDraft.value
  if (!draft || busy.value) return
  const identity = identityKey()
  const conversation = conversationId()
  const op = ++operation
  busy.value = true
  errorMessage.value = ''
  try {
    const updated = await assistantApi.editDocumentDraft(draft.draft_id, draft.version, edits)
    if (isCurrentConversation(op, identity, conversation)) {
      currentDraft.value = updated
      await loadDraftListOnly(conversation)
    }
  } catch (error) {
    if (isCurrentConversation(op, identity, conversation)) {
      errorMessage.value = error instanceof ApiError ? error.message : '草稿更新失败，请刷新后重试'
      void loadDraft()
    }
  } finally {
    if (op === operation) busy.value = false
  }
}

function editLine(index: number, value: Record<string, number | string | null>): void {
  const rows = currentDraft.value?.items.map(() => ({}))
  if (!rows) return
  rows[index] = value
  void saveEdits({ items: rows })
}

function selectSupplier(value: number | null): void {
  void saveEdits({ supplier_id: value })
}

function selectDate(value: string | null): void {
  void saveEdits({ plan_date: value })
}

function changeRemark(value: string): void {
  void saveEdits({ remark: value })
}

function selectProduct(index: number, value: number): void {
  editLine(index, { product_id: value })
}

function selectSku(index: number, value: number | null): void {
  editLine(index, { sku_id: value })
}

function selectUnit(index: number, value: number): void {
  editLine(index, { unit_id: value })
}

function changeQuantity(index: number, value: string): void {
  editLine(index, { planned_qty: value })
}

function changePrice(index: number, value: string): void {
  editLine(index, { purchase_price: value || null })
}

async function cancelDraft(): Promise<void> {
  const draft = currentDraft.value
  if (!draft || busy.value) return
  const identity = identityKey()
  const conversation = conversationId()
  const op = ++operation
  busy.value = true
  try {
    await assistantApi.cancelDocumentDraft(draft.draft_id, draft.version)
    if (!isCurrentConversation(op, identity, conversation)) return
    currentDraft.value = null
    drafts.value = drafts.value.filter((item) => item.draft_id !== draft.draft_id)
    selectedDraftId.value = ''
    releaseSourceImage()
    inputText.value = ''
    errorMessage.value = ''
    ElMessage.success('草稿已取消')
  } catch (error) {
    if (isCurrentConversation(op, identity, conversation)) {
      errorMessage.value = error instanceof ApiError ? error.message : '取消草稿失败'
    }
  } finally {
    if (op === operation) busy.value = false
  }
}

function statusLabel(status: AssistantDraftStatus): string {
  const labels: Record<AssistantDraftStatus, string> = {
    recognized: '已识别',
    missing: '待补充',
    ambiguous: '需选择',
    invalid: '格式有误',
    user_edited: '已修改',
    optional: '可选',
  }
  return labels[status]
}

function fieldHint(status: AssistantDraftStatus): string | undefined {
  return ['missing', 'ambiguous', 'invalid'].includes(status) ? statusLabel(status) : undefined
}

function fieldLabel(field: string): string {
  const labels: Record<string, string> = {
    supplier: '供应商',
    plan_date: '计划日期',
    remark: '备注',
    product: '商品',
    sku: '规格',
    unit: '单位',
    quantity: '计划数量',
    purchase_price: '采购价',
  }
  const tail = field.split('.').at(-1) || field
  return labels[tail] || field
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '空'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

async function fillPurchasePlanForm(): Promise<void> {
  const draft = currentDraft.value
  const staffId = authStore.profile?.staff_id
  const token = authStore.token
  if (!draft?.ready || !draft.payload || !staffId || !token) return
  formDraftStore.queue(`${staffId}\u0000${token}`, {
    ...draft,
    attach_source_on_confirm: attachSourceToPlan.value,
  })
  visible.value = false
  try {
    await router.push({ name: 'purchase-plans' })
    ElMessage.success('草稿已交给采购计划表单；请核对内容后再点击保存')
  } catch {
    formDraftStore.clear()
    ElMessage.error('无法打开采购计划表单，请重试')
  }
}

async function loadDraftListOnly(conversation: string): Promise<void> {
  if (conversation) drafts.value = await assistantApi.listDocumentDrafts(conversation)
}

function selectStoredDraft(id: string): void {
  const draft = drafts.value.find((item) => item.draft_id === id)
  if (!draft) return
  currentDraft.value = draft
  selectedDraftId.value = id
  attachSourceToPlan.value = false
  inputText.value = draft.source_text || ''
  mode.value = draft.source_kind === 'table' ? 'table' : 'natural'
  void loadSourceImage(draft)
}

let sourceController: AbortController | null = null
function releaseSourceImage(): void {
  sourceController?.abort()
  sourceController = null
  if (sourceImageUrl.value) URL.revokeObjectURL(sourceImageUrl.value)
  sourceImageUrl.value = ''
}

async function loadSourceImage(draft: AssistantPurchasePlanDraft | null): Promise<void> {
  releaseSourceImage()
  if (!draft?.source_attachment_id || !draft.conversation_id) return
  const controller = new AbortController()
  sourceController = controller
  try {
    const preview = await assistantAttachmentsApi.preview(
      draft.source_attachment_id, draft.conversation_id, controller.signal,
    )
    if (preview.kind !== 'image' || controller.signal.aborted) return
    const blob = await assistantAttachmentsApi.content(
      draft.source_attachment_id, draft.conversation_id, controller,
    )
    if (!controller.signal.aborted && currentDraft.value?.draft_id === draft.draft_id) {
      sourceImageUrl.value = URL.createObjectURL(blob)
    }
  } catch {
    // 原附件按既有私有保留策略清理后，草稿文本和字段仍可继续核对。
  }
}

async function createFromExtraction(extraction: AssistantExtraction): Promise<void> {
  let conversation = conversationId()
  if (!conversation) {
    conversation = await props.ensureConversation() || ''
    ensuredConversationId.value = conversation
  }
  if (!conversation || conversation !== extraction.conversation_id) {
    errorMessage.value = '请切换到附件所属的本人会话后再生成草稿'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const created = await assistantApi.createDocumentDraftFromExtraction({
      conversation_id: conversation,
      extraction_id: extraction.id,
      request_id: crypto.randomUUID(),
    })
    currentDraft.value = created
    selectedDraftId.value = created.draft_id
    attachSourceToPlan.value = false
    inputText.value = created.source_text || ''
    mode.value = 'natural'
    await loadDraftListOnly(conversation)
    await loadSourceImage(created)
    visible.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '未能从识别结果创建草稿'
  } finally {
    busy.value = false
  }
}

function draftStatusLabel(status?: string): string {
  return ({ READY: '待确认', EDITING: '待补充', CREATING: '保存处理中', CREATED: '已创建', FAILED: '创建失败', CANCELLED: '已取消', EXPIRED: '已过期' } as Record<string, string>)[status || ''] || '草稿'
}

watch(
  () => [authStore.profile?.staff_id ?? null, authStore.token] as const,
  () => {
    operation += 1
    currentDraft.value = null
    drafts.value = []
    selectedDraftId.value = ''
    attachSourceToPlan.value = false
    ensuredConversationId.value = ''
    inputText.value = ''
    visible.value = false
    busy.value = false
    loadedConversationId = ''
    releaseSourceImage()
  },
  { flush: 'sync' },
)

watch(
  () => [conversationId(), visible.value] as const,
  ([conversation, isVisible]) => {
    if (conversation !== loadedConversationId) {
      operation += 1
      currentDraft.value = null
      busy.value = false
      drafts.value = []
      selectedDraftId.value = ''
      attachSourceToPlan.value = false
      inputText.value = ''
      loadedConversationId = conversation
      releaseSourceImage()
    }
    if (isVisible && conversation) void loadDraft()
  },
)

onBeforeUnmount(releaseSourceImage)
defineExpose({ createFromExtraction })
</script>

<style scoped>
.assistant-draft-entry__launch {
  width: 100%;
}

.assistant-draft-entry__privacy,
.assistant-draft-entry__hint,
.assistant-draft-entry__footnote {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.assistant-draft-entry__toolbar,
.assistant-draft-entry__actions,
.assistant-draft-entry__preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px 0;
}

.assistant-draft-entry__counter,
.assistant-draft-entry__state,
.assistant-draft-entry__subfield span,
.assistant-draft-entry__preview-header span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.assistant-draft-entry__preview {
  margin-top: 22px;
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 12px;
}

.assistant-draft-entry__draft-select { width: 100%; margin: 4px 0 8px; }
.assistant-draft-entry__columns { display: grid; grid-template-columns: minmax(220px, .75fr) minmax(0, 2fr); gap: 16px; align-items: start; }
.assistant-draft-entry__source { min-width: 0; padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 6px; background: var(--el-fill-color-lighter); }
.assistant-draft-entry__source h4 { margin: 0 0 8px; }
.assistant-draft-entry__source pre { max-height: 48vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.assistant-draft-entry__source img { display: block; max-width: 100%; max-height: 48vh; margin: 8px auto; object-fit: contain; }
.assistant-draft-entry__form { min-width: 0; }

.assistant-draft-entry__preview-header h3 {
  margin: 0 0 4px;
}

.assistant-draft-entry__mapping {
  margin: 10px 0;
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 1.7;
}

.assistant-draft-entry__header-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 16px;
  margin: 14px 0;
}

.assistant-draft-entry__subfield {
  display: grid;
  gap: 4px;
  margin-top: 5px;
}

.assistant-draft-entry__subfield .el-select,
.assistant-draft-entry__header-fields .el-select,
.assistant-draft-entry__header-fields .el-date-editor,
.assistant-draft-entry__header-fields .el-input {
  width: 100%;
}

.assistant-draft-entry__state {
  display: block;
  margin-top: 2px;
}

.assistant-draft-entry__alert {
  margin-top: 10px;
}

.assistant-draft-entry__footnote {
  margin: 10px 0 0;
}

@media (max-width: 640px) {
  .assistant-draft-entry__columns { grid-template-columns: 1fr; }
  .assistant-draft-entry__header-fields {
    grid-template-columns: 1fr;
  }
}
</style>
