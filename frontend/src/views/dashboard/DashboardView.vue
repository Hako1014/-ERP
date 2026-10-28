<template>
  <div class="settings-page dashboard-page">
    <SettingsPageHeader
      title="首页看板"
      :description="overview ? `经营概览 · 统计业务日 ${overview.as_of}（${overview.timezone}）` : '查看当前经营数据与销售走势'"
    >
      <template #actions>
        <el-button :loading="loading" @click="loadOverview">刷新数据</el-button>
      </template>
    </SettingsPageHeader>

    <el-alert
      v-if="loadError"
      type="error"
      :title="loadError"
      show-icon
      :closable="false"
      class="dashboard-error"
    >
      <template #default>
        <el-button link type="primary" @click="loadOverview">重试加载</el-button>
      </template>
    </el-alert>

    <template v-if="overview">
      <div class="dashboard-metrics">
        <MetricCard v-for="metric in overview.metrics" :key="metric.key" :metric="metric" />
      </div>

      <div class="dashboard-charts">
        <section v-loading="loading" class="settings-page__panel dashboard-charts__trend">
          <SalesTrendChart
            v-model:period="period"
            :items="overview.sales_trend.items"
            :start-date="overview.sales_trend.start_date"
            :end-date="overview.as_of"
            @period-change="loadOverview"
          />
        </section>
        <section class="settings-page__panel">
          <CategorySalesChart
            :items="overview.category_sales"
            :period-text="monthLabel"
          />
        </section>
        <section class="settings-page__panel">
          <BrandSalesPieChart
            :items="overview.brand_sales"
            :period-text="monthLabel"
          />
        </section>
      </div>

      <div class="dashboard-lists">
        <section class="settings-page__panel">
          <div class="dashboard-section-heading">
            <div>
              <h2>最近订单</h2>
              <p>按下单或制单时间倒序展示</p>
            </div>
          </div>
          <el-table :data="overview.recent_orders" row-key="id" stripe>
            <el-table-column prop="order_no" label="订单编号" min-width="175" />
            <el-table-column label="客户" min-width="115">
              <template #default="{ row }">{{ row.customer || '--' }}</template>
            </el-table-column>
            <el-table-column label="金额" width="130" align="right">
              <template #default="{ row }"><MoneyText :value="row.amount" /></template>
            </el-table-column>
            <el-table-column label="状态" width="105" align="center">
              <template #default="{ row }">
                <StatusTag :status="row.status" :label="row.status_label" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="76" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openOrder(row)">查看</el-button>
              </template>
            </el-table-column>
            <template #empty><EmptyState description="暂无最近订单" :show-action="false" /></template>
          </el-table>
        </section>

        <section class="settings-page__panel">
          <div class="dashboard-section-heading">
            <div>
              <h2>热门商品</h2>
              <p>近 30 日销量，环比前 30 日</p>
            </div>
          </div>
          <el-table :data="overview.hot_products" row-key="product_id" stripe>
            <el-table-column label="商品" min-width="180">
              <template #default="{ row }">
                <div class="dashboard-product-name">{{ row.product_name }}</div>
                <div class="dashboard-product-code">{{ row.product_code }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="sales_qty" label="销量" width="100" align="right" />
            <el-table-column label="价格" width="125" align="right">
              <template #default="{ row }"><MoneyText :value="row.price" /></template>
            </el-table-column>
            <el-table-column label="环比" width="120" align="right">
              <template #default="{ row }">
                <span :class="['dashboard-change', changeTone(row.change_rate)]">
                  {{ changeText(row.change_rate) }}
                </span>
              </template>
            </el-table-column>
            <template #empty><EmptyState description="暂无热门商品" :show-action="false" /></template>
          </el-table>
        </section>
      </div>
    </template>
    <el-skeleton v-else-if="loading" :rows="12" animated />

    <el-drawer v-model="orderVisible" title="订单详情" size="min(720px, 92vw)">
      <el-descriptions v-if="selectedOrder" :column="2" border>
        <el-descriptions-item label="订单编号">{{ selectedOrder.order_no }}</el-descriptions-item>
        <el-descriptions-item label="订单状态">
          <StatusTag :status="selectedOrder.status" :label="selectedOrder.status_label" />
        </el-descriptions-item>
        <el-descriptions-item label="客户">{{ selectedOrder.customer || '--' }}</el-descriptions-item>
        <el-descriptions-item label="店铺">{{ selectedOrder.shop_name || '--' }}</el-descriptions-item>
        <el-descriptions-item label="订单金额">
          <MoneyText :value="selectedOrder.amount" />
        </el-descriptions-item>
        <el-descriptions-item label="下单时间">
          {{ formatDateTime(selectedOrder.order_time || selectedOrder.created_at) }}
        </el-descriptions-item>
      </el-descriptions>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { dashboardApi, type DashboardOrder, type DashboardOverview } from '@/api/dashboard'
import EmptyState from '@/components/EmptyState.vue'
import MetricCard from '@/components/MetricCard.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import {
  BrandSalesPieChart,
  CategorySalesChart,
  SalesTrendChart,
  type TrendPeriod,
} from '@/components/charts'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'

const period = ref<TrendPeriod>('day')
const overview = ref<DashboardOverview | null>(null)
const loading = ref(false)
const loadError = ref('')
const orderVisible = ref(false)
const selectedOrder = ref<DashboardOrder | null>(null)
let requestSequence = 0

const monthLabel = computed(() =>
  overview.value ? `${overview.value.as_of.slice(0, 7)}（截至 ${overview.value.as_of}）` : '',
)

async function loadOverview(): Promise<void> {
  const sequence = ++requestSequence
  const selectedPeriod = period.value
  loading.value = true
  loadError.value = ''
  try {
    const data = await dashboardApi.overview(selectedPeriod)
    if (sequence !== requestSequence) return
    overview.value = data
  } catch (error) {
    if (sequence !== requestSequence) return
    loadError.value = error instanceof Error ? error.message : '看板数据加载失败'
    if (overview.value) period.value = overview.value.sales_trend.period
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

function changeTone(value: string | number | null): string {
  if (value === null || value === undefined) return 'is-empty'
  const amount = Number(value)
  if (!Number.isFinite(amount) || amount === 0) return 'is-empty'
  return amount < 0 ? 'is-down' : 'is-up'
}

function changeText(value: string | number | null): string {
  if (value === null || value === undefined) return '暂无环比'
  const text = String(value).trim()
  const percentage = text.endsWith('%') ? text : `${text}%`
  return Number(value) > 0 && !percentage.startsWith('+') ? `+${percentage}` : percentage
}

function openOrder(row: DashboardOrder): void {
  selectedOrder.value = row
  orderVisible.value = true
}

onMounted(() => { void loadOverview() })
</script>

<style scoped>
.dashboard-error { margin-bottom: 16px; }
.dashboard-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px; }
.dashboard-charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px; }
.dashboard-charts__trend { grid-column: 1 / -1; }
.dashboard-lists { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.dashboard-section-heading { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 14px; }
.dashboard-section-heading h2 { margin: 0; font-size: 16px; font-weight: 600; }
.dashboard-section-heading p { margin: 5px 0 0; color: var(--el-text-color-secondary); font-size: 12px; }
.dashboard-product-name { font-weight: 500; }
.dashboard-product-code { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.dashboard-change.is-up { color: var(--el-color-danger); }
.dashboard-change.is-down { color: var(--el-color-success); }
.dashboard-change.is-empty { color: var(--el-text-color-secondary); }
@media (max-width: 1100px) { .dashboard-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .dashboard-lists { grid-template-columns: 1fr; } }
@media (max-width: 720px) { .dashboard-metrics, .dashboard-charts { grid-template-columns: 1fr; } }
</style>
