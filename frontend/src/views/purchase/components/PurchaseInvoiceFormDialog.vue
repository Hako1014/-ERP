<template>
  <FormDialog
    v-model:visible="visible"
    :title="editingId ? '编辑采购发票' : '新增采购发票'"
    width="min(820px, 94vw)"
    :loading="loading"
    @submit="submit"
  >
    <div class="settings-page__form-grid">
      <el-form-item label="供应商" required>
        <el-select v-model="supplierId" filterable placeholder="请选择供应商">
          <el-option
            v-for="item in supplierOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="发票类型" required>
        <el-select v-model="invoiceType" @change="handleTypeChange">
          <el-option
            v-for="item in INVOICE_TYPE_OPTIONS"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="税率（%）" required>
        <el-input v-model="taxRate" inputmode="decimal" />
      </el-form-item>
      <el-form-item label="价税合计" required>
        <el-input
          v-model="amount"
          inputmode="decimal"
          :placeholder="isRedInvoice ? '红字发票请输入负数' : '请输入正数金额'"
        />
      </el-form-item>
      <el-form-item label="业务类型">
        <el-input v-model="businessType" maxlength="32" />
      </el-form-item>
      <el-form-item label="凭证编号">
        <el-input v-model="voucherNo" maxlength="64" />
      </el-form-item>
      <el-form-item class="is-full-width" label="关联入库单" required>
        <el-select
          v-model="stockInIds"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="如无列表，可直接输入入库单 ID 后回车"
        >
          <el-option
            v-for="item in stockInOptions"
            :key="item.id"
            :label="`${item.stock_in_no} · ${item.purchase_order_no || '无采购订单'}`"
            :value="item.id"
          />
        </el-select>
        <div class="purchase-muted">
          发票审核后将按关联入库单分摊不含税成本。
        </div>
      </el-form-item>
      <el-form-item class="is-full-width" label="发票描述">
        <el-input
          v-model="description"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-word-limit
        />
      </el-form-item>
    </div>
  </FormDialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  type PurchaseInvoice,
  type PurchaseInvoicePayload,
  type StockInOption,
} from '@/api/purchase'
import type { PartyRecord } from '@/api/party'
import FormDialog from '@/components/FormDialog.vue'
import {
  INVOICE_TYPE_OPTIONS,
  invoiceDefaultTaxRate,
} from '@/views/purchase/purchaseHelpers'

const visible = defineModel<boolean>('visible', { default: false })

const props = withDefaults(
  defineProps<{
    supplierOptions?: PartyRecord[]
    stockInOptions?: StockInOption[]
    loading?: boolean
  }>(),
  {
    supplierOptions: () => [],
    stockInOptions: () => [],
    loading: false,
  },
)

const emit = defineEmits<{
  submit: [payload: PurchaseInvoicePayload]
}>()

const editingId = ref<number | null>(null)
const supplierId = ref<number | null>(null)
const invoiceType = ref('SPECIAL')
const taxRate = ref('13')
const amount = ref('')
const businessType = ref('')
const voucherNo = ref('')
const description = ref('')
const stockInIds = ref<Array<number | string>>([])

const isRedInvoice = computed(() => invoiceType.value.startsWith('RED_'))

function reset(): void {
  editingId.value = null
  supplierId.value = null
  invoiceType.value = 'SPECIAL'
  taxRate.value = '13'
  amount.value = ''
  businessType.value = ''
  voucherNo.value = ''
  description.value = ''
  stockInIds.value = []
}

function openCreate(supplier?: number): void {
  reset()
  supplierId.value = supplier || null
  visible.value = true
}

function openEdit(record: PurchaseInvoice): void {
  reset()
  editingId.value = record.id
  supplierId.value = record.supplier_id
  invoiceType.value = record.invoice_type
  taxRate.value = String(
    record.tax_rate ?? record.default_tax_rate ?? invoiceDefaultTaxRate(record.invoice_type),
  )
  amount.value = String(record.amount)
  businessType.value = record.business_type || ''
  voucherNo.value = record.voucher_no || ''
  description.value = record.description || ''
  stockInIds.value = [...(record.stock_in_ids || [])]
  visible.value = true
}

function handleTypeChange(value: string): void {
  taxRate.value = invoiceDefaultTaxRate(value)
  if (value.startsWith('RED_')) {
    amount.value = amount.value.startsWith('-')
      ? amount.value
      : amount.value
        ? `-${amount.value}`
        : ''
  } else if (amount.value.startsWith('-')) {
    amount.value = amount.value.slice(1)
  }
}

function submit(): void {
  if (!supplierId.value) {
    ElMessage.warning('请选择供应商')
    return
  }
  if (!amount.value || Number(amount.value) === 0) {
    ElMessage.warning('发票金额不能为 0')
    return
  }
  if (isRedInvoice.value && Number(amount.value) >= 0) {
    ElMessage.warning('红字发票金额必须为负数')
    return
  }
  if (!isRedInvoice.value && Number(amount.value) <= 0) {
    ElMessage.warning('非红字发票金额必须为正数')
    return
  }
  const ids = stockInIds.value
    .map((item) => Number(item))
    .filter((item) => Number.isInteger(item) && item > 0)
  if (!ids.length) {
    ElMessage.warning('请至少关联一张已审核入库单')
    return
  }
  emit('submit', {
    supplier_id: supplierId.value,
    invoice_type: invoiceType.value,
    tax_rate: taxRate.value || null,
    amount: amount.value,
    business_type: businessType.value.trim() || null,
    voucher_no: voucherNo.value.trim() || null,
    description: description.value.trim() || null,
    stock_in_ids: ids,
  })
}

defineExpose({ openCreate, openEdit })
</script>
