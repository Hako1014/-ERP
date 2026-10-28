<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库存成本"
      description="按商品规格查看当前成本、库存数量与库存成本，支持刷新成本快照。"
    >
      <template #actions>
        <el-button v-if="canManage" type="primary" @click="refresh">
          刷新库存成本
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
            @keyup.enter="load"
            @clear="load"
          />
          <el-button @click="load">查询</el-button>
        </div>
      </div>

      <DataTable
        :rows="filteredRows"
        :columns="columns"
        :loading="loading"
        :total="filteredRows.length"
        :sync-query="false"
        empty-description="暂无库存成本数据"
        empty-action-text="清空搜索"
        :show-empty-action="Boolean(keyword)"
        @empty-action="reset"
      >
        <template #product="{ row }">
          <div>
            <div>{{ row.product_name }}</div>
            <div class="stock-muted">{{ row.product_code }}</div>
          </div>
        </template>
        <template #spec="{ row }">
          {{ row.spec_code || row.spec_name || '商品级' }}
        </template>
        <template #cost="{ row }">
          <MoneyText :value="row.cost_price" />
        </template>
        <template #total="{ row }">
          <MoneyText :value="row.total_cost" />
        </template>
      </DataTable>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { stockQueryApi, type StockCost } from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<StockCost>[] = [
  { label: '商品', slot: 'product', minWidth: 220 },
  { label: '规格', slot: 'spec', minWidth: 130 },
  { label: '成本价', slot: 'cost', width: 120, align: 'right' },
  { prop: 'qty', label: '库存数量', width: 110, align: 'right' },
  { label: '库存金额', slot: 'total', width: 130, align: 'right' },
]

const rows = ref<StockCost[]>([])
const loading = ref(false)
const keyword = ref('')

const filteredRows = computed(() => {
  const search = keyword.value.trim().toLowerCase()
  if (!search) {
    return rows.value
  }
  return rows.value.filter((row) =>
    [row.product_code, row.product_name, row.spec_code, row.spec_name]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(search)),
  )
})

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await stockQueryApi.costs()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存成本加载失败')
  } finally {
    loading.value = false
  }
}

async function refresh(): Promise<void> {
  loading.value = true
  try {
    rows.value = await stockQueryApi.refreshCosts()
    ElMessage.success('库存成本已刷新')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存成本刷新失败')
  } finally {
    loading.value = false
  }
}

function reset(): void {
  keyword.value = ''
  void load()
}

onMounted(load)
</script>
