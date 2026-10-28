<template>
  <div class="settings-page purchase-page">
    <SettingsPageHeader
      title="采购预付款"
      description="维护采购预付款申请、订单关联、审批与凭证生成。"
    />

    <section class="settings-page__panel">
      <div class="purchase-toolbar">
        <div class="purchase-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="预付款单号、供应商或采购订单"
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
            v-model="statusFilter"
            class="purchase-filter"
            placeholder="全部状态"
            @change="search"
          >
            <el-option label="全部状态" value="" />
            <el-option label="待审核" value="PENDING" />
            <el-option label="已审核" value="APPROVED" />
            <el-option label="已驳回" value="REJECTED" />
            <el-option label="已终止" value="TERMINATED" />
          </el-select>
          <el-select
            v-model="auditFilter"
            class="purchase-filter"
            placeholder="全部审核状态"
            @change="search"
          >
            <el-option label="全部审核状态" value="" />
            <el-option label="待审核" value="PENDING" />
            <el-option label="已审核" value="APPROVED" />
            <el-option label="已驳回" value="REJECTED" />
          </el-select>
          <el-button @click="search">查询</el-button>
        </div>
        <el-button v-if="canCreate" type="primary" @click="openCreate">
          新增预付款申请
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
        empty-description="暂无采购预付款申请"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #orderNo="{ row }">{{ row.order_no || '未关联' }}</template>
        <template #orderAmount="{ row }">
          <MoneyText :value="row.order_amount" />
        </template>
        <template #prepayAmount="{ row }">
          <MoneyText :value="row.prepay_amount" />
        </template>
        <template #unwrittenAmount="{ row }">
          <MoneyText :value="row.unwritten_amount" />
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

    <PurchasePrepayFormDialog
      ref="formRef"
      v-model:visible="formVisible"
      :supplier-options="supplierOptions"
      :orders="orderOptions"
      :loading="saving"
      @submit="submitForm"
    />

    <PurchaseDetailDrawer
      v-model:visible="detailVisible"
      title="采购预付款详情"
      :document="detail"
      :document-no="detail?.prepay_no"
      :loading="detailLoading"
    >
      <template #summary>
        <el-descriptions-item label="供应商">
          {{ detail?.supplier_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="关联采购订单">
          {{ detail?.order_no || '未关联' }}
        </el-descriptions-item>
        <el-descriptions-item label="订单金额">
          <MoneyText :value="detail?.order_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="预付金额">
          <MoneyText :value="detail?.prepay_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="已核销金额">
          <MoneyText :value="detail?.write_off_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="未核销金额">
          <MoneyText :value="detail?.unwritten_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="开户银行">
          {{ detail?.bank || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="开户账号">
          {{ detail?.account_no || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="账户余额">
          <MoneyText :value="detail?.account_balance" />
        </el-descriptions-item>
        <el-descriptions-item label="制单人">
          {{ detail?.created_by_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="付款说明" :span="2">
          {{ detail?.pay_note || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="核销说明" :span="2">
          {{ detail?.write_off_note || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="凭证" :span="2">
          <span v-if="detail?.voucher">
            {{ detail.voucher.voucher_no }}
          </span>
          <span v-else class="purchase-muted">尚未生成凭证</span>
        </el-descriptions-item>
      </template>
      <template #footer>
        <div class="purchase-detail-actions">
          <el-button
            v-if="canCreate && detail && canEdit(detail)"
            @click="editDetail"
          >
            编辑申请
          </el-button>
          <el-button
            v-if="canCreate && detail && canSubmit(detail)"
            type="primary"
            :loading="actionLoading"
            @click="runAction(detail, 'submit')"
          >
            提交审批
          </el-button>
          <el-button
            v-if="canApprove && detail && canAudit(detail)"
            type="success"
            :loading="actionLoading"
            @click="runAction(detail, 'approve')"
          >
            审核通过
          </el-button>
          <el-button
            v-if="canApprove && detail && canAudit(detail)"
            type="danger"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'reject')"
          >
            审核不通过
          </el-button>
          <el-button
            v-if="canCreate && detail && detail.audit_status === 'APPROVED'"
            type="warning"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'unaudit')"
          >
            弃审
          </el-button>
          <el-button
            v-if="canCreate && detail && canTerminate(detail)"
            type="warning"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'terminate')"
          >
            终止申请
          </el-button>
          <el-button
            v-if="canCreate && detail && canEdit(detail)"
            type="danger"
            plain
            @click="remove(detail)"
          >
            删除申请
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
  purchaseOrderApi,
  purchasePrepayApi,
  type PurchaseOrder,
  type PurchasePrepay,
  type PurchasePrepayPayload,
} from '@/api/purchase'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import PurchaseDetailDrawer from '@/views/purchase/components/PurchaseDetailDrawer.vue'
import PurchasePrepayFormDialog from '@/views/purchase/components/PurchasePrepayFormDialog.vue'
import {
  AUDIT_STATUS_LABELS,
  PURCHASE_STATUS_LABELS,
  labelOf,
  statusTone,
} from '@/views/purchase/purchaseHelpers'
import '@/views/settings/settings.css'
import '@/views/purchase/purchase.css'

const authStore = useAuthStore()
const canCreate = computed(() => authStore.hasPermission('purchase:create'))
const canApprove = computed(() => authStore.hasPermission('purchase:approve'))

const columns: DataTableColumn<PurchasePrepay>[] = [
  { prop: 'prepay_no', label: '预付款单号', minWidth: 170 },
  { prop: 'supplier_name', label: '供应商', minWidth: 150 },
  { label: '采购订单', slot: 'orderNo', minWidth: 160 },
  { label: '订单金额', slot: 'orderAmount', width: 130, align: 'right' },
  { label: '预付金额', slot: 'prepayAmount', width: 130, align: 'right' },
  { label: '未核销金额', slot: 'unwrittenAmount', width: 130, align: 'right' },
  { label: '状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 90, fixed: 'right' },
]

const rows = ref<PurchasePrepay[]>([])
const supplierOptions = ref<PartyRecord[]>([])
const orderOptions = ref<PurchaseOrder[]>([])
const loading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const supplierFilter = ref<number | null>(null)
const statusFilter = ref('')
const auditFilter = ref('')
const formVisible = ref(false)
const detailVisible = ref(false)
const detail = ref<PurchasePrepay | null>(null)
const formRef = ref<InstanceType<typeof PurchasePrepayFormDialog>>()

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    supplierFilter.value !== null ||
    Boolean(statusFilter.value) ||
    Boolean(auditFilter.value),
)

function approvalStatus(record: PurchasePrepay): string {
  return String(record.approval?.state.status || '')
}

function canEdit(record: PurchasePrepay): boolean {
  return ['PENDING', 'REJECTED'].includes(record.status)
}

function canSubmit(record: PurchasePrepay): boolean {
  return (
    ['PENDING', 'REJECTED'].includes(record.status) &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(approvalStatus(record))
  )
}

function canAudit(record: PurchasePrepay): boolean {
  return record.status === 'PENDING' && record.audit_status === 'PENDING'
}

function canTerminate(record: PurchasePrepay): boolean {
  return !['TERMINATED', 'APPROVED'].includes(record.status)
}

async function loadOptions(): Promise<void> {
  try {
    const [suppliers, orders] = await Promise.all([
      partyApi.listSuppliers({ is_active: true, page_size: 200 }),
      purchaseOrderApi.list({ page: 1, page_size: 200 }),
    ])
    supplierOptions.value = suppliers.items
    orderOptions.value = orders.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await purchasePrepayApi.list({
      keyword: keyword.value || undefined,
      supplier_id: supplierFilter.value || undefined,
      status: statusFilter.value || undefined,
      audit_status: auditFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购预付款加载失败')
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
  statusFilter.value = ''
  auditFilter.value = ''
  search()
}

function openCreate(): void {
  detail.value = null
  formRef.value?.openCreate(supplierFilter.value || undefined)
}

async function openDetail(record: PurchasePrepay): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await purchasePrepayApi.get(record.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '预付款详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await purchasePrepayApi.get(detail.value.id)
  }
}

function editDetail(): void {
  if (detail.value) {
    formRef.value?.openEdit(detail.value)
  }
}

async function submitForm(payload: PurchasePrepayPayload): Promise<void> {
  saving.value = true
  try {
    if (detail.value && canEdit(detail.value)) {
      await purchasePrepayApi.update(detail.value.id, payload)
      ElMessage.success('采购预付款申请已更新')
    } else {
      await purchasePrepayApi.create(payload)
      ElMessage.success('采购预付款申请已创建')
    }
    formVisible.value = false
    await load()
    if (detail.value) {
      await refreshDetail()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '预付款保存失败')
  } finally {
    saving.value = false
  }
}

async function runAction(
  record: PurchasePrepay,
  action: 'submit' | 'approve' | 'reject' | 'unaudit' | 'terminate',
): Promise<void> {
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await purchasePrepayApi.submit(record.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt(
        '可填写审批意见',
        '审核通过',
        { inputPlaceholder: '可留空' },
      )
      await purchasePrepayApi.approve(record.id, value || undefined)
    } else if (action === 'reject') {
      const { value } = await ElMessageBox.prompt(
        '请填写不通过原因',
        '审核不通过',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
        },
      )
      await purchasePrepayApi.reject(record.id, value)
    } else {
      const { value } = await ElMessageBox.prompt(
        action === 'unaudit' ? '请填写弃审原因' : '请填写终止原因',
        action === 'unaudit' ? '弃审预付款' : '终止预付款',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写原因',
        },
      )
      if (action === 'unaudit') {
        await purchasePrepayApi.unaudit(record.id, value)
      } else {
        await purchasePrepayApi.terminate(record.id, value)
      }
    }
    ElMessage.success('采购预付款状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '预付款操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function remove(record: PurchasePrepay): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除预付款申请 ${record.prepay_no} 吗？`,
      '删除预付款申请',
      { type: 'warning' },
    )
    await purchasePrepayApi.remove(record.id)
    detailVisible.value = false
    detail.value = null
    ElMessage.success('采购预付款申请已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '预付款删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})
</script>
