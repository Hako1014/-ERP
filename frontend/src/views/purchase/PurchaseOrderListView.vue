<template>
  <div class="settings-page purchase-page">
    <SettingsPageHeader
      title="采购订单"
      description="维护采购订单、计划转单、到货进度、审核流程、附件与打印。"
    />

    <section class="settings-page__panel">
      <div class="purchase-toolbar">
        <div class="purchase-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="订单号、计划号或供应商"
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
            <el-option label="收货中" value="RECEIVING" />
            <el-option label="已收货" value="RECEIVED" />
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
        <div class="purchase-toolbar__actions">
          <el-button @click="excelVisible = true">导入/导出</el-button>
          <el-button v-if="canCreate" type="primary" @click="openCreate">
            新建采购订单
          </el-button>
        </div>
      </div>

      <DataTable
        v-model:page="page"
        v-model:page-size="pageSize"
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="total"
        :sync-query="false"
        empty-description="暂无采购订单"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #warehouse="{ row }">
          {{ row.warehouse_name || '待指定' }}
        </template>
        <template #totalQty="{ row }">{{ row.total_qty }}</template>
        <template #arrivedQty="{ row }">
          {{ row.arrived_qty }} / {{ row.total_qty }}
        </template>
        <template #totalAmount="{ row }">
          <MoneyText :value="row.total_amount" />
        </template>
        <template #arrival="{ row }">
          <StatusTag
            :status="row.arrival_status"
            :label="labelOf(ARRIVAL_STATUS_LABELS, row.arrival_status)"
            :tone="statusTone(row.arrival_status)"
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
          <div class="settings-page__actions">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" @click="downloadPdf(row)">打印</el-button>
          </div>
        </template>
      </DataTable>
    </section>

    <PurchaseOrderFormDialog
      ref="formRef"
      v-model:visible="formVisible"
      :supplier-options="supplierOptions"
      :warehouse-options="warehouseOptions"
      :loading="saving"
      @submit="submitForm"
    />

    <PurchaseDetailDrawer
      v-model:visible="detailVisible"
      title="采购订单详情"
      :document="detail"
      :document-no="detail?.order_no"
      :loading="detailLoading"
    >
      <template #summary>
        <el-descriptions-item label="供应商">
          {{ detail?.supplier_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="关联计划">
          {{ detail?.plan_no || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="入库仓库">
          {{ detail?.warehouse_name || '待指定' }}
        </el-descriptions-item>
        <el-descriptions-item label="采购人员">
          {{ detail?.purchaser_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="采购总数量">
          {{ detail?.total_qty || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="已到货数量">
          {{ detail?.arrived_qty || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="采购金额">
          <MoneyText :value="detail?.total_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="到货状态">
          <StatusTag
            v-if="detail"
            :status="detail.arrival_status"
            :label="labelOf(ARRIVAL_STATUS_LABELS, detail.arrival_status)"
            :tone="statusTone(detail.arrival_status)"
          />
        </el-descriptions-item>
        <el-descriptions-item label="采购描述" :span="2">
          {{ detail?.description || '--' }}
        </el-descriptions-item>
      </template>
      <template #items>
        <section class="purchase-detail-section">
          <h3 class="purchase-detail-section__title">采购明细</h3>
          <el-table :data="detail?.items || []" row-key="id" border max-height="420">
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="qty" label="采购数量" width="100" align="right" />
            <el-table-column prop="arrived_qty" label="到货数量" width="100" align="right" />
            <el-table-column prop="difference_qty" label="差异数量" width="100" align="right" />
            <el-table-column label="采购价" width="120" align="right">
              <template #default="{ row }"><MoneyText :value="row.purchase_price" /></template>
            </el-table-column>
            <el-table-column label="金额" width="120" align="right">
              <template #default="{ row }"><MoneyText :value="row.amount" /></template>
            </el-table-column>
          </el-table>
        </section>
        <section
          v-if="detail?.attachments?.length"
          class="purchase-detail-section"
        >
          <h3 class="purchase-detail-section__title">附件</h3>
          <div class="purchase-detail-attachments">
            <a
              v-for="(url, index) in detail.attachments"
              :key="url"
              :href="url"
              target="_blank"
              rel="noopener noreferrer"
            >
              附件 {{ index + 1 }}：{{ url }}
            </a>
          </div>
        </section>
      </template>
      <template #footer>
        <div class="purchase-detail-actions">
          <el-button
            v-if="canCreate && detail && canEdit(detail)"
            @click="editDetail"
          >
            编辑订单
          </el-button>
          <el-button @click="detail && downloadPdf(detail)">打印订单</el-button>
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
            v-if="canUnaudit && detail && detail.audit_status === 'APPROVED'"
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
            终止订单
          </el-button>
          <el-button
            v-if="canCreate && detail && canEdit(detail)"
            type="danger"
            plain
            @click="remove(detail)"
          >
            删除订单
          </el-button>
        </div>
      </template>
    </PurchaseDetailDrawer>

    <ExcelImportExportDialog
      v-model:visible="excelVisible"
      title="采购订单导入导出"
      :template-url="PURCHASE_ORDER_TEMPLATE_URL"
      :import-url="canCreate ? PURCHASE_ORDER_IMPORT_URL : undefined"
      :export-url="PURCHASE_ORDER_EXPORT_URL"
      :export-params="exportParams"
      filename="采购订单.xlsx"
      @imported="load"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { partyApi, type PartyRecord } from '@/api/party'
import {
  PURCHASE_ORDER_EXPORT_URL,
  PURCHASE_ORDER_IMPORT_URL,
  PURCHASE_ORDER_TEMPLATE_URL,
  purchaseOrderApi,
  purchaseReferenceApi,
  type PurchaseOrder,
  type PurchaseOrderCreatePayload,
  type PurchaseOrderFromPlanPayload,
  type WarehouseOption,
} from '@/api/purchase'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import ExcelImportExportDialog from '@/components/business/ExcelImportExportDialog.vue'
import { saveBlob } from '@/components/business/download'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import {
  clearAssistantSelectedDocument,
  setAssistantSelectedDocument,
} from '@/layouts/components/assistantShell'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import PurchaseDetailDrawer from '@/views/purchase/components/PurchaseDetailDrawer.vue'
import PurchaseOrderFormDialog from '@/views/purchase/components/PurchaseOrderFormDialog.vue'
import {
  ARRIVAL_STATUS_LABELS,
  AUDIT_STATUS_LABELS,
  PURCHASE_STATUS_LABELS,
  labelOf,
  statusTone,
} from '@/views/purchase/purchaseHelpers'
import '@/views/settings/settings.css'
import '@/views/purchase/purchase.css'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const canCreate = computed(() => authStore.hasPermission('purchase:create'))
const canApprove = computed(() => authStore.hasPermission('purchase:approve'))
const canUnaudit = computed(() => canCreate.value || canApprove.value)

const columns: DataTableColumn<PurchaseOrder>[] = [
  { prop: 'order_no', label: '采购订单号', minWidth: 170 },
  { prop: 'plan_no', label: '采购计划', minWidth: 160 },
  { prop: 'supplier_name', label: '供应商', minWidth: 150 },
  { label: '入库仓库', slot: 'warehouse', minWidth: 120 },
  { label: '采购数量', slot: 'totalQty', width: 100, align: 'right' },
  { label: '到货/采购', slot: 'arrivedQty', width: 120, align: 'right' },
  { label: '采购金额', slot: 'totalAmount', width: 130, align: 'right' },
  { label: '到货状态', slot: 'arrival', width: 100, align: 'center' },
  { label: '订单状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 140, fixed: 'right' },
]

const rows = ref<PurchaseOrder[]>([])
const supplierOptions = ref<PartyRecord[]>([])
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
const auditFilter = ref('')
const detailVisible = ref(false)
const formVisible = ref(false)
const excelVisible = ref(false)
const detail = ref<PurchaseOrder | null>(null)
const formRef = ref<InstanceType<typeof PurchaseOrderFormDialog>>()
let detailLoadSequence = 0

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    supplierFilter.value !== null ||
    Boolean(statusFilter.value) ||
    Boolean(auditFilter.value),
)
const exportParams = computed(() => ({
  supplier_id: supplierFilter.value || undefined,
  status: statusFilter.value || undefined,
  audit_status: auditFilter.value || undefined,
  keyword: keyword.value || undefined,
}))

function canEdit(order: PurchaseOrder): boolean {
  return (
    order.status === 'PENDING' &&
    Number(order.arrived_qty) <= 0 &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(
      String(order.approval?.state.status || ''),
    )
  )
}

function canSubmit(order: PurchaseOrder): boolean {
  return (
    order.status === 'PENDING' &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(
      String(order.approval?.state.status || ''),
    )
  )
}

function canAudit(order: PurchaseOrder): boolean {
  return (
    ['PENDING', 'APPROVED'].includes(order.status) &&
    order.audit_status === 'PENDING'
  )
}

function canTerminate(order: PurchaseOrder): boolean {
  return (
    !['TERMINATED', 'CLOSED', 'RECEIVED'].includes(order.status) &&
    Number(order.arrived_qty) <= 0
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
      warehouseOptions.value = await purchaseReferenceApi.listWarehouses()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await purchaseOrderApi.list({
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
    ElMessage.error(error instanceof Error ? error.message : '采购订单加载失败')
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
  formRef.value?.openCreate()
}

async function openDetail(order: PurchaseOrder): Promise<void> {
  const sequence = ++detailLoadSequence
  const identity = `${authStore.profile?.staff_id || ''}:${authStore.token || ''}`
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  clearAssistantSelectedDocument()
  try {
    const record = await purchaseOrderApi.get(order.id)
    if (
      sequence !== detailLoadSequence ||
      !detailVisible.value ||
      identity !== `${authStore.profile?.staff_id || ''}:${authStore.token || ''}`
    ) return
    detail.value = record
    const staffId = authStore.profile?.staff_id
    if (staffId) {
      setAssistantSelectedDocument(staffId, {
        doc_type: 'purchase_order',
        doc_id: record.id,
      })
    }
  } catch (error) {
    if (sequence === detailLoadSequence) {
      ElMessage.error(error instanceof Error ? error.message : '采购订单详情加载失败')
    }
  } finally {
    if (sequence === detailLoadSequence) detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await purchaseOrderApi.get(detail.value.id)
  }
}

async function submitForm(
  payload: PurchaseOrderCreatePayload | PurchaseOrderFromPlanPayload,
): Promise<void> {
  saving.value = true
  try {
    if ('plan_id' in payload) {
      const order = await purchaseOrderApi.fromPlan(payload)
      ElMessage.success(`采购订单 ${order.order_no} 已生成`)
    } else if (detail.value && canEdit(detail.value)) {
      await purchaseOrderApi.update(detail.value.id, payload)
      ElMessage.success('采购订单已更新')
    } else {
      const order = await purchaseOrderApi.create(payload)
      ElMessage.success(`采购订单 ${order.order_no} 已创建`)
    }
    formVisible.value = false
    await load()
    if (detail.value) {
      await refreshDetail()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购订单保存失败')
  } finally {
    saving.value = false
  }
}

async function editDetail(): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    const order = await purchaseOrderApi.get(detail.value.id)
    formRef.value?.openEdit(order)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购订单加载失败')
  }
}

async function runAction(
  order: PurchaseOrder,
  action: 'submit' | 'approve' | 'reject' | 'unaudit' | 'terminate',
): Promise<void> {
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await purchaseOrderApi.submit(order.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt(
        '可填写审批意见',
        '审核通过',
        { inputPlaceholder: '可留空' },
      )
      await purchaseOrderApi.approve(order.id, value || undefined)
    } else if (action === 'reject') {
      const { value } = await ElMessageBox.prompt(
        '请填写不通过原因',
        '审核不通过',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
        },
      )
      await purchaseOrderApi.reject(order.id, value)
    } else {
      const title = action === 'unaudit' ? '弃审采购订单' : '终止采购订单'
      const { value } = await ElMessageBox.prompt(
        action === 'unaudit' ? '请填写弃审原因' : '请填写终止原因',
        title,
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写原因',
        },
      )
      if (action === 'unaudit') {
        await purchaseOrderApi.unaudit(order.id, value)
      } else {
        await purchaseOrderApi.terminate(order.id, value)
      }
    }
    ElMessage.success('采购订单状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购订单操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function remove(order: PurchaseOrder): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除采购订单 ${order.order_no} 吗？`, '删除订单', {
      type: 'warning',
    })
    await purchaseOrderApi.remove(order.id)
    detailVisible.value = false
    detail.value = null
    ElMessage.success('采购订单已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购订单删除失败')
    }
  }
}

async function downloadPdf(order: PurchaseOrder): Promise<void> {
  try {
    const blob = await purchaseOrderApi.downloadPdf(order.id)
    saveBlob(blob, `采购订单-${order.order_no}.pdf`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购订单打印失败')
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})

watch(
  () => route.query.notificationTargetId,
  async (value) => {
    const rawId = Array.isArray(value) ? value[0] : value
    if (typeof rawId !== 'string' || !/^\d{1,15}$/.test(rawId)) return
    const id = Number(rawId)
    if (!Number.isSafeInteger(id) || id < 1) return
    const identity = `${authStore.profile?.staff_id || ''}:${authStore.token || ''}`
    await openDetail({ id } as PurchaseOrder)
    if (identity !== `${authStore.profile?.staff_id || ''}:${authStore.token || ''}`) return
    const query = { ...route.query }
    delete query.notificationTargetId
    await router.replace({ path: route.path, query })
  },
  { immediate: true },
)

watch(detailVisible, (visible) => {
  if (!visible) {
    detailLoadSequence += 1
    detail.value = null
    clearAssistantSelectedDocument()
  }
})

onBeforeUnmount(() => clearAssistantSelectedDocument())
</script>
