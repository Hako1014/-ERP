<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库存调整单"
      description="在同一仓库内按商品规格调整库存库位，并保留审批与流水。"
    >
      <template #actions>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新建调整单
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
            placeholder="调整单号"
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
            <el-option label="已驳回" value="REJECTED" />
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
        empty-description="暂无库存调整单"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
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
          <el-button link type="primary" @click="openDetail(row)">
            详情
          </el-button>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="createVisible"
      :title="editId ? '编辑库存调整单' : '新建库存调整单'"
      :loading="saving"
      width="980px"
      @submit="submitCreate"
    >
      <div class="stock-form-grid">
        <el-form-item label="调整仓库" required>
          <el-select v-model="createForm.warehouse_id" filterable @change="loadLocations">
            <el-option
              v-for="item in selectableWarehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" maxlength="255" />
        </el-form-item>
      </div>

      <section class="stock-detail-section">
        <div class="stock-item__header">
          <h3 class="stock-detail-section__title">调整明细</h3>
          <el-button link type="primary" @click="addItem">
            添加一行
          </el-button>
        </div>
        <p class="stock-muted">
          仅调整库位，不改变仓库总库存；调出与调入库位必须属于当前仓库。
        </p>
        <div class="stock-item-list">
          <div
            v-for="(item, index) in createForm.items"
            :key="item.key"
            class="stock-item"
          >
            <div class="stock-item__header">
              <span class="stock-operation__title">明细 {{ index + 1 }}</span>
              <el-button
                link
                type="danger"
                :disabled="createForm.items.length === 1"
                @click="createForm.items.splice(index, 1)"
              >
                删除
              </el-button>
            </div>
            <div class="stock-item__grid stock-item__grid--adjust">
              <el-select
                v-model="item.product_id"
                filterable
                placeholder="选择商品"
                @change="handleProductChange(item)"
              >
                <el-option
                  v-for="product in products"
                  :key="product.id"
                  :label="`${product.code} · ${product.name}`"
                  :value="product.id"
                />
              </el-select>
              <el-select
                v-model="item.sku_id"
                placeholder="选择规格"
                :disabled="!item.product_id"
                @change="updateItemLocations(item, $event)"
              >
                <el-option
                  v-for="sku in item.skus"
                  :key="sku.id"
                  :label="sku.spec_code || sku.spec_name || `规格 #${sku.id}`"
                  :value="sku.id"
                />
              </el-select>
              <el-select
                v-model="item.from_location_id"
                filterable
                placeholder="调出库位"
              >
                <el-option
                  v-for="location in item.fromLocations"
                  :key="location.location_id"
                  :label="`${location.location_code} · 库存 ${location.qty}`"
                  :value="location.location_id"
                />
              </el-select>
              <el-select
                v-model="item.to_location_id"
                filterable
                placeholder="调入库位"
              >
                <el-option
                  v-for="location in locations"
                  :key="location.id"
                  :label="location.code"
                  :value="location.id"
                />
              </el-select>
              <el-input
                v-model="item.qty"
                inputmode="decimal"
                placeholder="调整数量"
              />
            </div>
          </div>
        </div>
      </section>
    </FormDialog>

    <el-drawer
      v-model="detailVisible"
      :title="`库存调整单详情 · ${detail?.adjust_no || ''}`"
      size="min(860px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="调整仓库">
            {{ detail?.warehouse_name || '--' }}
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
          <el-descriptions-item label="制单人">
            {{ detail?.created_by_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">
            {{ detail?.remark || '--' }}
          </el-descriptions-item>
        </el-descriptions>

        <section class="stock-detail-section">
          <h3 class="stock-detail-section__title">调整明细</h3>
          <el-table :data="detail?.items || []" border max-height="420">
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="from_location_code" label="调出库位" min-width="170" />
            <el-table-column prop="to_location_code" label="调入库位" min-width="170" />
            <el-table-column prop="qty" label="调整数量" width="100" align="right" />
          </el-table>
        </section>

        <section v-if="detail?.approval" class="stock-detail-section">
          <ApprovalFlow
            :doc-type="detail.approval.doc_type"
            :doc-id="detail.approval.doc_id"
            :flow-name="detail.approval.flow_name || '库存调整审批流'"
            :state="detail.approval.state"
            :records="detail.approval.records"
          />
        </section>

        <div class="stock-detail-actions">
          <el-button
            v-if="canManage && detail && canEditStockDocument(detail)"
            @click="editDetail"
          >
            编辑
          </el-button>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { productOptionsApi, type ProductOption, type ProductSkuOption } from '@/api/productOptions'
import {
  locationAdjustApi,
  stockQueryApi,
  warehouseApi,
  type Location,
  type LocationAdjust,
  type StockLocationRow,
  type Warehouse,
} from '@/api/stock'
import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import DataTable from '@/components/DataTable.vue'
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

interface AdjustItemRow {
  key: number
  product_id: number | null
  sku_id: number | null
  from_location_id: number | null
  to_location_id: number | null
  qty: string
  skus: ProductSkuOption[]
  fromLocations: StockLocationRow[]
}

interface CreateForm {
  warehouse_id: number
  remark: string
  items: AdjustItemRow[]
}

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<LocationAdjust>[] = [
  { prop: 'adjust_no', label: '调整单号', minWidth: 180 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 130 },
  { prop: 'remark', label: '备注', minWidth: 220 },
  { label: '单据状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const rows = ref<LocationAdjust[]>([])
const warehouses = ref<Warehouse[]>([])
const products = ref<ProductOption[]>([])
const locations = ref<Location[]>([])
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
const editId = ref<number | null>(null)
const detail = ref<LocationAdjust | null>(null)
let rowKey = 1

const createForm = reactive<CreateForm>({
  warehouse_id: 0,
  remark: '',
  items: [],
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    warehouseFilter.value !== null ||
    Boolean(statusFilter.value),
)
const selectableWarehouses = computed(() =>
  warehouses.value.filter(
    (item) => item.is_enabled && !item.is_virtual && item.enable_location,
  ),
)

function emptyItem(): AdjustItemRow {
  return {
    key: rowKey++,
    product_id: null,
    sku_id: null,
    from_location_id: null,
    to_location_id: null,
    qty: '',
    skus: [],
    fromLocations: [],
  }
}

async function loadOptions(): Promise<void> {
  try {
    const [warehouseRows, productRows] = await Promise.all([
      warehouseApi.list(),
      productOptionsApi.listProducts(undefined, 1, 200),
    ])
    warehouses.value = warehouseRows
    products.value = productRows.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调整基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await locationAdjustApi.list({
      keyword: keyword.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存调整单加载失败')
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

async function loadLocations(): Promise<void> {
  locations.value = createForm.warehouse_id
    ? await warehouseApi.listLocations({
        warehouse_id: createForm.warehouse_id,
        is_enabled: true,
      })
    : []
}

function openCreate(): void {
  editId.value = null
  Object.assign(createForm, {
    warehouse_id: selectableWarehouses.value[0]?.id || 0,
    remark: '',
    items: [emptyItem()],
  })
  void loadLocations()
  createVisible.value = true
}

function addItem(): void {
  createForm.items.push(emptyItem())
}

async function handleProductChange(item: AdjustItemRow): Promise<void> {
  item.sku_id = null
  item.from_location_id = null
  item.fromLocations = []
  if (!item.product_id) {
    item.skus = []
    return
  }
  item.skus = await productOptionsApi.listSkus(item.product_id)
  if (item.skus.length === 1) {
    item.sku_id = item.skus[0].id
  }
}

async function updateItemLocations(item: AdjustItemRow, skuId: number): Promise<void> {
  const stockRows = await stockQueryApi.locations({
    warehouse_id: createForm.warehouse_id,
    product_id: item.product_id || undefined,
    sku_id: skuId,
  })
  item.fromLocations = stockRows.filter((row) => Number(row.qty) > 0)
}

async function submitCreate(): Promise<void> {
  if (!createForm.warehouse_id) {
    ElMessage.warning('请选择仓库')
    return
  }
  const items = createForm.items.map((item) => ({
    product_id: Number(item.product_id),
    sku_id: Number(item.sku_id),
    from_location_id: Number(item.from_location_id),
    to_location_id: Number(item.to_location_id),
    qty: item.qty.trim(),
  }))
  if (
    items.some(
      (item) =>
        !item.product_id ||
        !item.sku_id ||
        !item.from_location_id ||
        !item.to_location_id ||
        Number(item.qty) <= 0,
    )
  ) {
    ElMessage.warning('请完整填写商品、规格、调出/调入库位和数量')
    return
  }
  saving.value = true
  try {
    if (editId.value) {
      await locationAdjustApi.update(editId.value, {
        warehouse_id: createForm.warehouse_id,
        remark: createForm.remark?.trim() || null,
        items,
      })
    } else {
      await locationAdjustApi.create({
        warehouse_id: createForm.warehouse_id,
        remark: createForm.remark?.trim() || null,
        items,
      })
    }
    ElMessage.success(editId.value ? '库存调整单已更新' : '库存调整单已创建')
    createVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存调整单保存失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(row: LocationAdjust): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await locationAdjustApi.get(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调整单详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await locationAdjustApi.get(detail.value.id)
  }
}

async function editDetail(): Promise<void> {
  if (!detail.value) {
    return
  }
  editId.value = detail.value.id
  createForm.warehouse_id = detail.value.warehouse_id
  createForm.remark = detail.value.remark || ''
  await loadLocations()
  const itemRows: AdjustItemRow[] = []
  for (const item of detail.value.items || []) {
    const row: AdjustItemRow = {
      key: rowKey++,
      product_id: item.product_id,
      sku_id: item.sku_id,
      from_location_id: item.from_location_id,
      to_location_id: item.to_location_id,
      qty: String(item.qty),
      skus: [
        {
          id: item.sku_id,
          product_id: item.product_id,
          spec_code: item.spec_code,
          spec_name: item.spec_name,
        },
      ],
      fromLocations: [],
    }
    await updateItemLocations(row, item.sku_id)
    itemRows.push(row)
  }
  createForm.items = itemRows
  detailVisible.value = false
  createVisible.value = true
}

async function runAction(action: 'submit' | 'approve' | 'reject'): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await locationAdjustApi.submit(detail.value.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核通过', {
        inputPlaceholder: '可留空',
      })
      await locationAdjustApi.approve(detail.value.id, value || undefined)
    } else {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await locationAdjustApi.reject(detail.value.id, value)
    }
    ElMessage.success('库存调整单状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '库存调整操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function removeDetail(): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除调整单 ${detail.value.adjust_no} 吗？`,
      '删除调整单',
      { type: 'warning' },
    )
    await locationAdjustApi.remove(detail.value.id)
    detailVisible.value = false
    ElMessage.success('库存调整单已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '调整单删除失败')
    }
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>
