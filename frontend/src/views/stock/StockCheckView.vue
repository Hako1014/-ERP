<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库存盘点"
      description="按仓库生成盘点快照，录入实存、导入导出结果并完成审核调整。"
    >
      <template #actions>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新建盘点单
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
            placeholder="盘点单号"
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
              :label="item.name"
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
            <el-option label="已作废" value="TERMINATED" />
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
        empty-description="暂无库存盘点单"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #lock="{ row }">
          <StatusTag
            :status="row.warehouse_locked ? 'LOCKED' : 'UNLOCKED'"
            :label="row.warehouse_locked ? '仓库已锁盘' : '仓库未锁定'"
            :tone="row.warehouse_locked ? 'warning' : 'info'"
          />
        </template>
        <template #progress="{ row }">
          {{ row.result_entered_count }} / {{ row.item_count }}
        </template>
        <template #difference="{ row }">
          <span class="stock-gain">盘盈 {{ row.gain_total_qty }}</span>
          <span class="stock-loss">盘亏 {{ row.loss_total_qty }}</span>
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
        <template #createdAt="{ row }">
          {{ formatDateTime(row.created_at) }}
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click="openDetail(row, true)">
            详情
          </el-button>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="createVisible"
      title="新建库存盘点单"
      :loading="saving"
      width="620px"
      @submit="submitCreate"
    >
      <p class="stock-muted">
        创建时按当前库位库存生成快照；仓库存在进行中的盘点单时不能重复创建。
      </p>
      <el-form-item label="盘点仓库" required>
        <el-select v-model="createForm.warehouse_id" filterable>
          <el-option
            v-for="item in enabledWarehouses"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="锁定仓库">
        <el-switch v-model="createForm.lock_warehouse" />
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
      :title="`盘点单详情 · ${detail?.check_no || ''}`"
      size="min(980px, 97vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <div class="stock-summary">
          <div class="stock-summary__item">
            <div class="stock-summary__label">盘点商品数</div>
            <div class="stock-summary__value">{{ detail?.item_count || 0 }}</div>
          </div>
          <div class="stock-summary__item">
            <div class="stock-summary__label">已录入实存</div>
            <div class="stock-summary__value">
              {{ detail?.result_entered_count || 0 }}
            </div>
          </div>
          <div class="stock-summary__item">
            <div class="stock-summary__label">账面总量</div>
            <div class="stock-summary__value">
              {{ detail?.current_total_qty || '0' }}
            </div>
          </div>
          <div class="stock-summary__item">
            <div class="stock-summary__label">盘盈 / 盘亏</div>
            <div class="stock-summary__value">
              {{ detail?.gain_total_qty || '0' }} / {{ detail?.loss_total_qty || '0' }}
            </div>
          </div>
        </div>

        <el-descriptions :column="2" border>
          <el-descriptions-item label="盘点仓库">
            {{ detail?.warehouse_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="锁盘">
            {{ detail?.lock_warehouse ? '是' : '否' }}
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
          <el-descriptions-item label="备注" :span="2">
            {{ detail?.remark || '--' }}
          </el-descriptions-item>
        </el-descriptions>

        <section class="stock-detail-section">
          <div class="stock-item__header">
            <h3 class="stock-detail-section__title">盘点明细</h3>
            <div class="stock-detail-actions">
              <el-button
                v-if="canManage && canEditDetail"
                @click="excelVisible = true"
              >
                导入 / 导出
              </el-button>
              <el-button
                v-if="canManage && canEditDetail"
                type="primary"
                :loading="saving"
                @click="saveItems"
              >
                保存实存数量
              </el-button>
            </div>
          </div>
          <el-table :data="detail?.items || []" border max-height="470">
            <el-table-column prop="location_code" label="库位" min-width="180" />
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="current_qty" label="账面数量" width="100" align="right" />
            <el-table-column label="实存数量" width="150" align="right">
              <template #default="{ row }">
                <el-input
                  v-if="canEditDetail"
                  v-model="actualMap[row.id]"
                  inputmode="decimal"
                  placeholder="未盘"
                />
                <span v-else>{{ row.actual_qty ?? '--' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="差异" width="100" align="right">
              <template #default="{ row }">{{ row.difference_qty ?? '--' }}</template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="detail?.approval" class="stock-detail-section">
          <ApprovalFlow
            :doc-type="detail.approval.doc_type"
            :doc-id="detail.approval.doc_id"
            :flow-name="detail.approval.flow_name || '库存盘点审批流'"
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
            v-if="canManage && detail && canEditDetail"
            type="warning"
            plain
            :loading="actionLoading"
            @click="terminate"
          >
            作废盘点
          </el-button>
          <el-button
            v-if="canManage && detail && canEditDetail"
            type="danger"
            plain
            @click="removeDetail"
          >
            删除
          </el-button>
        </div>
      </div>
    </el-drawer>

    <ExcelImportExportDialog
      v-if="detail"
      v-model:visible="excelVisible"
      title="盘点结果导入导出"
      :template-url="STOCK_CHECK_TEMPLATE_URL"
      :import-url="canManage && canEditDetail ? stockCheckImportUrl(detail.id) : undefined"
      :export-url="stockCheckExportUrl(detail.id)"
      :filename="`盘点单-${detail.check_no}.xlsx`"
      @imported="reloadDetail"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  STOCK_CHECK_TEMPLATE_URL,
  stockCheckApi,
  stockCheckExportUrl,
  stockCheckImportUrl,
  warehouseApi,
  type StockCheck,
  type StockCheckPayload,
  type Warehouse,
} from '@/api/stock'
import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import DataTable from '@/components/DataTable.vue'
import ExcelImportExportDialog from '@/components/business/ExcelImportExportDialog.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
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

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<StockCheck>[] = [
  { prop: 'check_no', label: '盘点单号', minWidth: 180 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 130 },
  { label: '锁盘状态', slot: 'lock', width: 115, align: 'center' },
  { label: '录入进度', slot: 'progress', width: 105, align: 'center' },
  { label: '盘盈 / 盘亏', slot: 'difference', width: 190 },
  { label: '单据状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const rows = ref<StockCheck[]>([])
const warehouses = ref<Warehouse[]>([])
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
const createVisible = ref(false)
const detailVisible = ref(false)
const excelVisible = ref(false)
const detail = ref<StockCheck | null>(null)
const actualMap = reactive<Record<number, string>>({})
const createForm = reactive<StockCheckPayload>({
  warehouse_id: 0,
  lock_warehouse: true,
  remark: '',
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    warehouseFilter.value !== null ||
    Boolean(statusFilter.value),
)
const enabledWarehouses = computed(() =>
  warehouses.value.filter((item) => item.is_enabled && !item.is_virtual),
)
const canEditDetail = computed(() =>
  Boolean(detail.value && canEditStockDocument(detail.value)),
)

async function loadOptions(): Promise<void> {
  try {
    warehouses.value = await warehouseApi.list()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '仓库选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await stockCheckApi.list({
      keyword: keyword.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '盘点单加载失败')
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
  search()
}

function openCreate(): void {
  Object.assign(createForm, {
    warehouse_id: enabledWarehouses.value[0]?.id || 0,
    lock_warehouse: true,
    remark: '',
  })
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createForm.warehouse_id) {
    ElMessage.warning('请选择盘点仓库')
    return
  }
  saving.value = true
  try {
    const result = await stockCheckApi.create({
      warehouse_id: createForm.warehouse_id,
      lock_warehouse: createForm.lock_warehouse,
      remark: createForm.remark?.trim() || null,
    })
    ElMessage.success(`盘点单 ${result.check_no} 已创建`)
    createVisible.value = false
    await load()
    await openDetail(result, false)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '盘点单创建失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(row: StockCheck, show = true): Promise<void> {
  if (show) {
    detailVisible.value = true
  }
  detailLoading.value = true
  try {
    detail.value = await stockCheckApi.get(row.id)
    Object.keys(actualMap).forEach((key) => delete actualMap[Number(key)])
    for (const item of detail.value.items || []) {
      actualMap[item.id] =
        item.actual_qty === null || item.actual_qty === undefined
          ? ''
          : String(item.actual_qty)
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '盘点详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function reloadDetail(): Promise<void> {
  if (detail.value) {
    await openDetail(detail.value, false)
  }
}

async function saveItems(): Promise<void> {
  if (!detail.value) {
    return
  }
  const items = (detail.value.items || []).map((item) => ({
    product_id: item.product_id,
    sku_id: item.sku_id,
    location_id: item.location_id,
    actual_qty: actualMap[item.id]?.trim() || null,
    barcode: item.barcode,
  }))
  saving.value = true
  try {
    await stockCheckApi.update(detail.value.id, { items })
    ElMessage.success('盘点实存数量已保存')
    await Promise.all([load(), reloadDetail()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '盘点结果保存失败')
  } finally {
    saving.value = false
  }
}

async function runAction(action: 'submit' | 'approve' | 'reject'): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await stockCheckApi.submit(detail.value.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核通过', {
        inputPlaceholder: '可留空',
      })
      await stockCheckApi.approve(detail.value.id, value || undefined)
    } else {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await stockCheckApi.reject(detail.value.id, value)
    }
    ElMessage.success('盘点单状态已更新')
    await Promise.all([load(), reloadDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '盘点单操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function terminate(): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    const { value } = await ElMessageBox.prompt('请填写作废原因', '作废盘点单', {
      inputValidator: (value) => Boolean(value?.trim()) || '请填写原因',
    })
    await stockCheckApi.terminate(detail.value.id, value)
    ElMessage.success('盘点单已作废')
    await Promise.all([load(), reloadDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '盘点单作废失败')
    }
  }
}

async function removeDetail(): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除盘点单 ${detail.value.check_no} 吗？`,
      '删除盘点单',
      { type: 'warning' },
    )
    await stockCheckApi.remove(detail.value.id)
    detailVisible.value = false
    ElMessage.success('盘点单已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '盘点单删除失败')
    }
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>

<style scoped>
.stock-gain {
  margin-right: 10px;
  color: var(--el-color-success);
}

.stock-loss {
  color: var(--el-color-danger);
}
</style>
