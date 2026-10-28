<template>
  <div class="settings-page purchase-page">
    <SettingsPageHeader
      title="采购报表"
      description="按采购订单、采购入库、采购发票及库存与在途口径汇总采购经营数据。"
    />

    <section class="settings-page__panel">
      <div class="purchase-report-toolbar">
        <div class="purchase-report-toolbar__filters">
          <el-date-picker
            v-if="activeTab !== 'in_transit'"
            v-model="dateRange"
            class="purchase-filter--date"
            type="datetimerange"
            value-format="YYYY-MM-DDTHH:mm:ss"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            @change="loadReport"
          />
          <el-select
            v-model="supplierFilter"
            class="purchase-filter--wide"
            clearable
            filterable
            placeholder="全部供应商"
            @change="loadReport"
          >
            <el-option
              v-for="item in supplierOptions"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-if="activeTab === 'inbounds'"
            v-model="purchaserFilter"
            class="purchase-filter--wide"
            clearable
            filterable
            allow-create
            default-first-option
            placeholder="全部采购员"
            @change="loadReport"
          >
            <el-option
              v-for="item in purchaserOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-if="['inbounds', 'in_transit'].includes(activeTab)"
            v-model="productFilter"
            class="purchase-filter--wide"
            clearable
            filterable
            allow-create
            default-first-option
            placeholder="全部商品"
            @change="loadReport"
          >
            <el-option
              v-for="item in productOptions"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-if="activeTab === 'in_transit'"
            v-model="warehouseFilter"
            class="purchase-filter--wide"
            clearable
            filterable
            allow-create
            default-first-option
            placeholder="全部仓库"
            @change="loadReport"
          >
            <el-option
              v-for="item in warehouseOptions"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-if="activeTab === 'invoices'"
            v-model="writeOffFilter"
            class="purchase-filter"
            clearable
            placeholder="全部核销状态"
            @change="loadReport"
          >
            <el-option
              v-for="item in INVOICE_WRITE_OFF_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-input
            v-if="activeTab === 'in_transit'"
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="商品编码、名称或规格"
            @keyup.enter="loadReport"
            @clear="loadReport"
          />
          <el-button @click="loadReport">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </div>
      </div>

      <el-tabs v-model="activeTab" class="purchase-report-tabs" @tab-change="handleTabChange">
        <el-tab-pane label="采购订单" name="orders" />
        <el-tab-pane label="采购入库" name="inbounds" />
        <el-tab-pane label="采购发票" name="invoices" />
        <el-tab-pane label="库存 / 在途" name="in_transit" />
      </el-tabs>

      <div class="purchase-summary">
        <div
          v-for="[key, value] in summaryEntries"
          :key="key"
          class="purchase-summary__item"
        >
          <div class="purchase-summary__label">{{ reportSummaryLabel(key) }}</div>
          <div class="purchase-summary__value">
            <MoneyText v-if="isMoneySummary(key)" :value="value" />
            <span v-else>{{ value }}</span>
          </div>
        </div>
      </div>

      <el-table
        v-if="activeTab === 'orders'"
        v-loading="loading"
        :data="orderItems"
        row-key="supplier_id"
        border
      >
        <el-table-column prop="supplier_name" label="供应商" min-width="180" />
        <el-table-column prop="order_count" label="订单数" width="100" align="right" />
        <el-table-column prop="total_qty" label="采购数量" width="120" align="right" />
        <el-table-column label="采购金额" width="140" align="right">
          <template #default="{ row }"><MoneyText :value="row.total_amount" /></template>
        </el-table-column>
        <el-table-column prop="arrived_qty" label="到货数量" width="120" align="right" />
        <el-table-column prop="in_transit_qty" label="在途数量" width="120" align="right" />
      </el-table>

      <el-table
        v-else-if="activeTab === 'inbounds'"
        v-loading="loading"
        :data="inboundItems"
        border
      >
        <el-table-column prop="supplier_name" label="供应商" min-width="160" />
        <el-table-column prop="purchaser_name" label="采购员" min-width="110" />
        <el-table-column prop="product_code" label="商品编码" min-width="110" />
        <el-table-column prop="product_name" label="商品名称" min-width="170" />
        <el-table-column label="规格" min-width="120">
          <template #default="{ row }">
            {{ row.spec_code || row.spec_name || '商品级' }}
          </template>
        </el-table-column>
        <el-table-column prop="stock_in_count" label="入库单数" width="100" align="right" />
        <el-table-column prop="qty" label="入库数量" width="110" align="right" />
        <el-table-column label="入库金额" width="140" align="right">
          <template #default="{ row }"><MoneyText :value="row.amount" /></template>
        </el-table-column>
      </el-table>

      <el-table
        v-else-if="activeTab === 'invoices'"
        v-loading="loading"
        :data="invoiceItems"
        border
      >
        <el-table-column prop="supplier_name" label="供应商" min-width="180" />
        <el-table-column label="核销状态" width="110" align="center">
          <template #default="{ row }">
            <StatusTag
              :status="row.write_off_status"
              :label="labelOf(PURCHASE_STATUS_LABELS, row.write_off_status)"
              :tone="statusTone(row.write_off_status)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="invoice_count" label="发票数" width="100" align="right" />
        <el-table-column label="价税合计" width="140" align="right">
          <template #default="{ row }"><MoneyText :value="row.amount" /></template>
        </el-table-column>
        <el-table-column label="税额" width="130" align="right">
          <template #default="{ row }"><MoneyText :value="row.tax_amount" /></template>
        </el-table-column>
        <el-table-column label="不含税金额" width="140" align="right">
          <template #default="{ row }"><MoneyText :value="row.net_amount" /></template>
        </el-table-column>
        <el-table-column label="已核销金额" width="140" align="right">
          <template #default="{ row }"><MoneyText :value="row.write_off_amount" /></template>
        </el-table-column>
      </el-table>

      <el-table v-else v-loading="loading" :data="inTransitItems" border>
        <el-table-column prop="product_code" label="商品编码" min-width="120" />
        <el-table-column prop="product_name" label="商品名称" min-width="180" />
        <el-table-column label="规格" min-width="140">
          <template #default="{ row }">
            {{ row.spec_code || row.spec_name || '商品级' }}
          </template>
        </el-table-column>
        <el-table-column prop="current_qty" label="当前库存" width="120" align="right" />
        <el-table-column prop="in_transit_qty" label="在途数量" width="120" align="right" />
        <el-table-column prop="total_qty" label="合计" width="120" align="right" />
      </el-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { companyApi, type Staff } from '@/api/company'
import { partyApi, type PartyRecord } from '@/api/party'
import { productOptionsApi, type ProductOption } from '@/api/productOptions'
import {
  purchaseReferenceApi,
  purchaseReportApi,
  type InventoryInTransitRow,
  type PurchaseInboundReportRow,
  type PurchaseInvoiceReportRow,
  type PurchaseOrderReportRow,
  type PurchaseReportQuery,
  type WarehouseOption,
} from '@/api/purchase'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  INVOICE_WRITE_OFF_OPTIONS,
  PURCHASE_STATUS_LABELS,
  labelOf,
  reportSummaryLabel,
  statusTone,
} from '@/views/purchase/purchaseHelpers'
import '@/views/settings/settings.css'
import '@/views/purchase/purchase.css'

const authStore = useAuthStore()
const activeTab = ref('orders')
const dateRange = ref<string[]>([])
const supplierFilter = ref<number | string | null>(null)
const purchaserFilter = ref<number | string | null>(null)
const productFilter = ref<number | string | null>(null)
const warehouseFilter = ref<number | string | null>(null)
const writeOffFilter = ref('')
const keyword = ref('')
const loading = ref(false)
const summary = ref<Record<string, string | number>>({})
const orderItems = ref<PurchaseOrderReportRow[]>([])
const inboundItems = ref<PurchaseInboundReportRow[]>([])
const invoiceItems = ref<PurchaseInvoiceReportRow[]>([])
const inTransitItems = ref<InventoryInTransitRow[]>([])
const supplierOptions = ref<PartyRecord[]>([])
const purchaserOptions = ref<Staff[]>([])
const productOptions = ref<ProductOption[]>([])
const warehouseOptions = ref<WarehouseOption[]>([])
let requestSequence = 0

const summaryEntries = computed(() => Object.entries(summary.value))

function positiveId(value: number | string | null): number | undefined {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined
}

function buildQuery(tab: string): PurchaseReportQuery {
  const [startTime, endTime] = dateRange.value
  const query: PurchaseReportQuery = {
    supplier_id: positiveId(supplierFilter.value),
  }
  if (tab !== 'in_transit') {
    query.start_time = startTime || undefined
    query.end_time = endTime || undefined
  }
  if (tab === 'inbounds') {
    query.purchaser_id = positiveId(purchaserFilter.value)
    query.product_id = positiveId(productFilter.value)
  } else if (tab === 'invoices') {
    query.write_off_status = writeOffFilter.value || undefined
  } else if (tab === 'in_transit') {
    query.product_id = positiveId(productFilter.value)
    query.warehouse_id = positiveId(warehouseFilter.value)
    query.keyword = keyword.value.trim() || undefined
  }
  return query
}

async function loadReport(): Promise<void> {
  const tab = activeTab.value
  const sequence = ++requestSequence
  loading.value = true
  try {
    const query = buildQuery(tab)
    if (tab === 'inbounds') {
      const result = await purchaseReportApi.inbounds(query)
      if (sequence !== requestSequence) return
      inboundItems.value = result.items
      summary.value = result.summary
    } else if (tab === 'invoices') {
      const result = await purchaseReportApi.invoices(query)
      if (sequence !== requestSequence) return
      invoiceItems.value = result.items
      summary.value = result.summary
    } else if (tab === 'in_transit') {
      const result = await purchaseReportApi.inTransit(query)
      if (sequence !== requestSequence) return
      inTransitItems.value = result.items
      summary.value = result.summary
    } else {
      const result = await purchaseReportApi.orders(query)
      if (sequence !== requestSequence) return
      orderItems.value = result.items
      summary.value = result.summary
    }
  } catch (error) {
    if (sequence === requestSequence) {
      ElMessage.error(error instanceof Error ? error.message : '采购报表加载失败')
    }
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
    }
  }
}

function handleTabChange(): void {
  void loadReport()
}

function resetFilters(): void {
  dateRange.value = []
  supplierFilter.value = null
  purchaserFilter.value = null
  productFilter.value = null
  warehouseFilter.value = null
  writeOffFilter.value = ''
  keyword.value = ''
  void loadReport()
}

function isMoneySummary(key: string): boolean {
  return [
    'total_amount',
    'amount',
    'tax_amount',
    'net_amount',
    'write_off_amount',
  ].includes(key)
}

async function loadOptions(): Promise<void> {
  const errors: string[] = []
  try {
    const suppliers = await partyApi.listSuppliers({
      is_active: true,
      page_size: 200,
    })
    supplierOptions.value = suppliers.items
  } catch (error) {
    errors.push(error instanceof Error ? error.message : '供应商选项加载失败')
  }

  const optionalTasks: Promise<void>[] = []
  if (authStore.hasPermission('module:product')) {
    optionalTasks.push(
      productOptionsApi.listProducts(undefined, 1, 200).then((result) => {
        productOptions.value = result.items
      }),
    )
  }
  if (authStore.hasPermission('module:settings')) {
    optionalTasks.push(
      companyApi.listStaff({ is_active: true, page_size: 200 }).then((result) => {
        purchaserOptions.value = result.items
      }),
    )
  }
  if (authStore.hasPermission('module:stock')) {
    optionalTasks.push(
      purchaseReferenceApi.listWarehouses().then((result) => {
        warehouseOptions.value = result
      }),
    )
  }
  const results = await Promise.allSettled(optionalTasks)
  results.forEach((result) => {
    if (result.status === 'rejected') {
      errors.push(
        result.reason instanceof Error
          ? result.reason.message
          : '报表筛选选项加载失败',
      )
    }
  })
  if (errors.length) {
    ElMessage.error(errors[0])
  }
}

onMounted(() => {
  void loadOptions()
  void loadReport()
})
</script>
