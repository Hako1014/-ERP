<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库存结存"
      description="生成月末库存结存、查看结存明细、导入导出并追溯出库成本回写。"
    >
      <template #actions>
        <el-button @click="excelVisible = true">导入 / 导出</el-button>
        <el-button
          v-if="canManage"
          type="warning"
          plain
          @click="openRewrite"
        >
          出库成本回写
        </el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          生成库存结存
        </el-button>
      </template>
    </SettingsPageHeader>

    <el-tabs v-model="activeTab" class="stock-tabs" @tab-change="handleTabChange">
      <el-tab-pane label="结存明细" name="details">
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
                v-model="settlementDate"
                class="stock-filter"
                clearable
                placeholder="全部结存日期"
                @change="search"
              >
                <el-option
                  v-for="item in periods"
                  :key="item.settlement_date"
                  :label="`${item.settlement_date}（${item.item_count} 条）`"
                  :value="item.settlement_date"
                />
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
            empty-description="暂无库存结存记录"
            empty-action-text="重置筛选"
            :show-empty-action="hasFilters"
            @query-change="load"
            @empty-action="resetAndLoad"
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
      </el-tab-pane>

      <el-tab-pane label="结存期间" name="periods">
        <section class="settings-page__panel">
          <el-table v-loading="periodLoading" :data="periods" border>
            <el-table-column prop="settlement_date" label="结存日期" min-width="140" />
            <el-table-column prop="item_count" label="商品规格数" width="120" align="right" />
            <el-table-column prop="total_qty" label="结存数量" width="120" align="right" />
            <el-table-column label="结存金额" width="140" align="right">
              <template #default="{ row }">
                <MoneyText :value="row.total_cost" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  @click="viewPeriod(row.settlement_date)"
                >
                  查看明细
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </el-tab-pane>

      <el-tab-pane label="成本回写留痕" name="logs">
        <section class="settings-page__panel">
          <div class="stock-toolbar">
            <div class="stock-toolbar__filters">
              <el-input
                v-model="logKeyword"
                class="settings-page__search"
                clearable
                placeholder="批次号、商品或规格"
                @keyup.enter="loadLogs"
                @clear="loadLogs"
              />
              <el-button @click="loadLogs">查询</el-button>
            </div>
          </div>
          <DataTable
            v-model:page="logPage"
            v-model:page-size="logPageSize"
            :rows="logRows"
            :columns="logColumns"
            :loading="logLoading"
            :total="logTotal"
            :sync-query="false"
            empty-description="暂无出库成本回写记录"
            :show-empty-action="false"
            @query-change="loadLogs"
          >
            <template #product="{ row }">
              <div>
                <div>{{ row.product_name }}</div>
                <div class="stock-muted">{{ row.product_code }}</div>
              </div>
            </template>
            <template #target="{ row }">
              {{ row.target_type }} #{{ row.target_id }}
            </template>
            <template #oldCost="{ row }">
              <MoneyText :value="row.old_cost_price" />
            </template>
            <template #newCost="{ row }">
              <MoneyText :value="row.new_cost_price" />
            </template>
            <template #createdAt="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
          </DataTable>
        </section>
      </el-tab-pane>
    </el-tabs>

    <FormDialog
      v-model:visible="createVisible"
      title="生成库存结存"
      :loading="saving"
      width="560px"
      @submit="submitCreate"
    >
      <p class="stock-muted">
        结存日期必须是当月最后一天；也可以填写月份，由后端自动取月末。
      </p>
      <el-form-item label="结存日期">
        <el-date-picker
          v-model="createForm.settlement_date"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="例如 2026-09-30"
        />
      </el-form-item>
      <el-form-item label="或结存月份">
        <el-date-picker
          v-model="createForm.month"
          type="month"
          value-format="YYYY-MM"
          placeholder="例如 2026-09"
        />
      </el-form-item>
    </FormDialog>

    <FormDialog
      v-model:visible="rewriteVisible"
      title="出库成本回写"
      :loading="saving"
      width="560px"
      @submit="submitRewrite"
    >
      <p class="stock-muted">
        按结存期间将历史出库明细和流水成本更新为历史成本，并写入可追溯日志。
      </p>
      <el-form-item label="结存日期">
        <el-date-picker
          v-model="rewriteDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="留空表示当前期间"
        />
      </el-form-item>
    </FormDialog>

    <ExcelImportExportDialog
      v-model:visible="excelVisible"
      title="库存结存导入导出"
      :template-url="STOCK_SETTLEMENT_TEMPLATE_URL"
      :import-url="canManage ? STOCK_SETTLEMENT_IMPORT_URL : undefined"
      :export-url="STOCK_SETTLEMENT_EXPORT_URL"
      :export-params="exportParams"
      filename="库存结存.xlsx"
      @imported="reloadAll"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  STOCK_SETTLEMENT_EXPORT_URL,
  STOCK_SETTLEMENT_IMPORT_URL,
  STOCK_SETTLEMENT_TEMPLATE_URL,
  stockSettlementApi,
  type CostRewriteLog,
  type SettlementPeriod,
  type StockSettlement,
} from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import ExcelImportExportDialog from '@/components/business/ExcelImportExportDialog.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import type { DataTableColumn } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<StockSettlement>[] = [
  { prop: 'settlement_date', label: '结存日期', width: 120 },
  { label: '商品', slot: 'product', minWidth: 220 },
  { label: '规格', slot: 'spec', minWidth: 130 },
  { prop: 'qty', label: '结存数量', width: 110, align: 'right' },
  { label: '成本价', slot: 'cost', width: 120, align: 'right' },
  { label: '结存金额', slot: 'total', width: 130, align: 'right' },
]
const logColumns: DataTableColumn<CostRewriteLog>[] = [
  { prop: 'batch_no', label: '回写批次', minWidth: 160 },
  { label: '商品', slot: 'product', minWidth: 210 },
  { label: '目标', slot: 'target', minWidth: 140 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 110 },
  { prop: 'qty', label: '数量', width: 90, align: 'right' },
  { label: '原成本', slot: 'oldCost', width: 110, align: 'right' },
  { label: '新成本', slot: 'newCost', width: 110, align: 'right' },
  { prop: 'created_by_name', label: '操作人', width: 100 },
  { label: '回写时间', slot: 'createdAt', minWidth: 160 },
]

const activeTab = ref('details')
const rows = ref<StockSettlement[]>([])
const periods = ref<SettlementPeriod[]>([])
const logRows = ref<CostRewriteLog[]>([])
const loading = ref(false)
const periodLoading = ref(false)
const logLoading = ref(false)
const saving = ref(false)
const page = ref(1)
const logPage = ref(1)
const pageSize = ref(20)
const logPageSize = ref(20)
const total = ref(0)
const logTotal = ref(0)
const keyword = ref('')
const logKeyword = ref('')
const settlementDate = ref('')
const createVisible = ref(false)
const rewriteVisible = ref(false)
const excelVisible = ref(false)
const rewriteDate = ref('')
const createForm = reactive<{
  settlement_date: string
  month: string
}>({
  settlement_date: '',
  month: '',
})

const hasFilters = computed(
  () => Boolean(keyword.value) || Boolean(settlementDate.value),
)
const exportParams = computed(() => ({
  settlement_date: settlementDate.value || undefined,
  keyword: keyword.value || undefined,
}))

function clearCreateForm(): void {
  createForm.settlement_date = ''
  createForm.month = ''
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await stockSettlementApi.list({
      keyword: keyword.value || undefined,
      settlement_date: settlementDate.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存结存加载失败')
  } finally {
    loading.value = false
  }
}

async function loadPeriods(): Promise<void> {
  periodLoading.value = true
  try {
    periods.value = await stockSettlementApi.periods()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '结存期间加载失败')
  } finally {
    periodLoading.value = false
  }
}

async function loadLogs(): Promise<void> {
  logLoading.value = true
  try {
    const result = await stockSettlementApi.rewriteLogs({
      keyword: logKeyword.value || undefined,
      page: logPage.value,
      page_size: logPageSize.value,
    })
    logRows.value = result.items
    logTotal.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '成本回写记录加载失败')
  } finally {
    logLoading.value = false
  }
}

function search(): void {
  page.value = 1
  void load()
}

function resetAndLoad(): void {
  keyword.value = ''
  settlementDate.value = ''
  search()
}

function handleTabChange(name: string): void {
  if (name === 'periods') {
    void loadPeriods()
  } else if (name === 'logs') {
    void loadLogs()
  }
}

function viewPeriod(date: string): void {
  settlementDate.value = date
  activeTab.value = 'details'
  search()
}

function openCreate(): void {
  clearCreateForm()
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createForm.settlement_date && !createForm.month) {
    ElMessage.warning('请填写结存日期或结存月份')
    return
  }
  saving.value = true
  try {
    await stockSettlementApi.create({
      settlement_date: createForm.settlement_date || null,
      month: createForm.month || null,
    })
    ElMessage.success('库存结存完成')
    createVisible.value = false
    await reloadAll()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存结存失败')
  } finally {
    saving.value = false
  }
}

function openRewrite(): void {
  rewriteDate.value = ''
  rewriteVisible.value = true
}

async function submitRewrite(): Promise<void> {
  saving.value = true
  try {
    await stockSettlementApi.rewriteCost({
      settlement_date: rewriteDate.value || null,
    })
    ElMessage.success('出库成本已回写')
    rewriteVisible.value = false
    await loadLogs()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '出库成本回写失败')
  } finally {
    saving.value = false
  }
}

async function reloadAll(): Promise<void> {
  await Promise.all([load(), loadPeriods()])
}

onMounted(reloadAll)
</script>
