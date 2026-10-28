<template>
  <div class="settings-page purchase-page">
    <SettingsPageHeader
      title="采购退货单"
      description="维护采购退货、财务与仓库两级审核、自动出库及退款登记状态。"
    />

    <section class="settings-page__panel">
      <div class="purchase-toolbar">
        <div class="purchase-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="退货单号、供应商、采购订单或仓库"
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
            <el-option label="财务已审核" value="FINANCE_APPROVED" />
            <el-option label="已审核" value="APPROVED" />
            <el-option label="已驳回" value="REJECTED" />
            <el-option label="已终止" value="TERMINATED" />
          </el-select>
          <el-button @click="search">查询</el-button>
        </div>
        <el-button v-if="canCreate" type="primary" @click="openCreate">
          新增采购退货单
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
        empty-description="暂无采购退货单"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #orderNo="{ row }">{{ row.order_no || '未关联' }}</template>
        <template #warehouse="{ row }">{{ row.warehouse_name || '--' }}</template>
        <template #totalQty="{ row }">{{ row.total_qty }}</template>
        <template #refundAmount="{ row }">
          <MoneyText :value="row.refund_amount" />
        </template>
        <template #refundStatus="{ row }">
          <StatusTag
            :status="row.refund_status"
            :label="labelOf(PURCHASE_STATUS_LABELS, row.refund_status)"
            :tone="statusTone(row.refund_status)"
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

    <PurchaseReturnFormDialog
      ref="formRef"
      v-model:visible="formVisible"
      :supplier-options="supplierOptions"
      :orders="orderOptions"
      :warehouse-options="warehouseOptions"
      :loading="saving"
      @submit="submitForm"
    />

    <PurchaseDetailDrawer
      v-model:visible="detailVisible"
      title="采购退货单详情"
      :document="detail"
      :document-no="detail?.return_no"
      :loading="detailLoading"
    >
      <template #summary>
        <el-descriptions-item label="供应商">
          {{ detail?.supplier_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="关联采购订单">
          {{ detail?.order_no || '未关联' }}
        </el-descriptions-item>
        <el-descriptions-item label="退货仓库">
          {{ detail?.warehouse_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="自动出库">
          {{ detail?.auto_out ? '是' : '否' }}
        </el-descriptions-item>
        <el-descriptions-item label="退货数量">
          {{ detail?.total_qty || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="已退数量">
          {{ detail?.returned_qty || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="退款金额">
          <MoneyText :value="detail?.refund_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="退款状态">
          <StatusTag
            v-if="detail"
            :status="detail.refund_status"
            :label="labelOf(PURCHASE_STATUS_LABELS, detail.refund_status)"
            :tone="statusTone(detail.refund_status)"
          />
        </el-descriptions-item>
        <el-descriptions-item label="退货原因" :span="2">
          {{ detail?.return_reason || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="退货描述" :span="2">
          {{ detail?.description || '--' }}
        </el-descriptions-item>
      </template>
      <template #items>
        <section class="purchase-detail-section">
          <h3 class="purchase-detail-section__title">退货明细</h3>
          <el-table :data="detail?.items || []" row-key="id" border max-height="420">
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="return_qty" label="退货数量" width="100" align="right" />
            <el-table-column label="退货单价" width="120" align="right">
              <template #default="{ row }"><MoneyText :value="row.unit_price" /></template>
            </el-table-column>
            <el-table-column label="退款金额" width="120" align="right">
              <template #default="{ row }"><MoneyText :value="row.amount" /></template>
            </el-table-column>
          </el-table>
        </section>
        <section
          v-if="detail?.stock_out || detail?.refund_income"
          class="purchase-detail-section"
        >
          <h3 class="purchase-detail-section__title">出库与退款</h3>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="退货出库单">
              {{ detail?.stock_out?.stock_out_no || '尚未生成' }}
            </el-descriptions-item>
            <el-descriptions-item label="下架状态">
              {{
                detail?.stock_out?.off_shelf_status === 'DONE'
                  ? '已完成'
                  : '待完成'
              }}
            </el-descriptions-item>
            <el-descriptions-item label="退款收入单">
              {{ detail?.refund_income?.income_no || '尚未生成' }}
            </el-descriptions-item>
            <el-descriptions-item label="退款金额">
              <MoneyText :value="detail?.refund_income?.amount" />
            </el-descriptions-item>
          </el-descriptions>
        </section>
      </template>
      <template #footer>
        <div class="purchase-detail-actions">
          <el-button
            v-if="canCreate && detail && canEdit(detail)"
            @click="editDetail"
          >
            编辑退货单
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
            v-if="canCreate && detail && canEdit(detail)"
            type="warning"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'terminate')"
          >
            终止退货单
          </el-button>
          <el-button
            v-if="canCreate && detail && canEdit(detail)"
            type="danger"
            plain
            @click="remove(detail)"
          >
            删除退货单
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
  purchaseReferenceApi,
  purchaseReturnApi,
  type PurchaseOrder,
  type PurchaseReturn,
  type PurchaseReturnPayload,
  type WarehouseOption,
} from '@/api/purchase'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import PurchaseDetailDrawer from '@/views/purchase/components/PurchaseDetailDrawer.vue'
import PurchaseReturnFormDialog from '@/views/purchase/components/PurchaseReturnFormDialog.vue'
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
const canApprove = computed(
  () =>
    authStore.hasPermission('purchase:approve') ||
    authStore.hasPermission('stock:manage'),
)

const columns: DataTableColumn<PurchaseReturn>[] = [
  { prop: 'return_no', label: '退货单号', minWidth: 170 },
  { prop: 'supplier_name', label: '供应商', minWidth: 150 },
  { label: '采购订单', slot: 'orderNo', minWidth: 160 },
  { label: '退货仓库', slot: 'warehouse', minWidth: 130 },
  { label: '退货数量', slot: 'totalQty', width: 100, align: 'right' },
  { label: '退款金额', slot: 'refundAmount', width: 130, align: 'right' },
  { label: '退款状态', slot: 'refundStatus', width: 110, align: 'center' },
  { label: '状态', slot: 'status', width: 110, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 90, fixed: 'right' },
]

const rows = ref<PurchaseReturn[]>([])
const supplierOptions = ref<PartyRecord[]>([])
const orderOptions = ref<PurchaseOrder[]>([])
const warehouseOptions = ref<WarehouseOption[]>([])
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
const formVisible = ref(false)
const detailVisible = ref(false)
const detail = ref<PurchaseReturn | null>(null)
const formRef = ref<InstanceType<typeof PurchaseReturnFormDialog>>()

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    supplierFilter.value !== null ||
    Boolean(statusFilter.value),
)

function approvalStatus(record: PurchaseReturn): string {
  return String(record.approval?.state.status || '')
}

function canEdit(record: PurchaseReturn): boolean {
  return (
    ['PENDING', 'REJECTED'].includes(record.status) &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(approvalStatus(record))
  )
}

function canSubmit(record: PurchaseReturn): boolean {
  return canEdit(record)
}

function canAudit(record: PurchaseReturn): boolean {
  return (
    ['PENDING', 'FINANCE_APPROVED'].includes(record.status) &&
    record.audit_status === 'PENDING'
  )
}

async function loadOptions(): Promise<void> {
  try {
    const suppliers = await partyApi.listSuppliers({
      is_active: true,
      page_size: 200,
    })
    supplierOptions.value = suppliers.items
    if (authStore.hasPermission('module:stock')) {
      const [orders, warehouses] = await Promise.all([
        purchaseOrderApi.list({
          audit_status: 'APPROVED',
          page: 1,
          page_size: 200,
        }),
        purchaseReferenceApi.listWarehouses(),
      ])
      orderOptions.value = orders.items
      warehouseOptions.value = warehouses
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await purchaseReturnApi.list({
      keyword: keyword.value || undefined,
      supplier_id: supplierFilter.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购退货单加载失败')
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
  search()
}

function openCreate(): void {
  detail.value = null
  formRef.value?.openCreate(supplierFilter.value || undefined)
}

async function openDetail(record: PurchaseReturn): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await purchaseReturnApi.get(record.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购退货详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await purchaseReturnApi.get(detail.value.id)
  }
}

function editDetail(): void {
  if (detail.value) {
    formRef.value?.openEdit(detail.value)
  }
}

async function submitForm(payload: PurchaseReturnPayload): Promise<void> {
  saving.value = true
  try {
    if (detail.value && canEdit(detail.value)) {
      await purchaseReturnApi.update(detail.value.id, payload)
      ElMessage.success('采购退货单已更新')
    } else {
      await purchaseReturnApi.create(payload)
      ElMessage.success('采购退货单已创建')
    }
    formVisible.value = false
    await load()
    if (detail.value) {
      await refreshDetail()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购退货单保存失败')
  } finally {
    saving.value = false
  }
}

async function runAction(
  record: PurchaseReturn,
  action: 'submit' | 'approve' | 'reject' | 'unaudit' | 'terminate',
): Promise<void> {
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await purchaseReturnApi.submit(record.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt(
        '可填写审批意见',
        '审核通过',
        { inputPlaceholder: '可留空' },
      )
      await purchaseReturnApi.approve(record.id, value || undefined)
    } else if (action === 'reject') {
      const { value } = await ElMessageBox.prompt(
        '请填写不通过原因',
        '审核不通过',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
        },
      )
      await purchaseReturnApi.reject(record.id, value)
    } else {
      const { value } = await ElMessageBox.prompt(
        action === 'unaudit' ? '请填写弃审原因' : '请填写终止原因',
        action === 'unaudit' ? '弃审采购退货单' : '终止采购退货单',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写原因',
        },
      )
      if (action === 'unaudit') {
        await purchaseReturnApi.unaudit(record.id, value)
      } else {
        await purchaseReturnApi.terminate(record.id, value)
      }
    }
    ElMessage.success('采购退货单状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购退货操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function remove(record: PurchaseReturn): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除采购退货单 ${record.return_no} 吗？`,
      '删除采购退货单',
      { type: 'warning' },
    )
    await purchaseReturnApi.remove(record.id)
    detailVisible.value = false
    detail.value = null
    ElMessage.success('采购退货单已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购退货单删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})
</script>
