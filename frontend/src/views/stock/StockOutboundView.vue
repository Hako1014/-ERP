<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="采购出库"
      description="由已审核采购退货单生成出库单，完成审批后按库位下架。"
    >
      <template #actions>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新建出库单
        </el-button>
      </template>
    </SettingsPageHeader>

    <section class="settings-page__panel">
      <div class="stock-toolbar">
        <div class="stock-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="出库单号或采购退货单号"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="warehouseFilter"
            class="stock-filter--wide"
            clearable
            filterable
            placeholder="全部仓库"
            @change="search"
          >
            <el-option
              v-for="item in warehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="statusFilter"
            class="stock-filter"
            placeholder="全部状态"
            @change="search"
          >
            <el-option label="全部状态" value="" />
            <el-option label="待处理" value="PENDING" />
            <el-option label="已审核" value="APPROVED" />
            <el-option label="已驳回" value="REJECTED" />
          </el-select>
          <el-select
            v-model="offShelfFilter"
            class="stock-filter"
            placeholder="全部下架状态"
            @change="search"
          >
            <el-option label="全部下架状态" value="" />
            <el-option label="待下架" value="NONE" />
            <el-option label="已下架" value="DONE" />
          </el-select>
          <el-button @click="search">查询</el-button>
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
        empty-description="暂无采购出库单"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #returnNo="{ row }">
          {{ row.purchase_return_no || '--' }}
        </template>
        <template #status="{ row }">
          <StatusTag
            :status="row.status"
            :label="labelOf(STOCK_DOC_STATUS_LABELS, row.status)"
            :tone="stockStatusTone(row.status)"
          />
        </template>
        <template #audit="{ row }">
          <StatusTag
            :status="row.audit_status"
            :label="labelOf(STOCK_AUDIT_LABELS, row.audit_status)"
            :tone="stockStatusTone(row.audit_status)"
          />
        </template>
        <template #offShelf="{ row }">
          <StatusTag
            :status="row.off_shelf_status"
            :label="labelOf(OFF_SHELF_STATUS_LABELS, row.off_shelf_status)"
            :tone="stockStatusTone(row.off_shelf_status)"
          />
        </template>
        <template #createdAt="{ row }">
          {{ formatDateTime(row.created_at) }}
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">
            详情
          </el-button>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="createVisible"
      title="新建采购出库单"
      :loading="saving"
      width="620px"
      @submit="submitCreate"
    >
      <p class="stock-muted">
        仅已审核且未生成出库单的采购退货单可选择；商品明细由退货单自动带入。
      </p>
      <el-form-item label="采购退货单" required>
        <el-select
          v-model="createForm.purchase_return_id"
          filterable
          placeholder="选择采购退货单"
        >
          <el-option
            v-for="item in purchaseReturns"
            :key="item.id"
            :label="`${item.return_no} · ${item.supplier_name || ''} · ${item.warehouse_name || ''}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="备注">
        <el-input
          v-model="createForm.remark"
          type="textarea"
          :rows="3"
          maxlength="255"
          show-word-limit
        />
      </el-form-item>
    </FormDialog>

    <el-drawer
      v-model="detailVisible"
      :title="`采购出库详情 · ${detail?.stock_out_no || ''}`"
      size="min(820px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="出库仓库">
            {{ detail?.warehouse_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="采购退货单">
            {{ detail?.purchase_return_no || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="出库总数量">
            {{ detail?.total_qty || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="单据状态">
            <StatusTag
              v-if="detail"
              :status="detail.status"
              :label="labelOf(STOCK_DOC_STATUS_LABELS, detail.status)"
              :tone="stockStatusTone(detail.status)"
            />
          </el-descriptions-item>
          <el-descriptions-item label="审核状态">
            <StatusTag
              v-if="detail"
              :status="detail.audit_status"
              :label="labelOf(STOCK_AUDIT_LABELS, detail.audit_status)"
              :tone="stockStatusTone(detail.audit_status)"
            />
          </el-descriptions-item>
          <el-descriptions-item label="下架状态">
            <StatusTag
              v-if="detail"
              :status="detail.off_shelf_status"
              :label="labelOf(OFF_SHELF_STATUS_LABELS, detail.off_shelf_status)"
              :tone="stockStatusTone(detail.off_shelf_status)"
            />
          </el-descriptions-item>
          <el-descriptions-item label="制单人">
            {{ detail?.created_by_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="审核人">
            {{ detail?.auditor_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">
            {{ detail?.remark || '--' }}
          </el-descriptions-item>
        </el-descriptions>

        <section class="stock-detail-section">
          <h3 class="stock-detail-section__title">出库明细</h3>
          <el-table :data="detail?.items || []" border max-height="420">
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="qty" label="出库数量" width="100" align="right" />
            <el-table-column prop="location_code" label="实际下架库位" min-width="170">
              <template #default="{ row }">
                {{ row.location_code || '待下架' }}
              </template>
            </el-table-column>
            <el-table-column label="成本价" width="110" align="right">
              <template #default="{ row }">
                <MoneyText :value="row.cost_price" />
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="detail?.approval" class="stock-detail-section">
          <ApprovalFlow
            :doc-type="detail.approval.doc_type"
            :doc-id="detail.approval.doc_id"
            :flow-name="detail.approval.flow_name || '采购出库审批流'"
            :state="detail.approval.state"
            :records="detail.approval.records"
          />
        </section>

        <div class="stock-detail-actions">
          <el-button
            v-if="canManage && detail && canSubmitApproval(detail)"
            type="primary"
            :loading="actionLoading"
            @click="runAction('submit')"
          >
            提交审批
          </el-button>
          <el-button
            v-if="canManage && detail && canAuditApproval(detail)"
            type="success"
            :loading="actionLoading"
            @click="runAction('approve')"
          >
            审核通过
          </el-button>
          <el-button
            v-if="canManage && detail && canAuditApproval(detail)"
            type="danger"
            plain
            :loading="actionLoading"
            @click="runAction('reject')"
          >
            审核不通过
          </el-button>
          <el-button
            v-if="
              canManage &&
              detail &&
              detail.status === 'APPROVED' &&
              detail.off_shelf_status !== 'DONE'
            "
            type="primary"
            @click="openOffShelf(detail)"
          >
            下架
          </el-button>
          <el-button
            v-if="canManage && detail && canEditStockDocument(detail)"
            type="danger"
            plain
            @click="removeDetail"
          >
            删除
          </el-button>
        </div>
      </div>
    </el-drawer>

    <FormDialog
      v-model:visible="offShelfVisible"
      title="采购出库下架"
      :loading="saving"
      width="780px"
      @submit="submitOffShelf"
    >
      <p class="stock-muted">
        下架数量必须覆盖全部应出库数量；后端会校验库位库存与仓库一致性。
      </p>
      <div class="stock-operation-list">
        <div
          v-for="item in offShelfItems"
          :key="item.stock_out_item_id"
          class="stock-operation"
        >
          <div class="stock-operation__header">
            <div>
              <div class="stock-operation__title">
                {{ item.product_code }} · {{ item.product_name }}
              </div>
              <div class="stock-operation__meta">
                {{ item.spec_code || item.spec_name || '商品级' }}，应下架
                {{ item.maxQty }}
              </div>
            </div>
          </div>
          <div class="stock-operation__fields">
            <el-select
              v-model="item.location_id"
              filterable
              placeholder="选择下架库位"
            >
              <el-option
                v-for="location in item.locations"
                :key="location.location_id"
                :label="`${location.location_code} · 可用 ${location.qty}`"
                :value="location.location_id"
              />
            </el-select>
            <el-input
              v-model="item.qty"
              inputmode="decimal"
              placeholder="下架数量"
            />
          </div>
        </div>
      </div>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  purchaseReturnApi,
  type PurchaseReturn,
} from '@/api/purchase'
import {
  stockOutApi,
  stockQueryApi,
  warehouseApi,
  type StockLocationRow,
  type StockOut,
  type StockOutPayload,
  type Warehouse,
} from '@/api/stock'
import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  OFF_SHELF_STATUS_LABELS,
  STOCK_AUDIT_LABELS,
  STOCK_DOC_STATUS_LABELS,
  canAuditApproval,
  canEditStockDocument,
  canSubmitApproval,
  labelOf,
  stockStatusTone,
} from '@/views/stock/stockHelpers'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

interface OffShelfRow {
  stock_out_item_id: number
  product_code?: string | null
  product_name?: string | null
  spec_code?: string | null
  spec_name?: string | null
  maxQty: string
  location_id: number | null
  qty: string
  locations: StockLocationRow[]
}

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<StockOut>[] = [
  { prop: 'stock_out_no', label: '出库单号', minWidth: 180 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 120 },
  { label: '采购退货单', slot: 'returnNo', minWidth: 170 },
  { prop: 'total_qty', label: '出库数量', width: 100, align: 'right' },
  { label: '单据状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '下架状态', slot: 'offShelf', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const rows = ref<StockOut[]>([])
const warehouses = ref<Warehouse[]>([])
const purchaseReturns = ref<PurchaseReturn[]>([])
const loading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const warehouseFilter = ref<number | null>(null)
const statusFilter = ref('')
const offShelfFilter = ref('')
const createVisible = ref(false)
const detailVisible = ref(false)
const offShelfVisible = ref(false)
const detail = ref<StockOut | null>(null)
const offShelfItems = ref<OffShelfRow[]>([])
const createForm = reactive<StockOutPayload>({
  purchase_return_id: 0,
  remark: '',
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    warehouseFilter.value !== null ||
    Boolean(statusFilter.value) ||
    Boolean(offShelfFilter.value),
)

async function loadOptions(): Promise<void> {
  try {
    const [warehouseRows, returnRows] = await Promise.all([
      warehouseApi.list(),
      purchaseReturnApi.list({
        status: 'APPROVED',
        audit_status: 'APPROVED',
        page: 1,
        page_size: 200,
      }),
    ])
    warehouses.value = warehouseRows
    purchaseReturns.value = returnRows.items.filter(
      (item) => !item.stock_out || item.stock_out.status === 'TERMINATED',
    )
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '出库基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await stockOutApi.list({
      keyword: keyword.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      status: statusFilter.value || undefined,
      off_shelf_status: offShelfFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购出库单加载失败')
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
  warehouseFilter.value = null
  statusFilter.value = ''
  offShelfFilter.value = ''
  search()
}

function openCreate(): void {
  createForm.purchase_return_id = 0
  createForm.remark = ''
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createForm.purchase_return_id) {
    ElMessage.warning('请选择采购退货单')
    return
  }
  saving.value = true
  try {
    const result = await stockOutApi.create({
      purchase_return_id: createForm.purchase_return_id,
      remark: createForm.remark?.trim() || null,
    })
    ElMessage.success(`采购出库单 ${result.stock_out_no} 已创建`)
    createVisible.value = false
    await Promise.all([loadOptions(), load()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购出库单创建失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(row: StockOut): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await stockOutApi.get(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '出库单详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await stockOutApi.get(detail.value.id)
  }
}

async function runAction(action: 'submit' | 'approve' | 'reject'): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await stockOutApi.submit(detail.value.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核通过', {
        inputPlaceholder: '可留空',
      })
      await stockOutApi.approve(detail.value.id, value || undefined)
    } else {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await stockOutApi.reject(detail.value.id, value)
    }
    ElMessage.success('出库单状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '出库单操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function openOffShelf(record: StockOut): Promise<void> {
  const detailed = record.items?.length ? record : await stockOutApi.get(record.id)
  const locations = await stockQueryApi.locations({
    warehouse_id: detailed.warehouse_id,
  })
  offShelfItems.value = (detailed.items || []).map((item) => {
    const matched = locations.filter(
      (row) =>
        row.product_id === item.product_id &&
        row.sku_id === item.sku_id &&
        Number(row.qty) > 0,
    )
    return {
      stock_out_item_id: item.id,
      product_code: item.product_code,
      product_name: item.product_name,
      spec_code: item.spec_code,
      spec_name: item.spec_name,
      maxQty: String(item.qty),
      location_id: matched[0]?.location_id || null,
      qty: String(item.qty),
      locations: matched,
    }
  })
  offShelfVisible.value = true
}

async function submitOffShelf(): Promise<void> {
  if (!detail.value) {
    return
  }
  const items = offShelfItems.value.map((item) => ({
    stock_out_item_id: item.stock_out_item_id,
    location_id: Number(item.location_id),
    qty: item.qty.trim(),
  }))
  if (
    items.some(
      (item) =>
        !item.location_id ||
        !item.qty ||
        Number(item.qty) <= 0,
    )
  ) {
    ElMessage.warning('请为每条出库明细选择库位并填写数量')
    return
  }
  saving.value = true
  try {
    await stockOutApi.offShelf(detail.value.id, items)
    ElMessage.success('采购出库下架完成')
    offShelfVisible.value = false
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购出库下架失败')
  } finally {
    saving.value = false
  }
}

async function removeDetail(): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除出库单 ${detail.value.stock_out_no} 吗？`,
      '删除出库单',
      { type: 'warning' },
    )
    await stockOutApi.remove(detail.value.id)
    detailVisible.value = false
    ElMessage.success('采购出库单已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '出库单删除失败')
    }
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>
