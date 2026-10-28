<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="采购入库"
      description="维护入库单、审核流程、待上架数量与库位上架记录。"
    >
      <template #actions>
        <el-button
          v-if="canManage"
          type="primary"
          @click="openCreate"
        >
          新建入库单
        </el-button>
      </template>
    </SettingsPageHeader>

    <el-tabs v-model="activeTab" class="stock-tabs" @tab-change="handleTabChange">
      <el-tab-pane label="入库单" name="documents">
        <section class="settings-page__panel">
          <div class="stock-toolbar">
            <div class="stock-toolbar__filters">
              <el-input
                v-model="keyword"
                class="settings-page__search"
                clearable
                placeholder="入库单号或采购订单号"
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
                v-model="auditFilter"
                class="stock-filter"
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
          </div>

          <DataTable
            v-model:page="page"
            v-model:page-size="pageSize"
            :rows="rows"
            :columns="columns"
            :loading="loading"
            :total="total"
            :sync-query="false"
            empty-description="暂无入库单"
            empty-action-text="重置筛选"
            :show-empty-action="hasFilters"
            @query-change="load"
            @empty-action="resetAndLoad"
          >
            <template #purchaseOrder="{ row }">
              {{ row.purchase_order_no || '未关联采购订单' }}
            </template>
            <template #type="{ row }">
              {{ labelOf(STOCK_IN_TYPE_LABELS, row.stock_in_type) }}
            </template>
            <template #progress="{ row }">
              {{ row.putaway_qty }} / {{ row.total_qty }}
            </template>
            <template #pending="{ row }">
              <span :class="{ 'stock-warning-text': Number(row.pending_putaway_qty) > 0 }">
                {{ row.pending_putaway_qty }}
              </span>
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
              <el-button link type="primary" @click="openDetail(row)">
                详情
              </el-button>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>

      <el-tab-pane label="待上架 / 已上架" name="putaway">
        <section class="settings-page__panel">
          <div class="stock-toolbar">
            <div class="stock-toolbar__filters">
              <el-select
                v-model="putawayWarehouseId"
                class="stock-filter--wide"
                clearable
                filterable
                placeholder="全部仓库"
                @change="loadPutawayData"
              >
                <el-option
                  v-for="item in warehouses"
                  :key="item.id"
                  :label="`${item.code} · ${item.name}`"
                  :value="item.id"
                />
              </el-select>
              <el-input
                v-model="putawayKeyword"
                class="stock-filter--wide"
                clearable
                placeholder="商品编码、名称或规格"
                @keyup.enter="loadPutawayData"
                @clear="loadPutawayData"
              />
              <el-button @click="loadPutawayData">刷新</el-button>
            </div>
          </div>

          <el-table
            v-loading="putawayLoading"
            :data="pendingRows"
            border
            max-height="440"
          >
            <el-table-column prop="warehouse_name" label="仓库" min-width="120" />
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="130">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="received_qty" label="已入库" width="100" align="right" />
            <el-table-column prop="putaway_qty" label="已上架" width="100" align="right" />
            <el-table-column
              prop="pending_putaway_qty"
              label="待上架"
              width="100"
              align="right"
            />
            <el-table-column
              v-if="canManage"
              label="操作"
              width="110"
              fixed="right"
            >
              <template #default="{ row }">
                <el-button link type="primary" @click="openPutawayForAggregate(row)">
                  选择入库单
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <section class="stock-detail-section">
            <h3 class="stock-detail-section__title">最近上架记录</h3>
            <el-table :data="putaways" border max-height="360">
              <el-table-column prop="location_code" label="库位" min-width="180" />
              <el-table-column prop="product_code" label="商品编码" min-width="120" />
              <el-table-column prop="product_name" label="商品名称" min-width="170" />
              <el-table-column label="规格" min-width="120">
                <template #default="{ row }">
                  {{ row.spec_code || row.spec_name || '--' }}
                </template>
              </el-table-column>
              <el-table-column prop="qty" label="上架数量" width="100" align="right" />
              <el-table-column label="上架时间" min-width="160">
                <template #default="{ row }">
                  {{ formatDateTime(row.created_at) }}
                </template>
              </el-table-column>
            </el-table>
          </section>
        </section>
      </el-tab-pane>
    </el-tabs>

    <FormDialog
      v-model:visible="createVisible"
      :title="editId ? '编辑入库单' : '新建入库单'"
      :loading="saving"
      width="880px"
      @submit="submitCreate"
    >
      <div class="stock-form-grid">
        <el-form-item label="入库仓库" required>
          <el-select v-model="createForm.warehouse_id" filterable>
            <el-option
              v-for="item in selectableWarehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="入库类型">
          <el-select v-model="createForm.stock_in_type">
            <el-option label="采购入库" value="PURCHASE" />
            <el-option label="手动入库" value="MANUAL" />
            <el-option label="售后退货入库" value="AFTER_SALE" />
            <el-option label="其他调整入库" value="ADJUST" />
          </el-select>
        </el-form-item>
        <el-form-item class="is-full-width" label="关联采购订单">
          <el-select
            v-model="createForm.purchase_order_id"
            clearable
            filterable
            placeholder="可不关联，直接录入商品"
          >
            <el-option
              v-for="item in purchaseOrders"
              :key="item.id"
              :label="`${item.order_no} · ${item.supplier_name || ''}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item class="is-full-width" label="备注">
          <el-input
            v-model="createForm.remark"
            maxlength="255"
            show-word-limit
          />
        </el-form-item>
      </div>

      <section class="stock-detail-section">
        <div class="stock-item__header">
          <h3 class="stock-detail-section__title">入库商品</h3>
          <el-button link type="primary" @click="addCreateItem">
            添加一行
          </el-button>
        </div>
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
                @click="removeCreateItem(index)"
              >
                删除
              </el-button>
            </div>
            <div class="stock-item__grid">
              <el-select
                v-model="item.product_id"
                filterable
                placeholder="选择商品"
                @change="handleCreateProductChange(item)"
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
              >
                <el-option
                  v-for="sku in item.skus"
                  :key="sku.id"
                  :label="sku.spec_code || sku.spec_name || `规格 #${sku.id}`"
                  :value="sku.id"
                />
              </el-select>
              <el-input
                v-model="item.qty"
                inputmode="decimal"
                placeholder="入库数量"
              />
              <el-input
                v-model="item.cost_price"
                inputmode="decimal"
                placeholder="成本价（可选）"
              />
            </div>
          </div>
        </div>
      </section>
    </FormDialog>

    <el-drawer
      v-model="detailVisible"
      :title="`入库单详情 · ${detail?.stock_in_no || ''}`"
      size="min(860px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="入库仓库">
            {{ detail?.warehouse_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="入库类型">
            {{ labelOf(STOCK_IN_TYPE_LABELS, detail?.stock_in_type) }}
          </el-descriptions-item>
          <el-descriptions-item label="关联采购订单">
            {{ detail?.purchase_order_no || '未关联' }}
          </el-descriptions-item>
          <el-descriptions-item label="入库总数量">
            {{ detail?.total_qty || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="已上架">
            {{ detail?.putaway_qty || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="待上架">
            {{ detail?.pending_putaway_qty || '--' }}
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
          <el-descriptions-item label="审核人">
            {{ detail?.auditor_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">
            {{ detail?.remark || '--' }}
          </el-descriptions-item>
        </el-descriptions>

        <section class="stock-detail-section">
          <h3 class="stock-detail-section__title">入库明细</h3>
          <el-table :data="detail?.items || []" border max-height="420">
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="qty" label="入库数量" width="100" align="right" />
            <el-table-column prop="putaway_qty" label="已上架" width="100" align="right" />
            <el-table-column
              prop="pending_putaway_qty"
              label="待上架"
              width="100"
              align="right"
            />
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
            :flow-name="detail.approval.flow_name || '入库单审批流'"
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
            v-if="
              canManage &&
              detail &&
              detail.audit_status === 'APPROVED' &&
              Number(detail.pending_putaway_qty) > 0
            "
            type="primary"
            @click="openPutaway(detail)"
          >
            上架
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
      v-model:visible="putawayVisible"
      title="商品上架"
      :loading="saving"
      width="760px"
      @submit="submitPutaway"
    >
      <p class="stock-muted">
        上架数量不能超过待上架数量，库位容量由后端再次校验。
      </p>
      <div class="stock-operation-list">
        <div
          v-for="item in putawayItems"
          :key="item.stock_in_item_id"
          class="stock-operation"
        >
          <div class="stock-operation__header">
            <div>
              <div class="stock-operation__title">
                {{ item.product_code }} · {{ item.product_name }}
              </div>
              <div class="stock-operation__meta">
                {{ item.spec_code || item.spec_name || '商品级' }}，待上架
                {{ item.maxQty }}
              </div>
            </div>
          </div>
          <div class="stock-operation__fields">
            <el-select
              v-model="item.location_id"
              filterable
              placeholder="选择上架库位"
            >
              <el-option
                v-for="location in locations"
                :key="location.id"
                :label="`${location.code}${location.remaining_qty === null ? ' · 不限容量' : ` · 剩余 ${location.remaining_qty}`}`"
                :value="location.id"
              />
            </el-select>
            <el-input
              v-model="item.qty"
              inputmode="decimal"
              placeholder="上架数量"
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

import { productOptionsApi, type ProductOption, type ProductSkuOption } from '@/api/productOptions'
import { purchaseOrderApi, type PurchaseOrder } from '@/api/purchase'
import {
  stockInApi,
  warehouseApi,
  type Location,
  type PendingPutaway,
  type Putaway,
  type StockIn,
  type StockInPayload,
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
  STOCK_AUDIT_LABELS,
  STOCK_DOC_STATUS_LABELS,
  STOCK_IN_TYPE_LABELS,
  canAuditApproval,
  canEditStockDocument,
  canSubmitApproval,
  labelOf,
  stockStatusTone,
} from '@/views/stock/stockHelpers'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

interface CreateItemRow {
  key: number
  product_id: number | null
  sku_id: number | null
  qty: string
  cost_price: string
  skus: ProductSkuOption[]
}

interface PutawayRow {
  stock_in_item_id: number
  product_code?: string | null
  product_name?: string | null
  spec_code?: string | null
  spec_name?: string | null
  maxQty: string
  location_id: number | null
  qty: string
}

interface CreateForm {
  warehouse_id: number
  purchase_order_id: number | null
  stock_in_type: string
  remark: string
  items: CreateItemRow[]
}

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<StockIn>[] = [
  { prop: 'stock_in_no', label: '入库单号', minWidth: 180 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 120 },
  { label: '关联采购订单', slot: 'purchaseOrder', minWidth: 170 },
  { label: '入库类型', slot: 'type', width: 110 },
  { label: '已上架 / 入库', slot: 'progress', width: 130, align: 'right' },
  { label: '待上架', slot: 'pending', width: 95, align: 'right' },
  { label: '单据状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const activeTab = ref('documents')
const rows = ref<StockIn[]>([])
const warehouses = ref<Warehouse[]>([])
const products = ref<ProductOption[]>([])
const purchaseOrders = ref<PurchaseOrder[]>([])
const locations = ref<Location[]>([])
const pendingRows = ref<PendingPutaway[]>([])
const putaways = ref<Putaway[]>([])
const loading = ref(false)
const putawayLoading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const warehouseFilter = ref<number | null>(null)
const statusFilter = ref('')
const auditFilter = ref('')
const putawayWarehouseId = ref<number | null>(null)
const putawayKeyword = ref('')
const createVisible = ref(false)
const detailVisible = ref(false)
const putawayVisible = ref(false)
const editId = ref<number | null>(null)
const detail = ref<StockIn | null>(null)
const putawayItems = ref<PutawayRow[]>([])
const putawayStockInId = ref<number | null>(null)
let rowKey = 1

const createForm = reactive<CreateForm>({
  warehouse_id: 0,
  purchase_order_id: null,
  stock_in_type: 'PURCHASE',
  remark: '',
  items: [],
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    warehouseFilter.value !== null ||
    Boolean(statusFilter.value) ||
    Boolean(auditFilter.value),
)
const selectableWarehouses = computed(() =>
  warehouses.value.filter((item) => item.selectable),
)

function emptyItem(): CreateItemRow {
  return {
    key: rowKey++,
    product_id: null,
    sku_id: null,
    qty: '',
    cost_price: '',
    skus: [],
  }
}

async function loadOptions(): Promise<void> {
  try {
    const [warehouseRows, productRows, orderRows] = await Promise.all([
      warehouseApi.list(),
      productOptionsApi.listProducts(undefined, 1, 200),
      purchaseOrderApi.list({ page: 1, page_size: 200 }),
    ])
    warehouses.value = warehouseRows
    products.value = productRows.items
    purchaseOrders.value = orderRows.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '入库基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await stockInApi.list({
      keyword: keyword.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      status: statusFilter.value || undefined,
      audit_status: auditFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '入库单加载失败')
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
  auditFilter.value = ''
  search()
}

function handleTabChange(name: string): void {
  if (name === 'putaway') {
    void loadPutawayData()
  }
}

async function loadPutawayData(): Promise<void> {
  putawayLoading.value = true
  try {
    const [pending, records] = await Promise.all([
      stockInApi.pendingPutaways(
        putawayWarehouseId.value || undefined,
        putawayKeyword.value || undefined,
      ),
      stockInApi.listPutaways({
        warehouse_id: putawayWarehouseId.value || undefined,
      }),
    ])
    pendingRows.value = pending
    putaways.value = records.slice(0, 100)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '上架数据加载失败')
  } finally {
    putawayLoading.value = false
  }
}

function openCreate(): void {
  editId.value = null
  Object.assign(createForm, {
    warehouse_id: selectableWarehouses.value[0]?.id || 0,
    purchase_order_id: null,
    stock_in_type: 'PURCHASE',
    remark: '',
    items: [emptyItem()],
  })
  createVisible.value = true
}

function addCreateItem(): void {
  createForm.items.push(emptyItem())
}

function removeCreateItem(index: number): void {
  createForm.items.splice(index, 1)
}

async function handleCreateProductChange(item: CreateItemRow): Promise<void> {
  item.sku_id = null
  item.cost_price = ''
  if (!item.product_id) {
    item.skus = []
    return
  }
  try {
    item.skus = await productOptionsApi.listSkus(item.product_id)
    if (item.skus.length === 1) {
      item.sku_id = item.skus[0].id
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品规格加载失败')
  }
}

async function submitCreate(): Promise<void> {
  if (!createForm.warehouse_id) {
    ElMessage.warning('请选择入库仓库')
    return
  }
  const items = createForm.items.map((item) => ({
    product_id: Number(item.product_id),
    sku_id: item.sku_id,
    qty: item.qty.trim(),
    cost_price: item.cost_price.trim() || null,
  }))
  if (
    items.some(
      (item) =>
        !item.product_id ||
        !item.sku_id ||
        !item.qty ||
        Number(item.qty) <= 0,
    )
  ) {
    ElMessage.warning('请完整填写商品、规格和入库数量')
    return
  }
  saving.value = true
  try {
    const payload: StockInPayload = {
      warehouse_id: createForm.warehouse_id,
      purchase_order_id: createForm.purchase_order_id || null,
      stock_in_type: createForm.stock_in_type,
      remark: createForm.remark.trim() || null,
      items,
    }
    if (editId.value) {
      await stockInApi.update(editId.value, payload)
    } else {
      await stockInApi.create(payload)
    }
    ElMessage.success(editId.value ? '入库单已更新' : '入库单已创建')
    createVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '入库单保存失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(row: StockIn): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await stockInApi.get(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '入库单详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await stockInApi.get(detail.value.id)
  }
}

function editDetail(): void {
  if (!detail.value) {
    return
  }
  editId.value = detail.value.id
  const itemRows: CreateItemRow[] = (detail.value.items || []).map((item) => ({
    key: rowKey++,
    product_id: item.product_id,
    sku_id: item.sku_id || null,
    qty: String(item.qty),
    cost_price: item.cost_price === null ? '' : String(item.cost_price ?? ''),
    skus: item.sku_id
      ? [
          {
            id: item.sku_id,
            product_id: item.product_id,
            spec_code: item.spec_code,
            spec_name: item.spec_name,
          },
        ]
      : [],
  }))
  Object.assign(createForm, {
    warehouse_id: detail.value.warehouse_id,
    purchase_order_id: detail.value.purchase_order_id || null,
    stock_in_type: detail.value.stock_in_type,
    remark: detail.value.remark || '',
    items: itemRows.length ? itemRows : [emptyItem()],
  })
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
      await stockInApi.submit(detail.value.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核通过', {
        inputPlaceholder: '可留空',
      })
      await stockInApi.approve(detail.value.id, value || undefined)
    } else {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await stockInApi.reject(detail.value.id, value)
    }
    ElMessage.success('入库单状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '入库单操作失败')
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
      `确定删除入库单 ${detail.value.stock_in_no} 吗？`,
      '删除入库单',
      { type: 'warning' },
    )
    await stockInApi.remove(detail.value.id)
    detailVisible.value = false
    ElMessage.success('入库单已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '入库单删除失败')
    }
  }
}

async function loadLocations(warehouseId: number): Promise<void> {
  try {
    locations.value = await warehouseApi.listLocations({
      warehouse_id: warehouseId,
      is_enabled: true,
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库位加载失败')
  }
}

async function openPutaway(record: StockIn): Promise<void> {
  await openPutawayRows(record)
}

async function openPutawayRows(record: StockIn): Promise<void> {
  putawayStockInId.value = record.id
  await loadLocations(record.warehouse_id)
  putawayItems.value = (record.items || [])
    .filter((item) => Number(item.pending_putaway_qty) > 0)
    .map((item) => ({
      stock_in_item_id: item.id,
      product_code: item.product_code,
      product_name: item.product_name,
      spec_code: item.spec_code,
      spec_name: item.spec_name,
      maxQty: String(item.pending_putaway_qty),
      location_id: locations.value[0]?.id || null,
      qty: String(item.pending_putaway_qty),
    }))
  if (!putawayItems.value.length) {
    ElMessage.info('当前入库单已全部上架')
    return
  }
  putawayVisible.value = true
}

async function openPutawayForAggregate(row: PendingPutaway): Promise<void> {
  try {
    const result = await stockInApi.list({
      warehouse_id: row.warehouse_id,
      status: 'APPROVED',
      page: 1,
      page_size: 200,
    })
    for (const record of result.items) {
      const detailRecord = await stockInApi.get(record.id)
      const matched = (detailRecord.items || []).some(
        (item) =>
          item.product_id === row.product_id &&
          item.sku_id === row.sku_id &&
          Number(item.pending_putaway_qty) > 0,
      )
      if (matched) {
        await openPutawayRows(detailRecord)
        return
      }
    }
    ElMessage.warning('未找到可上架的入库明细')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '待上架明细加载失败')
  }
}

async function submitPutaway(): Promise<void> {
  if (!putawayStockInId.value || !putawayItems.value.length) {
    return
  }
  const items = putawayItems.value
    .filter((item) => Number(item.qty) > 0)
    .map((item) => ({
      stock_in_item_id: item.stock_in_item_id,
      location_id: Number(item.location_id),
      qty: item.qty.trim(),
    }))
  if (
    !items.length ||
    items.some((item) => !item.location_id || !item.qty)
  ) {
    ElMessage.warning('请选择上架库位并填写数量')
    return
  }
  if (
    putawayItems.value.some(
      (item) =>
        Number(item.qty) < 0 || Number(item.qty) > Number(item.maxQty),
    )
  ) {
    ElMessage.warning('上架数量不能超过待上架数量')
    return
  }
  saving.value = true
  try {
    await stockInApi.putaway(putawayStockInId.value, items)
    ElMessage.success('商品已上架')
    putawayVisible.value = false
    await loadPutawayData()
    await refreshDetail()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品上架失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>

<style scoped>
.stock-warning-text {
  color: var(--el-color-warning);
  font-weight: 600;
}
</style>
