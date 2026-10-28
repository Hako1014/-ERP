<template>
  <div class="quick-entry-page">
    <div class="quick-entry-page__header">
      <div>
        <h1>快捷录入</h1>
        <p>选择有权限的单据类型，用自然语言或粘贴表格生成可编辑草稿。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
    </div>

    <el-alert
      class="quick-entry-page__privacy"
      type="info"
      :closable="false"
      show-icon
      title="草稿仅本人可见，7 天后过期。确认导入只会填入原单据表单；在原表单点击保存后才会创建单据。"
    />

    <div class="quick-entry-page__layout">
      <main class="quick-entry-page__main">
        <el-card shadow="never" class="quick-entry-card">
          <template #header>
            <div class="quick-entry-card__title">
              <span>新建快捷草稿</span>
              <el-tag v-if="selectedType" effect="plain">{{ selectedType.module_label }} · {{ selectedType.document_label }}</el-tag>
            </div>
          </template>

          <el-form label-position="top">
            <div class="quick-entry-selectors">
              <el-form-item label="业务模块">
                <el-select v-model="selectedModule" :disabled="Boolean(selectedDraft)" placeholder="选择业务模块" @change="changeModule">
                  <el-option v-for="item in moduleOptions" :key="item.key" :label="item.label" :value="item.key" />
                </el-select>
              </el-form-item>
              <el-form-item label="单据类型">
                <el-select v-model="selectedDocumentType" :disabled="Boolean(selectedDraft)" placeholder="选择单据类型">
                  <el-option v-for="item in moduleDocuments" :key="item.document_type" :label="item.document_label" :value="item.document_type" />
                </el-select>
              </el-form-item>
            </div>

            <div class="quick-entry-tabs" role="tablist" aria-label="录入方式">
              <button :class="{ active: sourceMode === 'natural' }" type="button" @click="sourceMode = 'natural'">自然语言</button>
              <button :class="{ active: sourceMode === 'table' }" type="button" @click="sourceMode = 'table'">粘贴表格</button>
              <span>{{ inputText.length }} / {{ MAX_QUICK_ENTRY_TEXT }}</span>
            </div>
            <el-input
              v-model="inputText"
              type="textarea"
              :rows="6"
              maxlength="4000"
              show-word-limit
              resize="vertical"
              :placeholder="inputPlaceholder"
              aria-label="快捷录入内容"
            />
            <section v-if="sourceMode === 'table' && tableHeaders.length" class="quick-entry-table-preview" aria-label="表格预览和列映射">
              <div class="quick-entry-table-preview__heading">
                <strong>表格预览与列映射</strong>
                <span>供应商：{{ tablePreview.supplier_name || '待补充' }} · 日期：{{ tablePreview.plan_date || '待补充' }} · {{ tablePreview.items.length }} 行</span>
              </div>
              <div class="quick-entry-column-map">
                <div v-for="(header, index) in tableHeaders" :key="`${index}-${header}`" class="quick-entry-column-map__item">
                  <span :title="header">{{ header || `第 ${index + 1} 列` }}</span>
                  <el-select v-model="tableColumnMapping[index]" size="small" :aria-label="`映射表格列 ${header || index + 1}`">
                    <el-option v-for="option in tableColumnOptions" :key="option.value" :label="option.label" :value="option.value" />
                  </el-select>
                </div>
              </div>
              <el-table :data="tablePreview.items" border stripe size="small" max-height="220" empty-text="尚未解析出明细行">
                <el-table-column prop="product_name" label="商品名称" min-width="150" />
                <el-table-column prop="sku_name" label="规格 / SKU" min-width="110" />
                <el-table-column prop="quantity" label="数量" width="100" />
                <el-table-column prop="purchase_price" label="采购价" width="110" />
              </el-table>
              <div v-if="tablePreview.errors.length" class="quick-entry-table-preview__errors">
                <div v-for="(error, index) in tablePreview.errors" :key="index">
                  {{ error.row ? `第 ${error.row} 行：` : '' }}{{ error.message }}
                </div>
              </div>
            </section>
            <div class="quick-entry-actions">
              <el-button type="primary" :loading="parsing" :disabled="!selectedType || !inputText.trim()" @click="parseAndSave">
                解析并保存草稿
              </el-button>
              <el-button v-if="selectedDraft" @click="startNewDraft">新建草稿</el-button>
            </div>
          </el-form>
        </el-card>

        <el-card v-if="selectedDraft" shadow="never" class="quick-entry-card quick-entry-review">
          <template #header>
            <div class="quick-entry-card__title">
              <span>检查并编辑草稿</span>
              <el-tag :type="selectedDraft.status === 'EXPIRED' ? 'danger' : 'info'" effect="plain">
                {{ draftStatusLabel }}
              </el-tag>
            </div>
          </template>

          <el-alert
            v-if="draftData.errors.length || draftData.missing.length"
            type="warning"
            :closable="false"
            show-icon
            title="未识别或缺失的信息不会自动猜测，请补充并核对。"
          >
            <template #default>
              <div v-for="(error, index) in draftData.errors" :key="`error-${index}`">
                {{ error.row ? `第 ${error.row} 行：` : '' }}{{ error.message }}
              </div>
              <div v-if="draftData.missing.length">待补充：{{ draftData.missing.join('、') }}</div>
            </template>
          </el-alert>

          <template v-if="selectedDraft.document_type === 'purchase_plan'">
            <div class="quick-entry-fields">
              <el-form-item label="供应商（名称或编码）">
                <el-input v-model="draftData.supplier_name" @input="touchDraft" />
              </el-form-item>
              <el-form-item label="采购日期">
                <el-date-picker
                  v-model="draftData.plan_date"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="请选择日期"
                  @change="touchDraft"
                />
              </el-form-item>
              <el-form-item label="备注">
                <el-input v-model="draftData.remark" maxlength="255" @input="touchDraft" />
              </el-form-item>
            </div>

            <div class="quick-entry-lines-heading">
              <h3>商品明细</h3>
              <el-button text type="primary" @click="addLine">＋ 添加一行</el-button>
            </div>
            <el-table :data="draftData.items" border stripe size="small" empty-text="请添加商品明细">
              <el-table-column label="商品名称 / 编码" min-width="190">
                <template #default="{ row }"><el-input v-model="row.product_name" @input="touchDraft" /></template>
              </el-table-column>
              <el-table-column label="规格 / SKU" min-width="130">
                <template #default="{ row }"><el-input v-model="row.sku_name" @input="touchDraft" /></template>
              </el-table-column>
              <el-table-column label="单位" width="100">
                <template #default="{ row }"><el-input v-model="row.unit_name" @input="touchDraft" /></template>
              </el-table-column>
              <el-table-column label="数量" width="130">
                <template #default="{ row }"><el-input v-model="row.quantity" inputmode="decimal" @input="touchDraft" /></template>
              </el-table-column>
              <el-table-column label="采购价" width="140">
                <template #default="{ row }"><el-input v-model="row.purchase_price" inputmode="decimal" @input="touchDraft" /></template>
              </el-table-column>
              <el-table-column label="操作" width="78" fixed="right">
                <template #default="{ $index }"><el-button link type="danger" @click="removeLine($index)">移除</el-button></template>
              </el-table-column>
            </el-table>
          </template>

          <div class="quick-entry-review__footer">
            <span>草稿版本 {{ selectedDraft.version }} · 到期 {{ formatExpiry(selectedDraft.expires_at) }}</span>
            <div>
              <el-button :loading="saving" :disabled="selectedDraft.status === 'EXPIRED'" @click="saveDraft">保存修改</el-button>
              <el-button type="primary" :loading="importing" :disabled="selectedDraft.status === 'EXPIRED'" @click="confirmAndImport">
                确认并导入
              </el-button>
            </div>
          </div>
        </el-card>
      </main>

      <aside>
        <el-card shadow="never" class="quick-entry-card quick-entry-drafts">
          <template #header>
            <div class="quick-entry-card__title">
              <span>我的草稿</span>
              <el-button text :icon="Refresh" :loading="loadingDrafts" aria-label="刷新草稿" @click="loadDrafts" />
            </div>
          </template>
          <el-empty v-if="!drafts.length && !loadingDrafts" description="暂无快捷录入草稿" :image-size="72" />
          <div v-else class="quick-entry-draft-list">
            <div
              v-for="draft in drafts"
              :key="draft.draft_id"
              class="quick-entry-draft"
              :class="{ 'is-active': selectedDraft?.draft_id === draft.draft_id }"
            >
              <button type="button" class="quick-entry-draft__body" @click="selectDraft(draft)">
                <strong>{{ draft.title }}</strong>
                <span>{{ typeLabel(draft.document_type) }} · {{ formatExpiry(draft.updated_at) }}</span>
                <el-tag size="small" :type="draft.status === 'EXPIRED' ? 'danger' : draft.status === 'APPLIED' ? 'success' : 'info'">
                  {{ statusLabel(draft.status) }}
                </el-tag>
              </button>
              <el-button text type="danger" :icon="Delete" aria-label="删除草稿" @click="deleteDraft(draft)" />
            </div>
          </div>
        </el-card>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Delete, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'

import { partyApi, type PartyRecord } from '@/api/party'
import { productApi, type ProductRecord, type ProductSku } from '@/api/product'
import {
  quickEntryApi,
  type QuickEntryDocumentType,
  type QuickEntryDraft,
  type QuickEntryDraftData,
} from '@/api/quickEntry'
import type { AssistantDraftCandidate, AssistantDraftField, AssistantPurchasePlanDraft } from '@/api/assistant'
import { useQuickEntryStore } from '@/stores/quickEntry'
import { useAuthStore } from '@/stores/auth'
import {
  MAX_QUICK_ENTRY_TEXT,
  getDefaultPurchasePlanColumnMapping,
  getPurchasePlanTableHeaders,
  parsePurchasePlanNatural,
  parsePurchasePlanTable,
  type TableColumnField,
  validatePurchasePlanDraft,
} from '@/views/quick-entry/quickEntryHelpers'

const router = useRouter()
const authStore = useAuthStore()
const handoffStore = useQuickEntryStore()
const catalog = ref<QuickEntryDocumentType[]>([])
const drafts = ref<QuickEntryDraft[]>([])
const selectedDraft = ref<QuickEntryDraft | null>(null)
const selectedModule = ref('')
const selectedDocumentType = ref('')
const sourceMode = ref<'natural' | 'table'>('natural')
const inputText = ref('')
const tableColumnMapping = ref<Record<number, TableColumnField>>({})
const draftTitle = ref('')
const loading = ref(false)
const loadingDrafts = ref(false)
const parsing = ref(false)
const saving = ref(false)
const importing = ref(false)
const draftData = reactive<QuickEntryDraftData>({
  supplier_name: '', plan_date: '', remark: '', items: [], errors: [], missing: [],
})

const moduleOptions = computed(() => {
  const values = new Map<string, string>()
  catalog.value.forEach((item) => values.set(item.module_key, item.module_label))
  return [...values.entries()].map(([key, label]) => ({ key, label }))
})
const moduleDocuments = computed(() => catalog.value.filter((item) => item.module_key === selectedModule.value))
const selectedType = computed(() => catalog.value.find((item) => item.document_type === selectedDocumentType.value) || null)
const tableHeaders = computed(() => getPurchasePlanTableHeaders(inputText.value))
const tablePreview = computed(() => parsePurchasePlanTable(inputText.value, tableColumnMapping.value))
const tableColumnOptions: Array<{ value: TableColumnField; label: string }> = [
  { value: 'product_name', label: '商品名称' },
  { value: 'sku_name', label: '规格 / SKU' },
  { value: 'unit_name', label: '单位' },
  { value: 'quantity', label: '数量' },
  { value: 'purchase_price', label: '采购价' },
  { value: 'supplier_name', label: '供应商' },
  { value: 'plan_date', label: '采购日期' },
  { value: 'remark', label: '备注' },
  { value: 'ignore', label: '忽略此列' },
]
const inputPlaceholder = computed(() => sourceMode.value === 'natural'
  ? '例如：供应商：华东供应商，采购日期：2026-10-15，商品：蓝色收纳箱，规格：L，数量：24，单价：36.50，备注：急需。未明确的信息会留空待确认。'
  : '粘贴包含“商品名称”和“数量”列的 TSV/CSV，可选列：规格、单位、采购价。')
const draftStatusLabel = computed(() => statusLabel(selectedDraft.value?.status || 'EDITING'))

function statusLabel(status: QuickEntryDraft['status'] | string): string {
  return ({ EDITING: '待确认', APPLIED: '已导入表单', EXPIRED: '已过期', DELETED: '已删除' } as Record<string, string>)[status] || status
}

function typeLabel(type: string): string {
  return catalog.value.find((item) => item.document_type === type)?.document_label || type
}

function formatExpiry(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function resetDraftEditor(): void {
  selectedDraft.value = null
  inputText.value = ''
  sourceMode.value = 'natural'
  draftTitle.value = ''
  Object.assign(draftData, { supplier_name: '', plan_date: '', remark: '', items: [], errors: [], missing: [] })
}

function startNewDraft(): void {
  resetDraftEditor()
}

function changeModule(moduleKey: string): void {
  selectedDocumentType.value = catalog.value.find((item) => item.module_key === moduleKey)?.document_type || ''
}

function touchDraft(): void {
  draftData.errors = []
  validatePurchasePlanDraft(draftData)
}

function cloneData(data: QuickEntryDraftData): QuickEntryDraftData {
  return JSON.parse(JSON.stringify(data)) as QuickEntryDraftData
}

async function reload(): Promise<void> {
  loading.value = true
  try {
    catalog.value = await quickEntryApi.catalog()
    if (!selectedModule.value || !moduleOptions.value.some((item) => item.key === selectedModule.value)) {
      selectedModule.value = moduleOptions.value[0]?.key || ''
    }
    if (!moduleDocuments.value.some((item) => item.document_type === selectedDocumentType.value)) {
      selectedDocumentType.value = moduleDocuments.value[0]?.document_type || ''
    }
    await loadDrafts()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '快捷录入目录加载失败')
  } finally {
    loading.value = false
  }
}

async function loadDrafts(): Promise<void> {
  loadingDrafts.value = true
  try {
    drafts.value = await quickEntryApi.listDrafts()
    if (selectedDraft.value) {
      selectedDraft.value = drafts.value.find((item) => item.draft_id === selectedDraft.value?.draft_id) || selectedDraft.value
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '快捷录入草稿加载失败')
  } finally {
    loadingDrafts.value = false
  }
}

async function parseAndSave(): Promise<void> {
  if (!selectedType.value || !inputText.value.trim()) return
  if (selectedType.value.document_type !== 'purchase_plan') {
    ElMessage.warning('该单据类型尚未接入解析适配器')
    return
  }
  parsing.value = true
  try {
    const parsed = sourceMode.value === 'natural'
      ? parsePurchasePlanNatural(inputText.value)
      : parsePurchasePlanTable(inputText.value, tableColumnMapping.value)
    const row = await quickEntryApi.createDraft({
      request_id: crypto.randomUUID(),
      document_type: selectedType.value.document_type,
      source_mode: sourceMode.value,
      title: `${selectedType.value.document_label} · ${parsed.supplier_name || '待补充'}`,
      input_text: inputText.value,
      draft_data: parsed,
    })
    drafts.value = [row, ...drafts.value.filter((item) => item.draft_id !== row.draft_id)]
    selectDraft(row)
    ElMessage.success('草稿已保存，可继续补充和核对')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '解析或保存草稿失败')
  } finally {
    parsing.value = false
  }
}

function selectDraft(row: QuickEntryDraft): void {
  selectedDraft.value = row
  selectedModule.value = row.module_key
  selectedDocumentType.value = row.document_type
  sourceMode.value = row.source_mode
  inputText.value = row.input_text
  draftTitle.value = row.title
  Object.assign(draftData, cloneData(row.draft_data))
}

function addLine(): void {
  draftData.items.push({ product_name: '', sku_name: '', unit_name: '', quantity: '', purchase_price: '' })
  touchDraft()
}

function removeLine(index: number): void {
  draftData.items.splice(index, 1)
  touchDraft()
}

async function saveDraft(): Promise<QuickEntryDraft | null> {
  const current = selectedDraft.value
  if (!current) return null
  saving.value = true
  try {
    const updated = await quickEntryApi.updateDraft(current.draft_id, {
      draft_version: current.version,
      title: draftTitle.value || current.title,
      input_text: inputText.value,
      source_mode: sourceMode.value,
      draft_data: cloneData(draftData),
    })
    selectedDraft.value = updated
    drafts.value = drafts.value.map((item) => item.draft_id === updated.draft_id ? updated : item)
    return updated
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存草稿失败，请刷新后重试')
    return null
  } finally {
    saving.value = false
  }
}

function normalize(value: string): string {
  return value.trim().toLocaleLowerCase()
}

async function resolveSupplier(nameOrCode: string): Promise<PartyRecord> {
  const result = await partyApi.listSuppliers({ keyword: nameOrCode, is_active: true, page: 1, page_size: 100 })
  const key = normalize(nameOrCode)
  const matches = result.items.filter((item) => normalize(item.name) === key || normalize(item.code) === key)
  if (matches.length !== 1) throw new Error(matches.length ? `供应商“${nameOrCode}”匹配到多条，请改填唯一编码` : `找不到启用的供应商“${nameOrCode}”，请核对名称或编码`)
  return matches[0]
}

async function resolveProduct(item: QuickEntryDraftData['items'][number]): Promise<{ product: ProductRecord; sku: ProductSku | null }> {
  const result = await productApi.listProducts({ keyword: item.product_name, is_disabled: false, page: 1, page_size: 100 })
  const key = normalize(item.product_name)
  const matches = result.items.filter((product) => [product.name, product.code, product.art_no || ''].some((value) => normalize(value) === key))
  if (matches.length !== 1) throw new Error(matches.length ? `商品“${item.product_name}”匹配到多条，请改填唯一编码` : `找不到启用的商品“${item.product_name}”，请核对名称或编码`)
  const product = await productApi.getProduct(matches[0].id)
  const activeSkus = (product.skus || []).filter((sku) => sku.is_active)
  if (item.sku_name.trim()) {
    const skuKey = normalize(item.sku_name)
    const skuMatches = activeSkus.filter((sku) => normalize(sku.spec_name || '') === skuKey || normalize(sku.spec_code) === skuKey || normalize(sku.barcode || '') === skuKey)
    if (skuMatches.length !== 1) throw new Error(skuMatches.length ? `商品“${item.product_name}”的规格“${item.sku_name}”不唯一` : `找不到商品“${item.product_name}”的启用规格“${item.sku_name}”`)
    return { product, sku: skuMatches[0] }
  }
  if (activeSkus.length > 1) throw new Error(`商品“${item.product_name}”有多个规格，请填写规格编码或名称`)
  return { product, sku: activeSkus[0] || null }
}

function field<T>(value: T | null, status: AssistantDraftField<T>['status'] = 'user_edited', query?: string): AssistantDraftField<T> {
  return { status, value, query: query || null, source: '快捷录入人工确认' }
}

async function toPurchasePlanDraft(row: QuickEntryDraft): Promise<AssistantPurchasePlanDraft> {
  const currentData = row.draft_data
  const problems = validatePurchasePlanDraft(currentData)
  if (problems.length) throw new Error(`请先修正草稿：${problems.join('；')}`)
  const supplier = await resolveSupplier(currentData.supplier_name)
  const resolvedItems = await Promise.all(currentData.items.map(resolveProduct))
  const supplierCandidate: AssistantDraftCandidate = { id: supplier.id, code: supplier.code, name: supplier.name }
  const items = currentData.items.map((item, index) => {
    const { product, sku } = resolvedItems[index]
    const productCandidate: AssistantDraftCandidate = {
      id: product.id,
      code: product.code,
      name: product.name,
      art_no: product.art_no,
      skus: (product.skus || []).filter((option) => option.is_active).map((option) => ({
        id: option.id,
        code: option.spec_code,
        name: option.spec_name || option.spec_code,
      })),
    }
    const skuCandidate: AssistantDraftCandidate | null = sku
      ? { id: sku.id, code: sku.spec_code, name: sku.spec_name || sku.spec_code }
      : null
    return {
      row: index + 1,
      product: field(productCandidate, 'recognized', item.product_name),
      sku: field(skuCandidate, skuCandidate ? 'recognized' : 'optional', item.sku_name),
      unit: field<AssistantDraftCandidate>(null, 'optional', item.unit_name),
      quantity: field(item.quantity),
      purchase_price: field(item.purchase_price || null, item.purchase_price ? 'user_edited' : 'optional'),
      calculation: { unit_price: item.purchase_price || null, amount: null, source: '待原生表单校验' },
    }
  })
  return {
    draft_id: row.draft_id,
    version: row.version,
    doc_type: 'purchase_plan',
    source_kind: row.source_mode,
    draft_status: 'READY',
    source_text: row.input_text,
    status: 'ready',
    ready: true,
    supplier: field(supplierCandidate, 'recognized', currentData.supplier_name),
    plan_date: field(`${currentData.plan_date}T00:00:00`, 'user_edited'),
    remark: field(currentData.remark || null),
    items,
    table_mapping: row.source_mode === 'table'
      ? {
          columns: [],
          row_count: currentData.items.length,
          errors: currentData.errors.flatMap((item) => item.row === undefined ? [] : [{ row: item.row, message: item.message }]),
        }
      : null,
    errors: [],
    missing: [],
    edited_fields: [],
    diff: [],
    estimated_total: null,
    estimate_complete: false,
    expires_at: row.expires_at,
    payload: {
      supplier_id: supplier.id,
      plan_date: `${currentData.plan_date}T00:00:00`,
      remark: currentData.remark || null,
      items: currentData.items.map((item, index) => ({
        product_id: resolvedItems[index].product.id,
        sku_id: resolvedItems[index].sku?.id || null,
        planned_qty: item.quantity,
        purchase_price: item.purchase_price || null,
      })),
    },
  }
}

async function confirmAndImport(): Promise<void> {
  const current = selectedDraft.value
  if (!current || !selectedType.value) return
  const errors = validatePurchasePlanDraft(draftData)
  if (errors.length) {
    ElMessage.warning(errors.join('；'))
    return
  }
  importing.value = true
  try {
    const saved = await saveDraft()
    if (!saved) return
    const handoffDraft = await toPurchasePlanDraft(saved)
    await ElMessageBox.confirm(
      `将草稿“${saved.title}”导入${selectedType.value.document_label}原生表单。此操作不会创建单据；请在原表单核对后点击保存。`,
      '确认导入草稿',
      { confirmButtonText: '确认并打开表单', cancelButtonText: '返回修改', type: 'warning' },
    )
    await quickEntryApi.confirmImport(saved.draft_id, saved.version)
    const identity = `${authStore.profile?.staff_id ?? ''}\u0000${authStore.token ?? ''}`
    handoffStore.queue(identity, handoffDraft)
    await router.push(selectedType.value.target_path)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error instanceof Error ? error.message : '确认导入失败')
  } finally {
    importing.value = false
  }
}

async function deleteDraft(row: QuickEntryDraft): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除“${row.title}”吗？删除后草稿内容不可恢复。`, '删除草稿', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
    await quickEntryApi.deleteDraft(row.draft_id)
    drafts.value = drafts.value.filter((item) => item.draft_id !== row.draft_id)
    if (selectedDraft.value?.draft_id === row.draft_id) resetDraftEditor()
    ElMessage.success('草稿已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error instanceof Error ? error.message : '删除草稿失败')
  }
}

onMounted(() => { void reload() })

watch(
  () => tableHeaders.value.join('\u0000'),
  () => { tableColumnMapping.value = getDefaultPurchasePlanColumnMapping(tableHeaders.value) },
  { immediate: true },
)
</script>

<style scoped>
.quick-entry-page { display: flex; flex-direction: column; gap: 18px; padding: 24px; }
.quick-entry-page__header, .quick-entry-card__title, .quick-entry-review__footer, .quick-entry-lines-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.quick-entry-page__header h1 { margin: 0; color: var(--el-text-color-primary); font-size: 24px; }
.quick-entry-page__header p { margin: 8px 0 0; color: var(--el-text-color-secondary); }
.quick-entry-page__privacy { border-radius: 8px; }
.quick-entry-page__layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; align-items: start; gap: 18px; }
.quick-entry-page__main { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.quick-entry-card { border-radius: 10px; }
.quick-entry-card__title { color: var(--el-text-color-primary); font-weight: 600; }
.quick-entry-selectors, .quick-entry-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.quick-entry-tabs { display: flex; align-items: center; gap: 0; margin: 2px 0 12px; }
.quick-entry-tabs button { min-width: 112px; height: 38px; border: 1px solid var(--el-border-color); background: #fff; color: var(--el-text-color-regular); cursor: pointer; }
.quick-entry-tabs button:first-child { border-radius: 6px 0 0 6px; }
.quick-entry-tabs button:nth-child(2) { border-left: 0; border-radius: 0 6px 6px 0; }
.quick-entry-tabs button.active { border-color: var(--el-color-primary); background: var(--el-color-primary); color: #fff; }
.quick-entry-tabs span { margin-left: auto; color: var(--el-text-color-secondary); font-size: 13px; }
.quick-entry-table-preview { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-fill-color-lighter); }
.quick-entry-table-preview__heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; color: var(--el-text-color-primary); }
.quick-entry-table-preview__heading span, .quick-entry-table-preview__errors { color: var(--el-color-warning-dark-2); font-size: 12px; }
.quick-entry-column-map { display: flex; flex-wrap: wrap; gap: 10px; }
.quick-entry-column-map__item { display: grid; grid-template-columns: minmax(70px, 1fr) 140px; align-items: center; gap: 8px; max-width: 280px; }
.quick-entry-column-map__item span { overflow: hidden; color: var(--el-text-color-secondary); text-overflow: ellipsis; white-space: nowrap; }
.quick-entry-table-preview__errors { display: flex; flex-direction: column; gap: 4px; }
.quick-entry-actions { display: flex; gap: 10px; margin-top: 16px; }
.quick-entry-review :deep(.el-alert) { margin-bottom: 16px; }
.quick-entry-lines-heading { margin: 4px 0 10px; }
.quick-entry-lines-heading h3 { margin: 0; font-size: 15px; }
.quick-entry-review__footer { margin-top: 18px; color: var(--el-text-color-secondary); font-size: 12px; }
.quick-entry-draft-list { display: flex; flex-direction: column; gap: 8px; max-height: 600px; overflow: auto; }
.quick-entry-draft { display: flex; align-items: center; gap: 4px; padding: 8px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.quick-entry-draft.is-active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.quick-entry-draft__body { display: flex; flex: 1; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; padding: 4px; border: 0; background: transparent; text-align: left; cursor: pointer; }
.quick-entry-draft__body strong { max-width: 220px; overflow: hidden; color: var(--el-text-color-primary); text-overflow: ellipsis; white-space: nowrap; }
.quick-entry-draft__body span { color: var(--el-text-color-secondary); font-size: 12px; }
@media (max-width: 1100px) { .quick-entry-page__layout { grid-template-columns: minmax(0, 1fr); } .quick-entry-page__layout aside { order: -1; } }
@media (max-width: 700px) { .quick-entry-page { padding: 16px; } .quick-entry-selectors, .quick-entry-fields { grid-template-columns: 1fr; gap: 0; } .quick-entry-review__footer { align-items: flex-start; flex-direction: column; } }
</style>
