<template>
  <div class="settings-page order-page">
    <SettingsPageHeader
      title="扫描发货"
      description="扫描订单、运单或商品条码，按仓库库存逐项扣减并生成包裹。"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="shipmentLoading" @click="loadShipments">
          刷新发货记录
        </el-button>
      </template>
    </SettingsPageHeader>

    <section class="order-scan-panel">
      <div class="order-scan-grid">
        <el-form-item class="is-wide" label="订单号 / 运单号" required>
          <el-input
            ref="scanInputRef"
            v-model="form.scan_code"
            size="large"
            clearable
            autofocus
            placeholder="扫描后按回车连续操作"
            @keyup.enter="submitScan"
          />
        </el-form-item>
        <el-form-item label="商品条码">
          <el-input
            v-model="form.barcode"
            size="large"
            clearable
            placeholder="商品级可不填"
            @keyup.enter="submitScan"
          />
        </el-form-item>
        <el-form-item label="扫描数量">
          <el-input v-model="form.qty" inputmode="decimal" placeholder="1.000" />
        </el-form-item>
        <el-form-item label="包裹序号">
          <el-input-number
            v-model="form.package_no"
            :min="1"
            :max="999"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="运单号">
          <el-input v-model="form.tracking_no" maxlength="64" />
        </el-form-item>
        <el-form-item label="实际重量(g)">
          <el-input v-model="form.actual_weight_g" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="误差区间(g)">
          <el-input v-model="form.weight_tolerance_g" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="重量超差">
          <el-checkbox v-model="form.allow_weight_override">允许超差发货</el-checkbox>
        </el-form-item>
        <el-form-item class="is-wide" label="发货备注">
          <el-input v-model="form.remark" maxlength="255" />
        </el-form-item>
      </div>

      <div class="order-actions">
        <el-button
          type="primary"
          size="large"
          :icon="Van"
          :loading="scanning"
          :disabled="!form.scan_code.trim()"
          @click="submitScan"
        >
          扫描发货
        </el-button>
        <el-button size="large" @click="resetScanFields">清空扫描值</el-button>
      </div>
    </section>

    <section v-if="lastResult" class="settings-page__panel order-scan-result">
      <div class="order-toolbar">
        <div>
          <h2 class="order-section__title">
            {{ lastResult.shipment.order_no || lastResult.scan_code }}
          </h2>
          <p class="order-muted">
            扫描来源：{{ lastResult.scan_source }}，包裹序号：
            {{ lastResult.shipment.package_no || '--' }}
          </p>
        </div>
        <StatusTag
          :status="lastResult.completed ? 'SHIPPED' : 'SCANNING'"
          :label="lastResult.completed ? '订单已发完' : '继续扫描'"
          :tone="lastResult.completed ? 'success' : 'warning'"
        />
      </div>

      <el-alert
        v-if="lastResult.shipment.weight_out_of_range"
        class="order-scan-result"
        type="warning"
        :closable="false"
        show-icon
        :title="
          lastResult.shipment.weight_overridden
            ? '实际重量超出误差区间，已按授权强制发货'
            : '实际重量超出误差区间'
        "
      />

      <el-descriptions :column="3" border>
        <el-descriptions-item label="包裹号">
          {{ lastResult.shipment.shipment_no }}
        </el-descriptions-item>
        <el-descriptions-item label="运单号">
          {{ lastResult.shipment.tracking_no || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="平台回传">
          {{ labelOf(PLATFORM_SYNC_LABELS, lastResult.shipment.platform_sync_status) }}
        </el-descriptions-item>
        <el-descriptions-item label="预计重量">
          {{ lastResult.shipment.expected_weight_g || '--' }} g
        </el-descriptions-item>
        <el-descriptions-item label="实际重量">
          {{ lastResult.shipment.actual_weight_g || '--' }} g
        </el-descriptions-item>
        <el-descriptions-item label="发货人">
          {{ lastResult.shipment.shipped_by_name || '--' }}
        </el-descriptions-item>
      </el-descriptions>

      <section class="order-section">
        <h3 class="order-section__title">本次包裹明细</h3>
        <el-table :data="lastResult.shipment.items || []" border>
          <el-table-column prop="product_code" label="商品编码" min-width="120" />
          <el-table-column prop="product_name" label="商品名称" min-width="180" />
          <el-table-column label="规格" min-width="130">
            <template #default="{ row }">
              {{ row.spec_code || row.spec_name || '商品级' }}
            </template>
          </el-table-column>
          <el-table-column prop="qty" label="数量" width="100" align="right" />
          <el-table-column prop="barcode" label="条码" min-width="150" />
          <el-table-column label="成本" width="110" align="right">
            <template #default="{ row }"><MoneyText :value="row.cost_price" /></template>
          </el-table-column>
        </el-table>
      </section>

      <section class="order-section">
        <h3 class="order-section__title">订单剩余待发数量</h3>
        <el-table :data="remainingRows" border>
          <el-table-column prop="order_item_id" label="订单明细 ID" min-width="130" />
          <el-table-column prop="qty" label="剩余数量" width="120" align="right" />
        </el-table>
      </section>
    </section>

    <section class="settings-page__panel">
      <div class="order-toolbar">
        <div class="order-toolbar__filters">
          <el-input
            v-model="shipmentKeyword"
            class="settings-page__search"
            clearable
            placeholder="运单号"
            @keyup.enter="searchShipments"
            @clear="searchShipments"
          />
          <el-select
            v-model="statusFilter"
            class="order-filter"
            placeholder="全部状态"
            @change="searchShipments"
          >
            <el-option label="全部状态" value="" />
            <el-option label="扫描中" value="SCANNING" />
            <el-option label="已发货" value="SHIPPED" />
            <el-option label="已取消" value="CANCELLED" />
          </el-select>
          <el-select
            v-model="syncFilter"
            class="order-filter"
            placeholder="全部回传状态"
            @change="searchShipments"
          >
            <el-option label="全部回传状态" value="" />
            <el-option label="待回传" value="PENDING" />
            <el-option label="已回传" value="SYNCED" />
            <el-option label="回传失败" value="FAILED" />
            <el-option label="无需回传" value="SKIPPED" />
          </el-select>
          <el-button @click="searchShipments">查询</el-button>
        </div>
      </div>

      <DataTable
        v-model:page="shipmentPage"
        v-model:page-size="shipmentPageSize"
        :rows="shipments"
        :columns="shipmentColumns"
        :loading="shipmentLoading"
        :total="shipmentTotal"
        :sync-query="false"
        empty-description="暂无发货记录"
        @query-change="loadShipments"
      >
        <template #status="{ row }">
          <StatusTag
            :status="row.status"
            :label="labelOf(SHIPMENT_STATUS_LABELS, row.status)"
            :tone="statusTone(row.status)"
          />
        </template>
        <template #sync="{ row }">
          <StatusTag
            :status="row.platform_sync_status"
            :label="labelOf(PLATFORM_SYNC_LABELS, row.platform_sync_status)"
            :tone="statusTone(row.platform_sync_status)"
          />
        </template>
        <template #weight="{ row }">
          {{ row.actual_weight_g || row.expected_weight_g || '--' }} g
        </template>
        <template #shippedAt="{ row }">
          {{ formatDateTime(row.shipped_at || row.created_at) }}
        </template>
        <template #actions="{ row }">
          <el-button
            v-if="canManage && row.platform_sync_status === 'PENDING'"
            link
            type="primary"
            @click="markSynced(row)"
          >
            标记已回传
          </el-button>
        </template>
      </DataTable>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, type InputInstance } from 'element-plus'
import { Refresh, Van } from '@element-plus/icons-vue'

import {
  shipmentApi,
  type ScanShipmentResult,
  type Shipment,
} from '@/api/order'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  PLATFORM_SYNC_LABELS,
  SHIPMENT_STATUS_LABELS,
  labelOf,
  statusTone,
} from '@/views/order/orderHelpers'
import '@/views/settings/settings.css'
import '@/views/order/order.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('order:ship'))

const shipmentColumns: DataTableColumn<Shipment>[] = [
  { prop: 'shipment_no', label: '包裹号', minWidth: 180 },
  { prop: 'order_no', label: '订单号', minWidth: 170 },
  { prop: 'package_no', label: '包裹序号', width: 100, align: 'right' },
  { prop: 'tracking_no', label: '运单号', minWidth: 160 },
  { label: '状态', slot: 'status', width: 100, align: 'center' },
  { label: '重量', slot: 'weight', width: 110, align: 'right' },
  { label: '平台回传', slot: 'sync', width: 110, align: 'center' },
  { label: '发货时间', slot: 'shippedAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 110, fixed: 'right' },
]

const scanInputRef = ref<InputInstance>()
const scanning = ref(false)
const shipmentLoading = ref(false)
const shipments = ref<Shipment[]>([])
const lastResult = ref<ScanShipmentResult | null>(null)
const shipmentPage = ref(1)
const shipmentPageSize = ref(20)
const shipmentTotal = ref(0)
const shipmentKeyword = ref('')
const statusFilter = ref('')
const syncFilter = ref('')

const form = reactive({
  scan_code: '',
  barcode: '',
  qty: '1.000',
  tracking_no: '',
  package_no: 1,
  actual_weight_g: '',
  weight_tolerance_g: '',
  allow_weight_override: false,
  remark: '',
})

const remainingRows = computed(() =>
  Object.entries(lastResult.value?.remaining || {}).map(([orderItemId, qty]) => ({
    order_item_id: orderItemId,
    qty,
  })),
)

async function focusScanInput(): Promise<void> {
  await nextTick()
  scanInputRef.value?.focus()
}

function resetScanFields(): void {
  form.scan_code = ''
  form.barcode = ''
  form.qty = '1.000'
  void focusScanInput()
}

async function submitScan(): Promise<void> {
  if (!form.scan_code.trim()) {
    ElMessage.warning('请扫描订单号或运单号')
    return
  }
  if (Number(form.qty) <= 0) {
    ElMessage.warning('扫描数量必须大于 0')
    return
  }
  scanning.value = true
  try {
    lastResult.value = await shipmentApi.scan({
      scan_code: form.scan_code.trim(),
      barcode: form.barcode.trim() || null,
      qty: form.qty.trim(),
      tracking_no: form.tracking_no.trim() || null,
      package_no: form.package_no || null,
      actual_weight_g: form.actual_weight_g.trim() || null,
      weight_tolerance_g: form.weight_tolerance_g.trim() || null,
      allow_weight_override: form.allow_weight_override,
      remark: form.remark.trim() || null,
    })
    if (lastResult.value.completed) {
      ElMessage.success('订单已完成发货')
    } else {
      ElMessage.success('扫描明细已记录，请继续扫描')
    }
    resetScanFields()
    await loadShipments()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '扫描发货失败')
    await focusScanInput()
  } finally {
    scanning.value = false
  }
}

function searchShipments(): void {
  shipmentPage.value = 1
  void loadShipments()
}

async function loadShipments(): Promise<void> {
  shipmentLoading.value = true
  try {
    const result = await shipmentApi.list({
      tracking_no: shipmentKeyword.value || undefined,
      status: statusFilter.value || undefined,
      platform_sync_status: syncFilter.value || undefined,
      page: shipmentPage.value,
      page_size: shipmentPageSize.value,
    })
    shipments.value = result.items
    shipmentTotal.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '发货记录加载失败')
  } finally {
    shipmentLoading.value = false
  }
}

async function markSynced(row: Shipment): Promise<void> {
  try {
    await shipmentApi.syncPlatform(row.id, 'SYNCED', '人工标记已回传')
    ElMessage.success('平台回传状态已更新')
    await loadShipments()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '平台回传状态更新失败')
  }
}

onMounted(async () => {
  await Promise.all([loadShipments(), focusScanInput()])
})
</script>
