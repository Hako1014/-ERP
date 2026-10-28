<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库存预警"
      description="按商品规格查看当前库存和预警库存，支持刷新预警快照。"
    >
      <template #actions>
        <el-button v-if="canManage" type="primary" @click="refresh">
          刷新预警
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
          />
          <el-checkbox v-model="onlyBelow" @change="load">
            仅看低于预警
          </el-checkbox>
          <el-button @click="load">查询</el-button>
        </div>
      </div>

      <DataTable
        :rows="filteredRows"
        :columns="columns"
        :loading="loading"
        :total="filteredRows.length"
        :sync-query="false"
        empty-description="没有符合条件的预警商品"
        empty-action-text="查看全部"
        :show-empty-action="onlyBelow || Boolean(keyword)"
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
        <template #status="{ row }">
          <StatusTag
            :status="row.is_below_warning ? 'WARNING' : 'NORMAL'"
            :label="row.is_below_warning ? '低于预警' : '库存正常'"
            :tone="row.is_below_warning ? 'danger' : 'success'"
          />
        </template>
      </DataTable>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { stockQueryApi, type StockWarning } from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<StockWarning>[] = [
  { label: '商品', slot: 'product', minWidth: 220 },
  { label: '规格', slot: 'spec', minWidth: 130 },
  { prop: 'current_qty', label: '当前库存', width: 110, align: 'right' },
  { prop: 'warn_qty', label: '预警库存', width: 110, align: 'right' },
  { label: '预警状态', slot: 'status', width: 110, align: 'center' },
]

const rows = ref<StockWarning[]>([])
const loading = ref(false)
const onlyBelow = ref(false)
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
    rows.value = await stockQueryApi.warnings(onlyBelow.value)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存预警加载失败')
  } finally {
    loading.value = false
  }
}

async function refresh(): Promise<void> {
  loading.value = true
  try {
    rows.value = await stockQueryApi.refreshWarnings()
    if (onlyBelow.value) {
      rows.value = rows.value.filter((row) => row.is_below_warning)
    }
    ElMessage.success('库存预警已刷新')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存预警刷新失败')
  } finally {
    loading.value = false
  }
}

function reset(): void {
  onlyBelow.value = false
  keyword.value = ''
  void load()
}

onMounted(load)
</script>
