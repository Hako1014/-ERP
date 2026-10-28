<template>
  <div class="settings-page purchase-page">
    <SettingsPageHeader
      title="采购发票"
      description="维护采购发票、审核流程、入库单成本分摊、核销状态与红字发票。"
    />

    <section class="settings-page__panel">
      <div class="purchase-toolbar">
        <div class="purchase-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="发票号、凭证号或供应商"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="supplierFilter"
            class="purchase-filter--wide"
            clearable
            filterable
            placeholder="全部供应商"
            @change="search"
          >
            <el-option
              v-for="item in supplierOptions"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="invoiceTypeFilter"
            class="purchase-filter--wide"
            clearable
            placeholder="全部发票类型"
            @change="search"
          >
            <el-option
              v-for="item in INVOICE_TYPE_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-select
            v-model="writeOffFilter"
            class="purchase-filter"
            clearable
            placeholder="全部核销状态"
            @change="search"
          >
            <el-option
              v-for="item in INVOICE_WRITE_OFF_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-select
            v-model="statusFilter"
            class="purchase-filter"
            placeholder="全部状态"
            @change="search"
          >
            <el-option label="全部状态" value="" />
            <el-option label="待审核" value="PENDING" />
            <el-option label="已审核" value="APPROVED" />
            <el-option label="已驳回" value="REJECTED" />
          </el-select>
          <el-button @click="search">查询</el-button>
        </div>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新增采购发票
        </el-button>
      </div>

      <DataTable
        v-model:page="page"
        v-model:page-size="pageSize"
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="total"
        :sync-query="false"
        empty-description="暂无采购发票"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #invoiceType="{ row }">
          {{ invoiceTypeLabel(row.invoice_type) }}
        </template>
        <template #amount="{ row }">
          <MoneyText :value="row.amount" />
        </template>
        <template #taxAmount="{ row }">
          <MoneyText :value="row.tax_amount" />
        </template>
        <template #netAmount="{ row }">
          <MoneyText :value="row.net_amount" />
        </template>
        <template #writeOffAmount="{ row }">
          <MoneyText :value="row.write_off_amount" />
        </template>
        <template #writeOffStatus="{ row }">
          <StatusTag
            :status="row.write_off_status"
            :label="labelOf(PURCHASE_STATUS_LABELS, row.write_off_status)"
            :tone="statusTone(row.write_off_status)"
          />
        </template>
        <template #status="{ row }">
          <StatusTag
            :status="row.status"
            :label="labelOf(PURCHASE_STATUS_LABELS, row.status)"
            :tone="statusTone(row.status)"
          />
        </template>
        <template #audit="{ row }">
          <StatusTag
            :status="row.audit_status"
            :label="labelOf(AUDIT_STATUS_LABELS, row.audit_status)"
            :tone="statusTone(row.audit_status)"
          />
        </template>
        <template #createdAt="{ row }">
          {{ formatDateTime(row.created_at) }}
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </DataTable>
    </section>

    <PurchaseInvoiceFormDialog
      ref="formRef"
      v-model:visible="formVisible"
      :supplier-options="supplierOptions"
      :stock-in-options="stockInOptions"
      :loading="saving"
      @submit="submitForm"
    />

    <PurchaseDetailDrawer
      v-model:visible="detailVisible"
      title="采购发票详情"
      :document="detail"
      :document-no="detail?.invoice_no"
      :loading="detailLoading"
    >
      <template #summary>
        <el-descriptions-item label="供应商">
          {{ detail?.supplier_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="发票类型">
          {{ invoiceTypeLabel(detail?.invoice_type) }}
        </el-descriptions-item>
        <el-descriptions-item label="税率">
          {{ detail?.tax_rate ? `${detail.tax_rate}%` : '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="价税合计">
          <MoneyText :value="detail?.amount" />
        </el-descriptions-item>
        <el-descriptions-item label="税额">
          <MoneyText :value="detail?.tax_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="不含税金额">
          <MoneyText :value="detail?.net_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="已核销金额">
          <MoneyText :value="detail?.write_off_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="核销状态">
          <StatusTag
            v-if="detail"
            :status="detail.write_off_status"
            :label="labelOf(PURCHASE_STATUS_LABELS, detail.write_off_status)"
            :tone="statusTone(detail.write_off_status)"
          />
        </el-descriptions-item>
        <el-descriptions-item label="业务类型">
          {{ detail?.business_type || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="凭证编号">
          {{ detail?.voucher_no || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="关联入库单" :span="2">
          {{ detail?.stock_in_nos?.join('、') || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="发票描述" :span="2">
          {{ detail?.description || '--' }}
        </el-descriptions-item>
      </template>

      <template #items>
        <section class="purchase-detail-section">
          <h3 class="purchase-detail-section__title">成本分摊明细</h3>
          <el-table
            :data="detail?.cost_allocation || []"
            border
            max-height="420"
          >
            <el-table-column prop="stock_in_no" label="入库单号" min-width="160" />
            <el-table-column prop="product_code" label="商品编码" min-width="110" />
            <el-table-column prop="product_name" label="商品名称" min-width="160" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="qty" label="数量" width="90" align="right" />
            <el-table-column label="原金额" width="120" align="right">
              <template #default="{ row }">
                <MoneyText :value="row.original_amount" />
              </template>
            </el-table-column>
            <el-table-column label="分摊金额" width="120" align="right">
              <template #default="{ row }">
                <MoneyText :value="row.allocated_amount" />
              </template>
            </el-table-column>
            <el-table-column label="原成本价" width="120" align="right">
              <template #default="{ row }">
                <MoneyText :value="row.original_cost_price" />
              </template>
            </el-table-column>
            <el-table-column label="新成本价" width="120" align="right">
              <template #default="{ row }">
                <MoneyText :value="row.new_cost_price" />
              </template>
            </el-table-column>
          </el-table>
        </section>
      </template>

      <template #footer>
        <div class="purchase-detail-actions">
          <el-button
            v-if="canManage && detail && canEdit(detail)"
            @click="editDetail"
          >
            编辑发票
          </el-button>
          <el-button
            v-if="canManage && detail && canSubmit(detail)"
            type="primary"
            :loading="actionLoading"
            @click="runAction(detail, 'submit')"
          >
            提交审批
          </el-button>
          <el-button
            v-if="canManage && detail && canAudit(detail)"
            type="success"
            :loading="actionLoading"
            @click="runAction(detail, 'approve')"
          >
            审核通过
          </el-button>
          <el-button
            v-if="canManage && detail && canAudit(detail)"
            type="danger"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'reject')"
          >
            审核不通过
          </el-button>
          <el-button
            v-if="canManage && detail && canUnaudit(detail)"
            type="warning"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'unaudit')"
          >
            弃审
          </el-button>
          <el-button
            v-if="canManage && detail && canEdit(detail)"
            type="danger"
            plain
            @click="remove(detail)"
          >
            删除发票
          </el-button>
        </div>
      </template>
    </PurchaseDetailDrawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { partyApi, type PartyRecord } from '@/api/party'
import {
  purchaseInvoiceApi,
  purchaseReferenceApi,
  type PurchaseInvoice,
  type PurchaseInvoicePayload,
  type StockInOption,
} from '@/api/purchase'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import PurchaseDetailDrawer from '@/views/purchase/components/PurchaseDetailDrawer.vue'
import PurchaseInvoiceFormDialog from '@/views/purchase/components/PurchaseInvoiceFormDialog.vue'
import {
  AUDIT_STATUS_LABELS,
  INVOICE_TYPE_OPTIONS,
  INVOICE_WRITE_OFF_OPTIONS,
  PURCHASE_STATUS_LABELS,
  invoiceTypeLabel,
  labelOf,
  statusTone,
} from '@/views/purchase/purchaseHelpers'
import '@/views/settings/settings.css'
import '@/views/purchase/purchase.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('finance:manage'))

const columns: DataTableColumn<PurchaseInvoice>[] = [
  { prop: 'invoice_no', label: '发票编号', minWidth: 170 },
  { prop: 'supplier_name', label: '供应商', minWidth: 150 },
  { label: '发票类型', slot: 'invoiceType', minWidth: 130 },
  { label: '价税合计', slot: 'amount', width: 130, align: 'right' },
  { label: '税额', slot: 'taxAmount', width: 120, align: 'right' },
  { label: '不含税金额', slot: 'netAmount', width: 130, align: 'right' },
  { label: '已核销金额', slot: 'writeOffAmount', width: 130, align: 'right' },
  { label: '核销状态', slot: 'writeOffStatus', width: 110, align: 'center' },
  { label: '状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 90, fixed: 'right' },
]

const rows = ref<PurchaseInvoice[]>([])
const supplierOptions = ref<PartyRecord[]>([])
const stockInOptions = ref<StockInOption[]>([])
const loading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const supplierFilter = ref<number | null>(null)
const invoiceTypeFilter = ref('')
const writeOffFilter = ref('')
const statusFilter = ref('')
const formVisible = ref(false)
const detailVisible = ref(false)
const detail = ref<PurchaseInvoice | null>(null)
const formRef = ref<InstanceType<typeof PurchaseInvoiceFormDialog>>()

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    supplierFilter.value !== null ||
    Boolean(invoiceTypeFilter.value) ||
    Boolean(writeOffFilter.value) ||
    Boolean(statusFilter.value),
)

function approvalStatus(record: PurchaseInvoice): string {
  return String(record.approval?.state.status || '')
}

function canEdit(record: PurchaseInvoice): boolean {
  return (
    ['PENDING', 'REJECTED'].includes(record.status) &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(approvalStatus(record))
  )
}

function canSubmit(record: PurchaseInvoice): boolean {
  return canEdit(record)
}

function canAudit(record: PurchaseInvoice): boolean {
  return record.status === 'PENDING' && record.audit_status === 'PENDING'
}

function canUnaudit(record: PurchaseInvoice): boolean {
  return record.status === 'APPROVED' && record.audit_status === 'APPROVED'
}

async function loadOptions(): Promise<void> {
  try {
    const suppliers = await partyApi.listSuppliers({
      is_active: true,
      page_size: 200,
    })
    supplierOptions.value = suppliers.items
    if (authStore.hasPermission('module:stock')) {
      const stockIns = await purchaseReferenceApi.listStockIns()
      stockInOptions.value = stockIns.items
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购发票基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await purchaseInvoiceApi.list({
      keyword: keyword.value || undefined,
      supplier_id: supplierFilter.value || undefined,
      invoice_type: invoiceTypeFilter.value || undefined,
      write_off_status: writeOffFilter.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购发票加载失败')
  } finally {
    loading.value = false
  }
}

function search(): void {
  page.value = 1
  void load()
}

function resetAndLoad(): void {
  keyword.value = ''
  supplierFilter.value = null
  invoiceTypeFilter.value = ''
  writeOffFilter.value = ''
  statusFilter.value = ''
  search()
}

function openCreate(): void {
  detail.value = null
  formRef.value?.openCreate(supplierFilter.value || undefined)
}

async function openDetail(record: PurchaseInvoice): Promise<void> {
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await purchaseInvoiceApi.get(record.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购发票详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await purchaseInvoiceApi.get(detail.value.id)
  }
}

function editDetail(): void {
  if (detail.value) {
    formRef.value?.openEdit(detail.value)
  }
}

async function submitForm(payload: PurchaseInvoicePayload): Promise<void> {
  saving.value = true
  try {
    if (detail.value && canEdit(detail.value)) {
      await purchaseInvoiceApi.update(detail.value.id, payload)
      ElMessage.success('采购发票已更新')
    } else {
      await purchaseInvoiceApi.create(payload)
      ElMessage.success('采购发票已创建')
    }
    formVisible.value = false
    await load()
    if (detail.value) {
      await refreshDetail()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购发票保存失败')
  } finally {
    saving.value = false
  }
}

async function runAction(
  record: PurchaseInvoice,
  action: 'submit' | 'approve' | 'reject' | 'unaudit',
): Promise<void> {
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await purchaseInvoiceApi.submit(record.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt(
        '可填写审批意见',
        '审核通过',
        { inputPlaceholder: '可留空' },
      )
      await purchaseInvoiceApi.approve(record.id, value || undefined)
    } else if (action === 'reject') {
      const { value } = await ElMessageBox.prompt(
        '请填写不通过原因',
        '审核不通过',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
        },
      )
      await purchaseInvoiceApi.reject(record.id, value)
    } else {
      const { value } = await ElMessageBox.prompt(
        '请填写弃审原因',
        '弃审采购发票',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写弃审原因',
        },
      )
      await purchaseInvoiceApi.unaudit(record.id, value)
    }
    ElMessage.success('采购发票状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购发票操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function remove(record: PurchaseInvoice): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除采购发票 ${record.invoice_no} 吗？`,
      '删除采购发票',
      { type: 'warning' },
    )
    await purchaseInvoiceApi.remove(record.id)
    detailVisible.value = false
    detail.value = null
    ElMessage.success('采购发票已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购发票删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})
</script>
