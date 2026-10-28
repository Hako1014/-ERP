<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="店铺库存设置"
      description="按店铺、仓库和商品规格配置独享额度、生效时间及线上同步。"
    >
      <template #actions>
        <el-button
          v-if="canManage"
          :disabled="!rows.length"
          @click="syncSelected"
        >
          同步线上库存
        </el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新增店铺库存设置
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
            placeholder="店铺、商品或规格"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="shopFilter"
            class="stock-filter--wide"
            clearable
            filterable
            placeholder="全部店铺"
            @change="search"
          >
            <el-option
              v-for="item in shops"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
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
            v-model="enabledFilter"
            class="stock-filter"
            clearable
            placeholder="全部状态"
            @change="search"
          >
            <el-option label="已启用" :value="true" />
            <el-option label="已停用" :value="false" />
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
        empty-description="暂无店铺库存设置"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @selection-change="selectedRows = $event"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #selection>
          <el-table-column type="selection" width="46" />
        </template>
        <template #spec="{ row }">
          {{ row.spec_code || row.spec_name || '商品级' }}
        </template>
        <template #shopStock="{ row }">
          {{ row.shop_available_qty }} / {{ row.exclusive_qty }}
        </template>
        <template #available="{ row }">
          {{ row.available_qty }}（占用 {{ row.occupied_qty }}）
        </template>
        <template #effective="{ row }">
          <StatusTag
            :status="row.is_effective ? 'ACTIVE' : 'INACTIVE'"
            :label="row.is_effective ? '生效中' : '未生效'"
            :tone="row.is_effective ? 'success' : 'info'"
          />
        </template>
        <template #sync="{ row }">
          <StatusTag
            :status="row.is_sync ? 'SYNC' : 'LOCAL'"
            :label="row.is_sync ? '参与同步' : '仅本地'"
            :tone="row.is_sync ? 'success' : 'info'"
          />
        </template>
        <template #lastSync="{ row }">
          <div>{{ row.last_sync_qty ?? '--' }}</div>
          <div class="stock-muted">{{ formatDateTime(row.last_sync_at) }}</div>
        </template>
        <template #actions="{ row }">
          <div class="settings-page__actions">
            <el-button
              v-if="canManage"
              link
              type="primary"
              @click="openEdit(row)"
            >
              编辑
            </el-button>
            <el-button
              v-if="canManage"
              link
              type="primary"
              @click="syncOne(row)"
            >
              同步
            </el-button>
            <el-button
              v-if="canManage"
              link
              type="danger"
              @click="remove(row)"
            >
              删除
            </el-button>
          </div>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="formVisible"
      :title="form.id ? '编辑店铺库存设置' : '新增店铺库存设置'"
      :loading="saving"
      width="760px"
      @submit="submitForm"
    >
      <div class="stock-form-grid">
        <el-form-item label="店铺" required>
          <el-select
            v-model="form.shop_id"
            filterable
            :disabled="Boolean(form.id)"
          >
            <el-option
              v-for="item in shops"
              :key="item.id"
              :label="`${item.name}${item.platform_name ? ` · ${item.platform_name}` : ''}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="仓库" required>
          <el-select
            v-model="form.warehouse_id"
            filterable
            :disabled="Boolean(form.id)"
          >
            <el-option
              v-for="item in enabledWarehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="商品" required>
          <el-select
            v-model="form.product_id"
            filterable
            :disabled="Boolean(form.id)"
            @change="loadSkus"
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
          <el-select v-model="form.sku_id" :disabled="Boolean(form.id)">
            <el-option
              v-for="item in skus"
              :key="item.id"
              :label="item.spec_code || item.spec_name || `规格 #${item.id}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="独享额度">
          <el-input v-model="form.exclusive_qty" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="参与线上同步">
          <el-switch v-model="form.is_sync" />
        </el-form-item>
        <el-form-item label="启用设置">
          <el-switch v-model="form.is_enabled" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" maxlength="255" />
        </el-form-item>
        <el-form-item label="生效开始">
          <el-date-picker
            v-model="form.effective_start"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="可不设置"
          />
        </el-form-item>
        <el-form-item label="生效结束">
          <el-date-picker
            v-model="form.effective_end"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="可不设置"
          />
        </el-form-item>
      </div>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { productOptionsApi, type ProductOption, type ProductSkuOption } from '@/api/productOptions'
import { shopApi, type Shop } from '@/api/shop'
import {
  shopStockApi,
  warehouseApi,
  type ShopStock,
  type ShopStockPayload,
  type Warehouse,
} from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<ShopStock>[] = [
  { type: 'selection', label: '', width: 46 },
  { prop: 'shop_name', label: '店铺', minWidth: 150 },
  { prop: 'platform_name', label: '平台', minWidth: 100 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 120 },
  { prop: 'product_code', label: '商品编码', minWidth: 120 },
  { prop: 'product_name', label: '商品名称', minWidth: 170 },
  { label: '规格', slot: 'spec', minWidth: 120 },
  { label: '店铺可用 / 独享', slot: 'shopStock', width: 150, align: 'right' },
  { label: '仓库可用', slot: 'available', width: 170, align: 'right' },
  { label: '生效状态', slot: 'effective', width: 100, align: 'center' },
  { label: '同步', slot: 'sync', width: 100, align: 'center' },
  { label: '最近同步', slot: 'lastSync', width: 140 },
  { label: '操作', slot: 'actions', width: 170, fixed: 'right' },
]

const rows = ref<ShopStock[]>([])
const shops = ref<Shop[]>([])
const warehouses = ref<Warehouse[]>([])
const products = ref<ProductOption[]>([])
const skus = ref<ProductSkuOption[]>([])
const selectedRows = ref<ShopStock[]>([])
const loading = ref(false)
const saving = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const shopFilter = ref<number | null>(null)
const warehouseFilter = ref<number | null>(null)
const enabledFilter = ref<boolean | null>(null)
const formVisible = ref(false)

const form = reactive<ShopStockPayload & { id?: number }>({
  shop_id: 0,
  warehouse_id: 0,
  product_id: 0,
  sku_id: 0,
  exclusive_qty: '0',
  is_sync: false,
  is_enabled: true,
  effective_start: null,
  effective_end: null,
  remark: '',
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    shopFilter.value !== null ||
    warehouseFilter.value !== null ||
    enabledFilter.value !== null,
)
const enabledWarehouses = computed(() =>
  warehouses.value.filter((item) => item.is_enabled && !item.is_virtual),
)

async function loadOptions(): Promise<void> {
  try {
    const [shopRows, warehouseRows, productRows] = await Promise.all([
      shopApi.listShops({ page: 1, page_size: 200 }),
      warehouseApi.list(),
      productOptionsApi.listProducts(undefined, 1, 200),
    ])
    shops.value = shopRows.items
    warehouses.value = warehouseRows
    products.value = productRows.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '店铺库存基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await shopStockApi.list({
      keyword: keyword.value || undefined,
      shop_id: shopFilter.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      is_enabled: enabledFilter.value ?? undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '店铺库存设置加载失败')
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
  shopFilter.value = null
  warehouseFilter.value = null
  enabledFilter.value = null
  search()
}

function openCreate(): void {
  Object.assign(form, {
    id: undefined,
    shop_id: shops.value[0]?.id || 0,
    warehouse_id: enabledWarehouses.value[0]?.id || 0,
    product_id: 0,
    sku_id: 0,
    exclusive_qty: '0',
    is_sync: false,
    is_enabled: true,
    effective_start: null,
    effective_end: null,
    remark: '',
  })
  skus.value = []
  formVisible.value = true
}

async function openEdit(row: ShopStock): Promise<void> {
  if (!row.product_id || !row.sku_id) {
    return
  }
  Object.assign(form, {
    id: row.id,
    shop_id: row.shop_id,
    warehouse_id: row.warehouse_id,
    product_id: row.product_id,
    sku_id: row.sku_id,
    exclusive_qty: String(row.exclusive_qty),
    is_sync: row.is_sync,
    is_enabled: row.is_enabled,
    effective_start: row.effective_start || null,
    effective_end: row.effective_end || null,
    remark: row.remark || '',
  })
  skus.value = await productOptionsApi.listSkus(row.product_id)
  formVisible.value = true
}

async function loadSkus(): Promise<void> {
  form.sku_id = 0
  skus.value = form.product_id
    ? await productOptionsApi.listSkus(form.product_id)
    : []
  if (skus.value.length === 1) {
    form.sku_id = skus.value[0].id
  }
}

async function submitForm(): Promise<void> {
  if (
    !form.shop_id ||
    !form.warehouse_id ||
    !form.product_id ||
    !form.sku_id
  ) {
    ElMessage.warning('请完整选择店铺、仓库、商品和规格')
    return
  }
  saving.value = true
  try {
    const { id, ...payload } = form
    if (id) {
      await shopStockApi.update(id, payload)
    } else {
      await shopStockApi.create(payload)
    }
    ElMessage.success(id ? '店铺库存设置已更新' : '店铺库存设置已创建')
    formVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '店铺库存设置保存失败')
  } finally {
    saving.value = false
  }
}

async function syncOne(row: ShopStock): Promise<void> {
  try {
    await shopStockApi.sync({ ids: [row.id] })
    ElMessage.success('店铺库存同步完成')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '店铺库存同步失败')
  }
}

async function syncSelected(): Promise<void> {
  if (!selectedRows.value.length) {
    ElMessage.warning('请先选择需要同步的店铺库存设置')
    return
  }
  try {
    await shopStockApi.sync({ ids: selectedRows.value.map((row) => row.id) })
    ElMessage.success('店铺库存同步完成')
    selectedRows.value = []
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '店铺库存同步失败')
  }
}

async function remove(row: ShopStock): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除 ${row.shop_name} · ${row.product_name} 的库存设置吗？`,
      '删除店铺库存设置',
      { type: 'warning' },
    )
    await shopStockApi.remove(row.id)
    ElMessage.success('店铺库存设置已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '店铺库存设置删除失败')
    }
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>
