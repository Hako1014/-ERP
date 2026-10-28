<template>
  <FormDialog
    v-model:visible="visible"
    :title="editingId ? '编辑采购预付款' : '新增采购预付款'"
    width="min(780px, 94vw)"
    :loading="loading"
    @submit="submit"
  >
    <div class="settings-page__form-grid">
      <el-form-item label="结算供应商" required>
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
      <el-form-item label="关联采购订单">
        <el-select
          v-model="orderId"
          clearable
          filterable
          placeholder="可不关联订单"
          @change="handleOrderChange"
        >
          <el-option
            v-for="item in availableOrders"
            :key="item.id"
            :label="`${item.order_no} · ${item.supplier_name || ''}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="预付金额" required>
        <el-input
          v-model="prepayAmount"
          inputmode="decimal"
          placeholder="例如 10000.00"
        />
      </el-form-item>
      <el-form-item label="账户余额">
        <el-input
          v-model="accountBalance"
          inputmode="decimal"
          placeholder="可选"
        />
      </el-form-item>
      <el-form-item label="开户银行">
        <el-input v-model="bank" maxlength="64" />
      </el-form-item>
      <el-form-item label="开户账号">
        <el-input v-model="accountNo" maxlength="64" />
      </el-form-item>
      <el-form-item class="is-full-width" label="付款说明">
        <el-input
          v-model="payNote"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-word-limit
        />
      </el-form-item>
      <el-form-item class="is-full-width" label="核销说明">
        <el-input
          v-model="writeOffNote"
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
  type PurchaseOrder,
  type PurchasePrepay,
  type PurchasePrepayPayload,
} from '@/api/purchase'
import type { PartyRecord } from '@/api/party'
import FormDialog from '@/components/FormDialog.vue'

const visible = defineModel<boolean>('visible', { default: false })

const props = withDefaults(
  defineProps<{
    supplierOptions?: PartyRecord[]
    orders?: PurchaseOrder[]
    loading?: boolean
  }>(),
  {
    supplierOptions: () => [],
    orders: () => [],
    loading: false,
  },
)

const emit = defineEmits<{
  submit: [payload: PurchasePrepayPayload]
}>()

const editingId = ref<number | null>(null)
const supplierId = ref<number | null>(null)
const orderId = ref<number | null>(null)
const prepayAmount = ref('')
const bank = ref('')
const accountNo = ref('')
const accountBalance = ref('')
const payNote = ref('')
const writeOffNote = ref('')

const availableOrders = computed(() =>
  supplierId.value
    ? props.orders.filter((item) => item.supplier_id === supplierId.value)
    : props.orders,
)

function reset(): void {
  editingId.value = null
  supplierId.value = null
  orderId.value = null
  prepayAmount.value = ''
  bank.value = ''
  accountNo.value = ''
  accountBalance.value = ''
  payNote.value = ''
  writeOffNote.value = ''
}

function openCreate(supplier?: number, order?: number): void {
  reset()
  supplierId.value = supplier || null
  orderId.value = order || null
  visible.value = true
}

function openEdit(record: PurchasePrepay): void {
  reset()
  editingId.value = record.id
  supplierId.value = record.supplier_id
  orderId.value = record.order_id || null
  prepayAmount.value = String(record.prepay_amount)
  bank.value = record.bank || ''
  accountNo.value = record.account_no || ''
  accountBalance.value =
    record.account_balance === null || record.account_balance === undefined
      ? ''
      : String(record.account_balance)
  payNote.value = record.pay_note || ''
  writeOffNote.value = record.write_off_note || ''
  visible.value = true
}

function handleSupplierChange(): void {
  if (
    orderId.value &&
    !availableOrders.value.some((item) => item.id === orderId.value)
  ) {
    orderId.value = null
  }
}

function handleOrderChange(value?: number): void {
  if (!value) {
    return
  }
  const order = props.orders.find((item) => item.id === value)
  if (order) {
    supplierId.value = order.supplier_id
  }
}

function submit(): void {
  if (!supplierId.value) {
    ElMessage.warning('请选择结算供应商')
    return
  }
  if (!prepayAmount.value || Number(prepayAmount.value) <= 0) {
    ElMessage.warning('预付金额必须大于 0')
    return
  }
  emit('submit', {
    supplier_id: supplierId.value,
    order_id: orderId.value,
    prepay_amount: prepayAmount.value,
    bank: bank.value.trim() || null,
    account_no: accountNo.value.trim() || null,
    account_balance: accountBalance.value || null,
    pay_note: payNote.value.trim() || null,
    write_off_note: writeOffNote.value.trim() || null,
  })
}

defineExpose({ openCreate, openEdit })
</script>
