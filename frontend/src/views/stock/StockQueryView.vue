<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库存查询"
      description="按仓库、商品和规格查询结存、占用、可用、预警与库位分布。"
    >
      <template #actions>
        <el-button v-if="canManage" @click="openManual('IN')">
          手动入库
        </el-button>
        <el-button v-if="canManage" @click="openManual('OUT')">
          手动出库
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
            placeholder="商品编码、名称或规格"
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
          <el-checkbox v-model="belowWarning" @change="search">
            仅看低于预警
          </el-checkbox>
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
        empty-description="暂无库存"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #product="{ row }">
          <div class="stock-product">
            <el-image
              v-if="row.product_image"
              class="stock-product__image"
              :src="row.product_image"
              fit="cover"
              preview-teleported
              :preview-src-list="[row.product_image]"
            />
            <div>
              <div>{{ row.product_name }}</div>
              <div class="stock-muted">{{ row.product_code }}</div>
            </div>
          </div>
        </template>
        <template #spec="{ row }">
          {{ row.spec_code || row.spec_name || '商品级' }}
        </template>
        <template #normal="{ row }">{{ row.normal_qty }}</template>
        <template #defective="{ row }">{{ row.defective_qty }}</template>
        <template #total="{ row }">{{ row.total_qty }}</template>
        <template #available="{ row }">{{ row.available_qty }}</template>
        <template #warning="{ row }">
          <StatusTag
            :status="row.is_below_warning ? 'WARNING' : 'NORMAL'"
            :label="row.is_below_warning ? `低于 ${row.warn_qty}` : '正常'"
            :tone="row.is_below_warning ? 'danger' : 'success'"
          />
        </template>
        <template #cost="{ row }">
          <MoneyText :value="row.stock_cost" />
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click="openLocations(row)">
            库位明细
          </el-button>
        </template>
      </DataTable>
    </section>

    <el-drawer
      v-model="locationVisible"
      :title="`库位库存 · ${activeRow?.product_name || ''}`"
      size="min(720px, 94vw)"
      destroy-on-close
    >
      <el-table v-loading="locationLoading" :data="locationRows" border>
        <el-table-column prop="location_code" label="库位" min-width="190" />
        <el-table-column prop="warehouse_name" label="仓库" min-width="120" />
        <el-table-column label="库存状态" width="100">
          <template #default="{ row }">
            <StatusTag
              :status="row.status"
              :label="row.status === 'NORMAL' ? '良品' : '次品'"
              :tone="row.status === 'NORMAL' ? 'success' : 'warning'"
            />
          </template>
        </el-table-column>
        <el-table-column prop="qty" label="库存数量" width="110" align="right" />
        <el-table-column label="容量" width="100" align="right">
          <template #default="{ row }">
            {{ row.location_capacity || '不限' }}
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <FormDialog
      v-model:visible="manualVisible"
      :title="manualDirection === 'IN' ? '手动入库' : '手动出库'"
      :loading="saving"
      width="700px"
      @submit="submitManual"
    >
      <div class="stock-form-grid">
        <el-form-item label="仓库" required>
          <el-select
            v-model="manualForm.warehouse_id"
            filterable
            @change="loadManualLocations"
          >
            <el-option
              v-for="item in manualWarehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="库位" required>
          <el-select v-model="manualForm.location_id" filterable>
            <el-option
              v-for="item in manualLocations"
              :key="item.id"
              :label="item.code"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="商品" required>
          <el-select
            v-model="manualForm.product_id"
            filterable
            @change="loadManualSkus"
          >
            <el-option
              v-for="item in products"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="规格" required>
          <el-select v-model="manualForm.sku_id">
            <el-option
              v-for="item in manualSkus"
              :key="item.id"
              :label="item.spec_code || item.spec_name || `规格 #${item.id}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="数量" required>
          <el-input v-model="manualForm.qty" inputmode="decimal" />
        </el-form-item>
        <el-form-item v-if="manualDirection === 'IN'" label="成本价">
          <el-input v-model="manualForm.cost_price" inputmode="decimal" />
        </el-form-item>
        <el-form-item class="is-full-width" label="备注">
          <el-input v-model="manualForm.remark" maxlength="255" />
        </el-form-item>
      </div>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { productOptionsApi, type ProductOption, type ProductSkuOption } from '@/api/productOptions'
import {
  stockQueryApi,
  warehouseApi,
  type Location,
  type StockLocationRow,
  type StockSummary,
  type Warehouse,
} from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<StockSummary>[] = [
  { label: '商品', slot: 'product', minWidth: 210 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 110 },
  { label: '规格', slot: 'spec', minWidth: 125 },
  { label: '良品', slot: 'normal', width: 90, align: 'right' },
  { label: '次品', slot: 'defective', width: 90, align: 'right' },
  { label: '结存', slot: 'total', width: 90, align: 'right' },
  { label: '占用', prop: 'occupied_qty', width: 90, align: 'right' },
  { label: '可用', slot: 'available', width: 90, align: 'right' },
  { label: '预警', slot: 'warning', width: 115, align: 'center' },
  { label: '库存成本', slot: 'cost', width: 120, align: 'right' },
  { label: '操作', slot: 'actions', width: 100, fixed: 'right' },
]

const rows = ref<StockSummary[]>([])
const warehouses = ref<Warehouse[]>([])
const locationRows = ref<StockLocationRow[]>([])
const products = ref<ProductOption[]>([])
const manualSkus = ref<ProductSkuOption[]>([])
const manualLocations = ref<Location[]>([])
const loading = ref(false)
const locationLoading = ref(false)
const saving = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const warehouseFilter = ref<number | null>(null)
const belowWarning = ref(false)
const locationVisible = ref(false)
const manualVisible = ref(false)
const manualDirection = ref<'IN' | 'OUT'>('IN')
const activeRow = ref<StockSummary | null>(null)

const manualForm = reactive({
  warehouse_id: 0,
  location_id: 0,
  product_id: 0,
  sku_id: 0,
  qty: '',
  cost_price: '',
  remark: '',
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    warehouseFilter.value !== null ||
    belowWarning.value,
)
const manualWarehouses = computed(() =>
  warehouses.value.filter(
    (item) =>
      item.is_enabled &&
      !item.is_virtual &&
      item.enable_location &&
      (manualDirection.value === 'IN' || item.lock_type === 'NONE'),
  ),
)

async function loadOptions(): Promise<void> {
  try {
    const [warehouseRows, productRows] = await Promise.all([
      warehouseApi.list(),
      productOptionsApi.listProducts(undefined, 1, 200),
    ])
    warehouses.value = warehouseRows
    products.value = productRows.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await stockQueryApi.summary({
      keyword: keyword.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      below_warning: belowWarning.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存查询失败')
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
  belowWarning.value = false
  search()
}

async function openLocations(row: StockSummary): Promise<void> {
  activeRow.value = row
  locationVisible.value = true
  locationLoading.value = true
  try {
    locationRows.value = await stockQueryApi.locations({
      warehouse_id: row.warehouse_id,
      product_id: row.product_id,
      sku_id: row.sku_id,
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库位库存加载失败')
  } finally {
    locationLoading.value = false
  }
}

function openManual(direction: 'IN' | 'OUT'): void {
  manualDirection.value = direction
  Object.assign(manualForm, {
    warehouse_id: manualWarehouses.value[0]?.id || 0,
    location_id: 0,
    product_id: 0,
    sku_id: 0,
    qty: '',
    cost_price: '',
    remark: '',
  })
  manualVisible.value = true
  void loadManualLocations()
}

async function loadManualLocations(): Promise<void> {
  manualForm.location_id = 0
  manualLocations.value = manualForm.warehouse_id
    ? await warehouseApi.listLocations({
        warehouse_id: manualForm.warehouse_id,
        is_enabled: true,
      })
    : []
}

async function loadManualSkus(): Promise<void> {
  manualForm.sku_id = 0
  manualSkus.value = manualForm.product_id
    ? await productOptionsApi.listSkus(manualForm.product_id)
    : []
  if (manualSkus.value.length === 1) {
    manualForm.sku_id = manualSkus.value[0].id
  }
}

async function submitManual(): Promise<void> {
  if (
    !manualForm.warehouse_id ||
    !manualForm.location_id ||
    !manualForm.product_id ||
    !manualForm.sku_id ||
    Number(manualForm.qty) <= 0
  ) {
    ElMessage.warning('请完整选择仓库、库位、商品规格并填写数量')
    return
  }
  saving.value = true
  try {
    const payload = {
      warehouse_id: manualForm.warehouse_id,
      location_id: manualForm.location_id,
      product_id: manualForm.product_id,
      sku_id: manualForm.sku_id,
      qty: manualForm.qty.trim(),
      remark: manualForm.remark.trim() || null,
    }
    if (manualDirection.value === 'IN') {
      await stockQueryApi.manualIn({
        ...payload,
        cost_price: manualForm.cost_price.trim() || null,
      })
    } else {
      await stockQueryApi.manualOut(payload)
    }
    ElMessage.success(manualDirection.value === 'IN' ? '手动入库完成' : '手动出库完成')
    manualVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存操作失败')
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
.stock-product {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.stock-product__image {
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  border-radius: 4px;
}
</style>
