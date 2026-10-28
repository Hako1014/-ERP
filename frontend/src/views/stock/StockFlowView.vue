<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="出入库查询"
      description="按仓库、商品、库位、业务类型和来源单据追溯库存流水。"
    />

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
          <el-select
            v-model="directionFilter"
            class="stock-filter"
            placeholder="全部方向"
            @change="search"
          >
            <el-option label="全部方向" value="" />
            <el-option label="入库" value="IN" />
            <el-option label="出库" value="OUT" />
          </el-select>
          <el-select
            v-model="bizFilter"
            class="stock-filter"
            clearable
            placeholder="全部业务类型"
            @change="search"
          >
            <el-option
              v-for="(label, code) in STOCK_FLOW_BIZ_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
          <el-date-picker
            v-model="timeRange"
            class="stock-filter--date"
            type="datetimerange"
            value-format="YYYY-MM-DDTHH:mm:ss"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            @change="search"
          />
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
        empty-description="暂无库存流水"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #direction="{ row }">
          <StatusTag
            :status="row.direction"
            :label="directionLabel(row.direction)"
            :tone="directionTone(row.direction)"
          />
        </template>
        <template #biz="{ row }">
          {{ labelOf(STOCK_FLOW_BIZ_LABELS, row.biz_type) }}
        </template>
        <template #spec="{ row }">
          {{ row.spec_code || row.spec_name || '商品级' }}
        </template>
        <template #cost="{ row }">
          <MoneyText :value="row.total_cost" />
        </template>
        <template #flowTime="{ row }">
          {{ formatDateTime(row.flow_time || row.created_at) }}
        </template>
      </DataTable>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { stockQueryApi, warehouseApi, type StockFlow, type Warehouse } from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  STOCK_FLOW_BIZ_LABELS,
  directionLabel,
  directionTone,
  labelOf,
} from '@/views/stock/stockHelpers'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const columns: DataTableColumn<StockFlow>[] = [
  { label: '出入库时间', slot: 'flowTime', minWidth: 160 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 110 },
  { prop: 'product_code', label: '商品编码', minWidth: 120 },
  { prop: 'product_name', label: '商品名称', minWidth: 170 },
  { label: '规格', slot: 'spec', minWidth: 120 },
  { prop: 'location_code', label: '库位', minWidth: 160 },
  { label: '方向', slot: 'direction', width: 85, align: 'center' },
  { label: '业务类型', slot: 'biz', width: 110 },
  { prop: 'qty', label: '数量', width: 90, align: 'right' },
  { label: '成本金额', slot: 'cost', width: 110, align: 'right' },
  { prop: 'source_no', label: '来源单号', minWidth: 160 },
  { prop: 'order_no', label: '订单号', minWidth: 150 },
]

const rows = ref<StockFlow[]>([])
const warehouses = ref<Warehouse[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const warehouseFilter = ref<number | null>(null)
const directionFilter = ref('')
const bizFilter = ref('')
const timeRange = ref<[string, string] | null>(null)

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    warehouseFilter.value !== null ||
    Boolean(directionFilter.value) ||
    Boolean(bizFilter.value) ||
    Boolean(timeRange.value),
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
    const result = await stockQueryApi.flows({
      keyword: keyword.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      direction: directionFilter.value || undefined,
      biz_type: bizFilter.value || undefined,
      start_time: timeRange.value?.[0],
      end_time: timeRange.value?.[1],
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存流水加载失败')
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
  directionFilter.value = ''
  bizFilter.value = ''
  timeRange.value = null
  search()
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>
