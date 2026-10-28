<template>
  <div class="settings-page order-page">
    <SettingsPageHeader
      title="订单打印"
      description="获取运单号并生成面单、拣货单、送货单和发货单。"
    >
      <template #actions>
        <el-button
          :icon="RefreshLeft"
          :disabled="!selectedOrders.length || !canManage"
          :loading="actionLoading"
          @click="recycleWaybills"
        >
          回收运单号
        </el-button>
        <el-button
          :icon="Ticket"
          :disabled="!selectedOrders.length || !canManage"
          :loading="actionLoading"
          @click="acquireWaybills"
        >
          获取运单号
        </el-button>
        <el-button
          type="primary"
          :icon="Printer"
          :disabled="!selectedOrders.length || !canManage"
          :loading="printing"
          @click="createPrintBatch"
        >
          生成并打印
        </el-button>
      </template>
    </SettingsPageHeader>

    <section class="settings-page__panel">
      <div class="order-toolbar">
        <div class="order-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="订单号、买家、收货人或运单号"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="shopFilter"
            class="order-filter--wide"
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
            class="order-filter--wide"
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
          <el-button @click="search">查询</el-button>
        </div>
      </div>

      <div class="order-summary">
        <div class="order-summary__item">
          <div class="order-summary__label">待打印订单</div>
          <div class="order-summary__value">{{ total }}</div>
        </div>
        <div class="order-summary__item">
          <div class="order-summary__label">已选择</div>
          <div class="order-summary__value">{{ selectedOrders.length }}</div>
        </div>
        <div class="order-summary__item">
          <div class="order-summary__label">打印方式</div>
          <div class="order-summary__value order-print-type">
            {{ PRINT_TYPE_LABELS[printType] }}
          </div>
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
        empty-description="暂无待打印订单"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @selection-change="handleSelectionChange"
        @empty-action="resetAndLoad"
      >
        <template #receiver="{ row }">
          <div>{{ row.receiver_name || '--' }}</div>
          <div class="order-muted">{{ row.receiver_phone || '--' }}</div>
        </template>
        <template #tracking="{ row }">
          {{ row.tracking_no || '未获取' }}
        </template>
        <template #amount="{ row }">
          <MoneyText :value="row.paid_amount" />
        </template>
        <template #orderedAt="{ row }">
          {{ formatDateTime(row.order_time || row.created_at) }}
        </template>
      </DataTable>
    </section>

    <section class="settings-page__panel">
      <h2 class="order-section__title">打印设置</h2>
      <div class="order-scan-grid">
        <el-form-item label="面单类型">
          <el-select v-model="printType">
            <el-option
              v-for="(label, value) in PRINT_TYPE_LABELS"
              :key="value"
              :label="label"
              :value="value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="排序方式">
          <el-select v-model="sortType">
            <el-option
              v-for="(label, value) in WAVE_SORT_LABELS"
              :key="value"
              :label="label"
              :value="value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="打印机名称">
          <el-input v-model="printerName" placeholder="可留空" maxlength="128" />
        </el-form-item>
        <el-form-item label="随单打印">
          <el-checkbox v-model="includeShippingSlip">发货单</el-checkbox>
        </el-form-item>
        <el-form-item class="is-wide" label="手工运单号">
          <el-input
            v-model="manualWaybills"
            type="textarea"
            :rows="2"
            placeholder="每行一个：订单号=运单号；留空时由系统获取"
          />
        </el-form-item>
        <el-form-item label="下载单据">
          <el-select v-model="documentType">
            <el-option
              v-for="(label, value) in PRINT_DOCUMENT_LABELS"
              :key="value"
              :label="label"
              :value="value"
            />
          </el-select>
        </el-form-item>
      </div>
    </section>

    <section v-if="batch" class="settings-page__panel order-print-result">
      <div class="order-toolbar">
        <div>
          <h2 class="order-section__title">打印批次 {{ batch.batch_no }}</h2>
          <p class="order-muted">
            {{ batch.order_count }} 个订单，{{ batch.waybills.length }} 个运单号
          </p>
        </div>
        <div class="order-toolbar__actions">
          <el-button :icon="Download" :loading="downloading" @click="downloadPdf">
            下载 PDF
          </el-button>
          <el-button :icon="View" @click="openPdf">重新预览</el-button>
        </div>
      </div>
      <el-table :data="batch.orders" border max-height="360">
        <el-table-column prop="order_no" label="订单号" min-width="170" />
        <el-table-column prop="load_no" label="装载号" min-width="120" />
        <el-table-column prop="tracking_no" label="运单号" min-width="160" />
        <el-table-column prop="receiver_name" label="收货人" min-width="110" />
        <el-table-column prop="receiver_phone" label="电话" min-width="130" />
      </el-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Download,
  Printer,
  RefreshLeft,
  Ticket,
  View,
} from '@element-plus/icons-vue'

import {
  orderPrintApi,
  orderApi,
  type OrderRecord,
  type PrintBatch,
} from '@/api/order'
import { shopApi, type Shop } from '@/api/shop'
import { warehouseApi, type Warehouse } from '@/api/stock'
import { openBlobInNewTab, saveBlob } from '@/components/business/download'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  PRINT_DOCUMENT_LABELS,
  PRINT_TYPE_LABELS,
  WAVE_SORT_LABELS,
} from '@/views/order/orderHelpers'
import '@/views/settings/settings.css'
import '@/views/order/order.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('order:ship'))

const columns: DataTableColumn<OrderRecord>[] = [
  { type: 'selection', label: '选择', width: 52 },
  { prop: 'order_no', label: '订单号', minWidth: 170 },
  { prop: 'shop_name', label: '店铺', minWidth: 130 },
  { label: '收货人', slot: 'receiver', minWidth: 140 },
  { prop: 'warehouse_name', label: '发货仓库', minWidth: 120 },
  { prop: 'logistics_name', label: '物流公司', minWidth: 130 },
  { label: '运单号', slot: 'tracking', minWidth: 150 },
  { label: '实付金额', slot: 'amount', width: 120, align: 'right' },
  { label: '下单时间', slot: 'orderedAt', minWidth: 160 },
]

const rows = ref<OrderRecord[]>([])
const shops = ref<Shop[]>([])
const warehouses = ref<Warehouse[]>([])
const selectedOrders = ref<OrderRecord[]>([])
const batch = ref<PrintBatch | null>(null)
const loading = ref(false)
const actionLoading = ref(false)
const printing = ref(false)
const downloading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const shopFilter = ref<number | null>(null)
const warehouseFilter = ref<number | null>(null)
const printType = ref('THERMAL')
const sortType = ref('LOCATION')
const printerName = ref('')
const includeShippingSlip = ref(false)
const documentType = ref('ALL')
const manualWaybills = ref('')

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    shopFilter.value !== null ||
    warehouseFilter.value !== null,
)

function manualWaybillMap(): Record<string, string> | undefined {
  if (!manualWaybills.value.trim()) {
    return undefined
  }
  const values = manualWaybills.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const mapping: Record<string, string> = {}
  values.forEach((line) => {
    const separator = line.includes('=') ? '=' : ':'
    const [orderNo, waybillNo] = line.split(separator)
    if (orderNo?.trim() && waybillNo?.trim()) {
      mapping[orderNo.trim()] = waybillNo.trim()
    }
  })
  return Object.keys(mapping).length ? mapping : undefined
}

async function loadOptions(): Promise<void> {
  const results = await Promise.allSettled([
    shopApi.listShops({ page: 1, page_size: 200 }).then((result) => {
      shops.value = result.items
    }),
    warehouseApi.list().then((result) => {
      warehouses.value = result
    }),
  ])
  if (results.every((result) => result.status === 'rejected')) {
    ElMessage.error('订单打印基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await orderApi.list({
      shop_id: shopFilter.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      status: 'PENDING',
      audit_status: 'APPROVED',
      keyword: keyword.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '待打印订单加载失败')
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
  search()
}

function handleSelectionChange(items: OrderRecord[]): void {
  selectedOrders.value = items
}

async function acquireWaybills(): Promise<void> {
  actionLoading.value = true
  try {
    const result = await orderPrintApi.acquireWaybills(
      selectedOrders.value.map((item) => item.id),
      printType.value,
      manualWaybillMap(),
    )
    ElMessage.success(`已获取 ${result.count} 个运单号`)
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '运单号获取失败')
  } finally {
    actionLoading.value = false
  }
}

async function recycleWaybills(): Promise<void> {
  actionLoading.value = true
  try {
    const result = await orderPrintApi.recycleWaybills(
      selectedOrders.value.map((item) => item.id),
    )
    ElMessage.success(`已回收 ${result.count} 个运单号`)
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '运单号回收失败')
  } finally {
    actionLoading.value = false
  }
}

async function createPrintBatch(): Promise<void> {
  if (!selectedOrders.value.length) {
    ElMessage.warning('请先选择订单')
    return
  }
  printing.value = true
  try {
    batch.value = await orderPrintApi.createBatch({
      order_ids: selectedOrders.value.map((item) => item.id),
      print_type: printType.value,
      printer_name: printerName.value.trim() || null,
      include_shipping_slip: includeShippingSlip.value,
      sort_type: sortType.value,
      waybill_nos: manualWaybillMap(),
    })
    ElMessage.success('打印批次已生成')
    await openPdf()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '打印批次生成失败')
  } finally {
    printing.value = false
  }
}

async function batchBlob(): Promise<Blob | null> {
  if (!batch.value) {
    return null
  }
  return orderPrintApi.downloadPdf(batch.value.batch_no, documentType.value)
}

async function openPdf(): Promise<void> {
  try {
    const blob = await batchBlob()
    if (blob) {
      openBlobInNewTab(blob)
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '打印文件生成失败')
  }
}

async function downloadPdf(): Promise<void> {
  if (!batch.value) {
    return
  }
  downloading.value = true
  try {
    const blob = await batchBlob()
    if (blob) {
      saveBlob(blob, `打印批次-${batch.value.batch_no}.pdf`)
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '打印文件下载失败')
  } finally {
    downloading.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})
</script>

<style scoped>
.order-print-type {
  font-size: 16px;
}
</style>
