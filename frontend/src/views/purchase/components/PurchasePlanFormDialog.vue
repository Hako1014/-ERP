<template>
  <FormDialog
    v-model:visible="visible"
    title="新建采购计划"
    width="min(1120px, 96vw)"
    :loading="loading"
    submit-text="生成计划"
    @submit="submit"
  >
    <div class="settings-page__form-grid">
      <el-alert
        v-if="hasAssistantDraft"
        class="purchase-dialog-tip"
        :title="prefillNotice"
        type="info"
        :closable="false"
        show-icon
      />
      <el-form-item label="供应商" required>
        <el-select
          v-model="supplierId"
          filterable
          placeholder="请选择供应商"
          @change="handleSupplierChange"
        >
          <el-option
            v-for="item in supplierOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="计划采购时间" required>
        <el-date-picker
          v-model="planDate"
          type="datetime"
          value-format="YYYY-MM-DDTHH:mm:ss"
          placeholder="请选择计划采购时间"
          @change="markDirty"
        />
      </el-form-item>
      <el-form-item class="is-full-width" label="备注">
        <el-input
          v-model="remark"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-word-limit
          @input="markDirty"
        />
      </el-form-item>
    </div>

    <p class="purchase-dialog-tip">
      建议数量按前 20 日销量扣减现有库存和在途库存自动测算。自动结果可人工修改，
      同一商品规格仅能保留一条。
    </p>

    <div class="purchase-needs__toolbar">
      <el-input
        v-model="keyword"
        clearable
        class="settings-page__search"
        placeholder="商品编码、货号或名称"
        :disabled="!supplierId"
        @keyup.enter="loadNeeds"
        @clear="loadNeeds"
      />
      <div>
        <el-button
          :disabled="!supplierId"
          :loading="needsLoading"
          @click="loadNeeds"
        >
          重新测算
        </el-button>
        <el-button
          :disabled="!needs.length"
          @click="toggleAllSelected"
        >
          {{ allSelected ? '取消全选' : '全选有建议' }}
        </el-button>
      </div>
    </div>

    <el-table
      v-loading="needsLoading"
      :data="needs"
      border
      row-key="key"
      max-height="460"
      empty-text="请选择供应商后加载采购建议"
    >
      <el-table-column width="50" align="center">
        <template #default="{ row }">
          <el-checkbox v-model="row.selected" @change="markDirty" />
        </template>
      </el-table-column>
      <el-table-column label="商品" min-width="190">
        <template #default="{ row }">
          <div>{{ row.product_name || '--' }}</div>
          <div class="purchase-muted">
            {{ row.product_code || '--' }}
            <span v-if="row.art_no"> · {{ row.art_no }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="规格" min-width="130">
        <template #default="{ row }">
          {{ row.spec_code || row.spec_name || '商品级' }}
        </template>
      </el-table-column>
      <el-table-column label="20 日销量" width="100" align="right">
        <template #default="{ row }">{{ row.sales_20d }}</template>
      </el-table-column>
      <el-table-column label="现有库存" width="100" align="right">
        <template #default="{ row }">{{ row.current_stock }}</template>
      </el-table-column>
      <el-table-column label="在途库存" width="100" align="right">
        <template #default="{ row }">{{ row.in_transit_stock }}</template>
      </el-table-column>
      <el-table-column label="建议数量" width="100" align="right">
        <template #default="{ row }">{{ row.suggested_qty }}</template>
      </el-table-column>
      <el-table-column label="计划采购数量" width="150">
        <template #default="{ row }">
          <el-input
            v-model="row.planned_qty"
            inputmode="decimal"
            placeholder="必填"
            @input="markDirty"
          />
        </template>
      </el-table-column>
      <el-table-column label="采购价" width="140">
        <template #default="{ row }">
          <el-input
            v-model="row.purchase_price"
            inputmode="decimal"
            placeholder="后端取价"
            @input="markDirty"
          />
        </template>
      </el-table-column>
      <el-table-column label="测算金额" width="130" align="right">
        <template #default="{ row }">
          <MoneyText :value="row.estimated_amount" />
        </template>
      </el-table-column>
    </el-table>
  </FormDialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  purchasePlanApi,
  type PurchaseNeedItem,
  type PurchasePlanCreatePayload,
} from '@/api/purchase'
import type { PartyRecord } from '@/api/party'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import { toDateTimeInput } from '@/views/purchase/purchaseHelpers'
import type { AssistantPurchasePlanDraft } from '@/api/assistant'

interface EditableNeed extends PurchaseNeedItem {
  key: string
  selected: boolean
  planned_qty: string
  purchase_price: string
}

const visible = defineModel<boolean>('visible', { default: false })

const props = withDefaults(
  defineProps<{
    supplierOptions?: PartyRecord[]
    loading?: boolean
  }>(),
  {
    supplierOptions: () => [],
    loading: false,
  },
)

const emit = defineEmits<{
  submit: [payload: PurchasePlanCreatePayload]
}>()

const supplierId = ref<number | null>(null)
const planDate = ref(toDateTimeInput(new Date().toISOString()))
const remark = ref('')
const keyword = ref('')
const needsLoading = ref(false)
const needs = ref<EditableNeed[]>([])
const selectedState = reactive(new Map<string, boolean>())
const assistantItemKeys = new Set<string>()
const dirty = ref(false)
const hasAssistantDraft = ref(false)
const draftSource = ref<'assistant' | 'quick-entry'>('assistant')
const prefillNotice = computed(() =>
  `此表单已由${draftSource.value === 'quick-entry' ? '快捷录入' : 'AI 助手'}草稿预填。请逐项核对供应商、日期、商品、数量和采购价；只有点击“生成计划”才会创建单据。`,
)

const allSelected = computed(
  () =>
    needs.value.some((item) => Number(item.suggested_qty) > 0) &&
    needs.value
      .filter((item) => Number(item.suggested_qty) > 0)
      .every((item) => item.selected),
)

function rowKey(item: PurchaseNeedItem): string {
  return `${item.product_id}:${item.sku_id ?? 'none'}`
}

function reset(): void {
  supplierId.value = null
  planDate.value = toDateTimeInput(new Date().toISOString())
  remark.value = ''
  keyword.value = ''
  needs.value = []
  selectedState.clear()
  assistantItemKeys.clear()
  dirty.value = false
  hasAssistantDraft.value = false
  draftSource.value = 'assistant'
}

function open(supplier?: number): void {
  reset()
  supplierId.value = supplier || null
  if (supplierId.value) {
    void loadNeeds()
  }
  visible.value = true
}

async function loadNeeds(): Promise<void> {
  if (!supplierId.value) {
    needs.value = []
    return
  }
  needsLoading.value = true
  try {
    const result = await purchasePlanApi.needs(
      supplierId.value,
      keyword.value.trim() || undefined,
    )
    const previous = new Map(
      needs.value.map((item) => [item.key, item] as const),
    )
    const fetched = result.items.map((item) => {
      const key = rowKey(item)
      const old = previous.get(key)
      const selected =
        selectedState.get(key) ?? Number(item.suggested_qty) > 0
      selectedState.set(key, selected)
      return {
        ...item,
        key,
        selected,
        planned_qty: old?.planned_qty || String(item.suggested_qty || ''),
        purchase_price:
          old?.purchase_price ??
          (item.purchase_price === null || item.purchase_price === undefined
            ? ''
            : String(item.purchase_price)),
      }
    })
    const fetchedKeys = new Set(fetched.map((item) => item.key))
    const retainedAssistantRows = previousItems(previous).filter(
      (item) => assistantItemKeys.has(item.key) && !fetchedKeys.has(item.key),
    )
    needs.value = [...fetched, ...retainedAssistantRows]
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购测算加载失败')
  } finally {
    needsLoading.value = false
  }
}

function previousItems(items: Map<string, EditableNeed>): EditableNeed[] {
  return [...items.values()]
}

function markDirty(): void {
  dirty.value = true
}

function handleSupplierChange(): void {
  markDirty()
  void loadNeeds()
}

function hasUnsavedContent(): boolean {
  return dirty.value
}

function getFormSnapshot(): {
  supplier_id: number | null
  plan_date: string
  remark: string
  items: Array<{
    product_id: number
    sku_id: number | null
    planned_qty: string
    purchase_price: string
  }>
} {
  return {
    supplier_id: supplierId.value,
    plan_date: planDate.value,
    remark: remark.value,
    items: needs.value.filter((item) => item.selected).map((item) => ({
      product_id: item.product_id,
      sku_id: item.sku_id || null,
      planned_qty: String(item.planned_qty),
      purchase_price: item.purchase_price,
    })),
  }
}

function draftNeed(
  draft: AssistantPurchasePlanDraft,
  itemIndex: number,
): EditableNeed {
  const item = draft.payload!.items[itemIndex]
  const detail = draft.items[itemIndex]
  const product = detail?.product.value
  const sku = detail?.sku.value
  const price = item.purchase_price || ''
  return {
    product_id: item.product_id,
    product_name: product?.name || `商品 #${item.product_id}`,
    product_code: product?.code || null,
    art_no: product?.art_no || null,
    sku_id: item.sku_id,
    spec_name: sku?.name || null,
    spec_code: sku?.code || null,
    brand_id: null,
    brand_name: null,
    supplier_id: draft.payload!.supplier_id,
    sales_20d: '0',
    current_stock: '0',
    in_transit_stock: '0',
    in_progress_qty: '0',
    suggested_qty: '0',
    estimated_amount:
      price && Number.isFinite(Number(price))
        ? (Number(price) * Number(item.planned_qty)).toFixed(2)
        : null,
    key: `${item.product_id}:${item.sku_id ?? 'none'}`,
    selected: true,
    planned_qty: item.planned_qty,
    purchase_price: price,
  }
}

async function applyAssistantDraft(
  draft: AssistantPurchasePlanDraft,
  mode: 'merge' | 'replace',
  source: 'assistant' | 'quick-entry' = 'assistant',
): Promise<void> {
  const payload = draft.payload
  if (!draft.ready || !payload) throw new Error('草稿尚未补齐必填字段')

  const merging = mode === 'merge'
  const existingSupplierId = supplierId.value
  const existingPlanDate = planDate.value
  const existingRemark = remark.value
  if (!merging) {
    selectedState.clear()
    assistantItemKeys.clear()
    needs.value.forEach((item) => { item.selected = false })
  }

  supplierId.value = merging && existingSupplierId
    ? existingSupplierId
    : payload.supplier_id
  planDate.value = merging && existingPlanDate
    ? existingPlanDate
    : toDateTimeInput(payload.plan_date)
  remark.value = merging && existingRemark
    ? existingRemark
    : payload.remark || ''

  if (supplierId.value && (supplierId.value !== existingSupplierId || !needs.value.length)) {
    await loadNeeds()
  }
  if (!merging) {
    needs.value.forEach((item) => {
      item.selected = false
      selectedState.set(item.key, false)
    })
  }

  payload.items.forEach((payloadItem, index) => {
    const row = draftNeed(draft, index)
    const existing = needs.value.find((item) => item.key === row.key)
    if (existing && merging) {
      // 合并时已有表单值优先，助手仅补入当前表单缺少的字段/商品。
      existing.selected = true
      selectedState.set(existing.key, true)
    } else if (existing) {
      Object.assign(existing, row)
      selectedState.set(existing.key, true)
    } else {
      needs.value.push(row)
      selectedState.set(row.key, true)
    }
    assistantItemKeys.add(row.key)
  })
  hasAssistantDraft.value = true
  draftSource.value = source
  dirty.value = true
}

function toggleAllSelected(): void {
  markDirty()
  const next = !allSelected.value
  needs.value.forEach((item) => {
    if (Number(item.suggested_qty) > 0) {
      item.selected = next
      selectedState.set(item.key, next)
    }
  })
}

function submit(): void {
  if (!supplierId.value) {
    ElMessage.warning('请选择供应商')
    return
  }
  const selected = needs.value.filter((item) => item.selected)
  if (!selected.length) {
    ElMessage.warning('请至少选择一条采购建议')
    return
  }
  const invalid = selected.find(
    (item) => !item.planned_qty || Number(item.planned_qty) <= 0,
  )
  if (invalid) {
    ElMessage.warning(`请填写 ${invalid.product_name || '商品'} 的计划采购数量`)
    return
  }

  emit('submit', {
    supplier_id: supplierId.value,
    plan_date: planDate.value || null,
    remark: remark.value.trim() || null,
    items: selected.map((item) => ({
      product_id: item.product_id,
      sku_id: item.sku_id || null,
      planned_qty: String(item.planned_qty),
      purchase_price: item.purchase_price || null,
    })),
  })
}

defineExpose({
  open,
  loadNeeds,
  hasUnsavedContent,
  getFormSnapshot,
  applyAssistantDraft,
})
</script>

<style scoped>
.purchase-needs__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

@media (max-width: 720px) {
  .purchase-needs__toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .purchase-needs__toolbar > div {
    display: flex;
    gap: 8px;
  }
}
</style>
