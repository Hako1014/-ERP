<template>
  <div class="settings-page order-page">
    <SettingsPageHeader
      title="售后管理"
      description="处理退货、换货、退货登记、物流和退货入库确认。"
    >
      <template #actions>
        <el-button
          v-if="activeTab === 'returns'"
          :icon="Download"
          @click="exportReturns"
        >
          导出退货单
        </el-button>
        <el-button
          v-if="activeTab === 'returns' && canManage"
          type="primary"
          :icon="Plus"
          @click="openCreateReturn"
        >
          新建退货单
        </el-button>
        <el-button
          v-if="activeTab === 'exchanges' && canManage"
          type="primary"
          :icon="Plus"
          @click="openCreateExchange"
        >
          新建换货单
        </el-button>
        <el-button
          v-if="activeTab === 'registers' && canManage"
          type="primary"
          :icon="Plus"
          @click="openCreateRegister"
        >
          新建退货登记
        </el-button>
      </template>
    </SettingsPageHeader>

    <el-tabs v-model="activeTab" class="order-tabs" @tab-change="handleTabChange">
      <el-tab-pane label="退货管理" name="returns">
        <section class="settings-page__panel">
          <div class="order-toolbar">
            <div class="order-toolbar__filters">
              <el-input
                v-model="keyword"
                class="settings-page__search"
                clearable
                placeholder="售后单号或订单号"
                @keyup.enter="search"
                @clear="search"
              />
              <el-select
                v-model="statusFilter"
                class="order-filter"
                placeholder="全部状态"
                @change="search"
              >
                <el-option label="全部状态" value="" />
                <el-option
                  v-for="(label, value) in AFTER_SALE_STATUS_LABELS"
                  :key="value"
                  :label="label"
                  :value="value"
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

          <DataTable
            v-model:page="page"
            v-model:page-size="pageSize"
            :rows="returnRows"
            :columns="returnColumns"
            :loading="loading"
            :total="total"
            :sync-query="false"
            empty-description="暂无退货单"
            @query-change="loadReturns"
          >
            <template #status="{ row }">
              <StatusTag
                :status="row.status"
                :label="labelOf(AFTER_SALE_STATUS_LABELS, row.status)"
                :tone="statusTone(row.status)"
              />
            </template>
            <template #refund="{ row }">
              <MoneyText :value="row.refund_amount" />
            </template>
            <template #arrived="{ row }">
              {{ row.is_arrived ? '已到货' : '未到货' }}
            </template>
            <template #stockIn="{ row }">
              {{ row.stock_in_no || row.stock_in_status || '--' }}
            </template>
            <template #createdAt="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
            <template #actions="{ row }">
              <el-button link type="primary" @click="openReturnDetail(row)">
                详情
              </el-button>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>

      <el-tab-pane label="换货管理" name="exchanges">
        <section class="settings-page__panel">
          <div class="order-toolbar">
            <div class="order-toolbar__filters">
              <el-input
                v-model="keyword"
                class="settings-page__search"
                clearable
                placeholder="换货单号或订单号"
                @keyup.enter="search"
                @clear="search"
              />
              <el-select
                v-model="statusFilter"
                class="order-filter"
                placeholder="全部状态"
                @change="search"
              >
                <el-option label="全部状态" value="" />
                <el-option
                  v-for="(label, value) in AFTER_SALE_STATUS_LABELS"
                  :key="value"
                  :label="label"
                  :value="value"
                />
              </el-select>
              <el-button @click="search">查询</el-button>
            </div>
          </div>

          <DataTable
            v-model:page="page"
            v-model:page-size="pageSize"
            :rows="exchangeRows"
            :columns="exchangeColumns"
            :loading="loading"
            :total="total"
            :sync-query="false"
            empty-description="暂无换货单"
            @query-change="loadExchanges"
          >
            <template #status="{ row }">
              <StatusTag
                :status="row.status"
                :label="labelOf(AFTER_SALE_STATUS_LABELS, row.status)"
                :tone="statusTone(row.status)"
              />
            </template>
            <template #compensate="{ row }">
              <MoneyText :value="row.compensate_amount" />
            </template>
            <template #createdAt="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
            <template #actions="{ row }">
              <el-button link type="primary" @click="openExchangeDetail(row)">
                详情
              </el-button>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>

      <el-tab-pane label="退货登记" name="registers">
        <section class="settings-page__panel">
          <div class="order-toolbar">
            <div class="order-toolbar__filters">
              <el-input
                v-model="registerKeyword"
                class="settings-page__search"
                clearable
                placeholder="物流单号、寄件人或商品信息"
                @keyup.enter="loadRegisters"
                @clear="loadRegisters"
              />
              <el-button @click="loadRegisters">查询</el-button>
            </div>
          </div>
          <el-table v-loading="registerLoading" :data="filteredRegisters" border>
            <el-table-column prop="target_no" label="关联售后单" min-width="170" />
            <el-table-column prop="logistics_company" label="物流公司" min-width="130" />
            <el-table-column prop="tracking_no" label="物流单号" min-width="160" />
            <el-table-column prop="sender" label="寄件人" min-width="110" />
            <el-table-column prop="sender_phone" label="寄件电话" min-width="130" />
            <el-table-column
              prop="product_info"
              label="商品信息"
              min-width="220"
              show-overflow-tooltip
            />
            <el-table-column label="创建时间" min-width="160">
              <template #default="{ row }">
                {{ formatDateTime(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <div class="settings-page__actions">
                  <el-button
                    v-if="canManage"
                    link
                    type="primary"
                    @click="openEditRegister(row)"
                  >
                    编辑
                  </el-button>
                  <el-button
                    v-if="canManage"
                    link
                    type="danger"
                    @click="removeRegister(row)"
                  >
                    删除
                  </el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </el-tab-pane>
    </el-tabs>

    <FormDialog
      v-model:visible="returnFormVisible"
      title="新建退货单"
      :loading="saving"
      width="980px"
      @submit="submitReturn"
    >
      <div class="order-form-grid">
        <el-form-item label="原订单" required>
          <el-select
            v-model="returnForm.order_id"
            filterable
            placeholder="选择订单"
            @change="loadReturnOrderItems"
          >
            <el-option
              v-for="item in orders"
              :key="item.id"
              :label="`${item.order_no} · ${item.shop_name || ''}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="退货仓库" required>
          <el-select v-model="returnForm.warehouse_id" filterable>
            <el-option
              v-for="item in selectableWarehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="退货原因">
          <el-input v-model="returnForm.reason" maxlength="128" />
        </el-form-item>
        <el-form-item label="买家电话">
          <el-input v-model="returnForm.buyer_phone" maxlength="32" />
        </el-form-item>
        <el-form-item label="物流公司">
          <el-input v-model="returnForm.logistics_company" maxlength="64" />
        </el-form-item>
        <el-form-item label="物流单号">
          <el-input v-model="returnForm.tracking_no" maxlength="64" />
        </el-form-item>
        <el-form-item label="退货运费">
          <el-input v-model="returnForm.freight" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="是否到货">
          <el-switch v-model="returnForm.is_arrived" />
        </el-form-item>
        <el-form-item label="是否拒收">
          <el-switch v-model="returnForm.is_rejected_sign" />
        </el-form-item>
        <el-form-item label="需退回商品">
          <el-switch v-model="returnForm.need_return_goods" />
        </el-form-item>
        <el-form-item label="退回人">
          <el-input v-model="returnForm.return_person" maxlength="64" />
        </el-form-item>
        <el-form-item label="商品状态">
          <el-input v-model="returnForm.goods_status" maxlength="32" />
        </el-form-item>
      </div>

      <section class="order-section">
        <h3 class="order-section__title">退货商品</h3>
        <el-table :data="returnForm.items" border>
          <el-table-column prop="product_code" label="商品编码" min-width="120" />
          <el-table-column prop="product_name" label="商品名称" min-width="180" />
          <el-table-column label="规格" min-width="130">
            <template #default="{ row }">
              {{ row.spec_code || row.spec_name || '商品级' }}
            </template>
          </el-table-column>
          <el-table-column label="退货数量" width="140">
            <template #default="{ row }">
              <el-input v-model="row.qty" inputmode="decimal" />
            </template>
          </el-table-column>
          <el-table-column label="退款金额" width="150">
            <template #default="{ row }">
              <el-input v-model="row.refund_amount" inputmode="decimal" />
            </template>
          </el-table-column>
        </el-table>
      </section>
    </FormDialog>

    <FormDialog
      v-model:visible="exchangeFormVisible"
      title="新建换货单"
      :loading="saving"
      width="940px"
      @submit="submitExchange"
    >
      <div class="order-form-grid">
        <el-form-item label="原订单" required>
          <el-select
            v-model="exchangeForm.order_id"
            filterable
            placeholder="选择订单"
            @change="loadExchangeOrderItems"
          >
            <el-option
              v-for="item in orders"
              :key="item.id"
              :label="`${item.order_no} · ${item.shop_name || ''}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="换货原因">
          <el-input v-model="exchangeForm.reason" maxlength="128" />
        </el-form-item>
        <el-form-item label="货到付款">
          <el-switch v-model="exchangeForm.is_cod" />
        </el-form-item>
        <el-form-item label="需要退回">
          <el-switch v-model="exchangeForm.is_return" />
        </el-form-item>
        <el-form-item label="补偿订单号">
          <el-input v-model="exchangeForm.compensate_order_no" maxlength="64" />
        </el-form-item>
        <el-form-item label="补偿金额">
          <el-input
            v-model="exchangeForm.compensate_amount"
            inputmode="decimal"
          />
        </el-form-item>
        <el-form-item label="补偿方向">
          <el-select v-model="exchangeForm.compensate_direction" clearable>
            <el-option label="我方支付" value="PAY" />
            <el-option label="客户支付" value="RECEIVE" />
          </el-select>
        </el-form-item>
      </div>

      <section class="order-section">
        <h3 class="order-section__title">换货商品</h3>
        <el-table :data="exchangeForm.items" border>
          <el-table-column prop="product_code" label="商品编码" min-width="120" />
          <el-table-column prop="product_name" label="商品名称" min-width="180" />
          <el-table-column label="规格" min-width="130">
            <template #default="{ row }">
              {{ row.spec_code || row.spec_name || '商品级' }}
            </template>
          </el-table-column>
          <el-table-column label="原数量" width="110">
            <template #default="{ row }">{{ row.qty }}</template>
          </el-table-column>
          <el-table-column label="换货数量" width="150">
            <template #default="{ row }">
              <el-input v-model="row.exchange_qty" inputmode="decimal" />
            </template>
          </el-table-column>
        </el-table>
      </section>
    </FormDialog>

    <FormDialog
      v-model:visible="registerFormVisible"
      :title="registerEditId ? '编辑退货登记' : '新建退货登记'"
      :loading="saving"
      width="720px"
      @submit="submitRegister"
    >
      <div class="order-form-grid">
        <el-form-item label="物流公司">
          <el-input v-model="registerForm.logistics_company" maxlength="64" />
        </el-form-item>
        <el-form-item label="物流单号">
          <el-input v-model="registerForm.tracking_no" maxlength="64" />
        </el-form-item>
        <el-form-item label="寄件人">
          <el-input v-model="registerForm.sender" maxlength="64" />
        </el-form-item>
        <el-form-item label="寄件电话">
          <el-input v-model="registerForm.sender_phone" maxlength="32" />
        </el-form-item>
        <el-form-item class="is-full-width" label="商品信息">
          <el-input
            v-model="registerForm.product_info"
            type="textarea"
            :rows="3"
            maxlength="2000"
          />
        </el-form-item>
        <el-form-item class="is-full-width" label="图片地址">
          <el-input v-model="registerForm.image" maxlength="255" />
        </el-form-item>
      </div>
    </FormDialog>

    <el-drawer
      v-model="detailVisible"
      :title="detailTitle"
      size="min(900px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="关联订单">
              {{ detail.order_no || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              {{ labelOf(AFTER_SALE_STATUS_LABELS, detail.status) }}
            </el-descriptions-item>
            <el-descriptions-item label="店铺">
              {{ detail.shop_name || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="仓库">
              {{ detail.warehouse_name || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="退款金额">
              <MoneyText :value="detail.refund_amount" />
            </el-descriptions-item>
            <el-descriptions-item label="退换数量">
              {{ detail.return_qty || detail.exchange_qty || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="物流信息" :span="2">
              {{ detail.logistics_company || '--' }} {{ detail.tracking_no || '' }}
            </el-descriptions-item>
            <el-descriptions-item label="原因" :span="2">
              {{ detail.reason || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="入库单">
              {{ detail.stock_in_no || detail.stock_in_status || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="退货登记">
              {{ detail.register?.tracking_no || '--' }}
            </el-descriptions-item>
          </el-descriptions>

          <section class="order-section">
            <h3 class="order-section__title">商品明细</h3>
            <el-table :data="detail.items || []" border>
              <el-table-column prop="product_code" label="商品编码" min-width="120" />
              <el-table-column prop="product_name" label="商品名称" min-width="180" />
              <el-table-column label="规格" min-width="130">
                <template #default="{ row }">
                  {{ row.spec_code || row.spec_name || '商品级' }}
                </template>
              </el-table-column>
              <el-table-column prop="qty" label="退货数量" width="100" />
              <el-table-column prop="exchange_qty" label="换货数量" width="100" />
              <el-table-column label="退款金额" width="120" align="right">
                <template #default="{ row }">
                  <MoneyText :value="row.refund_amount" />
                </template>
              </el-table-column>
            </el-table>
          </section>

          <div class="order-actions">
            <el-button
              v-if="canManage && detail.status === 'PENDING'"
              type="primary"
              :loading="actionLoading"
              @click="submitAfterSale"
            >
              提交售后
            </el-button>
            <el-button
              v-if="
                activeTab === 'returns' &&
                canConfirm &&
                detail.status === 'SUBMITTED'
              "
              type="success"
              :loading="actionLoading"
              @click="confirmAfterSale"
            >
              确认退货入库
            </el-button>
            <el-button
              v-if="canManage && detail.status === 'SUBMITTED'"
              :loading="actionLoading"
              @click="openLogistics"
            >
              更新物流
            </el-button>
            <el-button
              v-if="
                canManage &&
                ['PENDING', 'SUBMITTED'].includes(detail.status)
              "
              type="danger"
              plain
              :loading="actionLoading"
              @click="cancelAfterSale"
            >
              取消售后
            </el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <FormDialog
      v-model:visible="logisticsVisible"
      title="更新退货物流"
      :loading="actionLoading"
      width="560px"
      @submit="submitLogistics"
    >
      <el-form-item label="物流公司">
        <el-input v-model="logisticsForm.logistics_company" maxlength="64" />
      </el-form-item>
      <el-form-item label="物流单号">
        <el-input v-model="logisticsForm.tracking_no" maxlength="64" />
      </el-form-item>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Plus } from '@element-plus/icons-vue'

import {
  afterSaleApi,
  orderApi,
  type AfterSaleRecord,
  type ExchangeCreatePayload,
  type OrderRecord,
  type ReturnCreatePayload,
  type ReturnRegister,
  type ReturnRegisterPayload,
} from '@/api/order'
import { warehouseApi, type Warehouse } from '@/api/stock'
import { saveBlob } from '@/components/business/download'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  AFTER_SALE_STATUS_LABELS,
  labelOf,
  statusTone,
} from '@/views/order/orderHelpers'
import '@/views/settings/settings.css'
import '@/views/order/order.css'

interface AfterSaleItemRow {
  product_id: number
  sku_id: number | null
  product_code?: string | null
  product_name?: string | null
  spec_code?: string | null
  spec_name?: string | null
  qty: string
  exchange_qty: string
  refund_amount: string
}

interface ReturnFormState extends Omit<ReturnCreatePayload, 'items'> {
  items: AfterSaleItemRow[]
}

interface ExchangeFormState extends Omit<ExchangeCreatePayload, 'items'> {
  items: AfterSaleItemRow[]
}

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('after_sale:manage'))
const canConfirm = computed(
  () =>
    authStore.hasPermission('after_sale:manage') ||
    authStore.hasPermission('finance:manage'),
)

const returnColumns: DataTableColumn<AfterSaleRecord>[] = [
  { prop: 'return_no', label: '退货单号', minWidth: 180 },
  { prop: 'order_no', label: '订单号', minWidth: 170 },
  { prop: 'shop_name', label: '店铺', minWidth: 130 },
  { label: '状态', slot: 'status', width: 100, align: 'center' },
  { label: '退款金额', slot: 'refund', width: 120, align: 'right' },
  { prop: 'return_qty', label: '退货数量', width: 100, align: 'right' },
  { prop: 'logistics_company', label: '物流公司', minWidth: 120 },
  { prop: 'tracking_no', label: '物流单号', minWidth: 150 },
  { label: '到货', slot: 'arrived', width: 90, align: 'center' },
  { label: '入库单', slot: 'stockIn', minWidth: 140 },
  { label: '创建时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const exchangeColumns: DataTableColumn<AfterSaleRecord>[] = [
  { prop: 'exchange_no', label: '换货单号', minWidth: 180 },
  { prop: 'order_no', label: '订单号', minWidth: 170 },
  { prop: 'shop_name', label: '店铺', minWidth: 130 },
  { label: '状态', slot: 'status', width: 100, align: 'center' },
  { prop: 'exchange_qty', label: '换货数量', width: 100, align: 'right' },
  { prop: 'reason', label: '换货原因', minWidth: 160 },
  { label: '补偿金额', slot: 'compensate', width: 120, align: 'right' },
  { prop: 'compensate_order_no', label: '补偿订单', minWidth: 150 },
  { label: '创建时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const activeTab = ref('returns')
const returnRows = ref<AfterSaleRecord[]>([])
const exchangeRows = ref<AfterSaleRecord[]>([])
const registers = ref<ReturnRegister[]>([])
const orders = ref<OrderRecord[]>([])
const warehouses = ref<Warehouse[]>([])
const loading = ref(false)
const registerLoading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const registerKeyword = ref('')
const statusFilter = ref('')
const warehouseFilter = ref<number | null>(null)
const returnFormVisible = ref(false)
const exchangeFormVisible = ref(false)
const registerFormVisible = ref(false)
const detailVisible = ref(false)
const logisticsVisible = ref(false)
const registerEditId = ref<number | null>(null)
const detail = ref<AfterSaleRecord | null>(null)
const detailType = ref<'return' | 'exchange'>('return')

const returnForm = reactive<ReturnFormState>({
  order_id: 0,
  warehouse_id: 0,
  reason: '',
  logistics_company: '',
  tracking_no: '',
  buyer_phone: '',
  is_arrived: false,
  freight: '',
  is_rejected_sign: false,
  return_person: '',
  goods_status: '',
  need_return_goods: true,
  items: [],
})

const exchangeForm = reactive<ExchangeFormState>({
  order_id: 0,
  reason: '',
  is_cod: false,
  is_return: false,
  compensate_order_no: '',
  compensate_amount: '',
  compensate_direction: null,
  items: [],
})

const registerForm = reactive<ReturnRegisterPayload>({
  logistics_company: '',
  tracking_no: '',
  sender: '',
  sender_phone: '',
  product_info: '',
  image: '',
})

const logisticsForm = reactive({
  logistics_company: '',
  tracking_no: '',
})

const selectableWarehouses = computed(() =>
  warehouses.value.filter((item) => item.selectable),
)
const detailTitle = computed(() =>
  detailType.value === 'return'
    ? `退货详情 · ${detail.value?.return_no || ''}`
    : `换货详情 · ${detail.value?.exchange_no || ''}`,
)
const filteredRegisters = computed(() => {
  const keywordValue = registerKeyword.value.trim().toLowerCase()
  if (!keywordValue) {
    return registers.value
  }
  return registers.value.filter((item) =>
    [
      item.target_no,
      item.tracking_no,
      item.logistics_company,
      item.sender,
      item.product_info,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keywordValue)),
  )
})

async function loadOptions(): Promise<void> {
  const results = await Promise.allSettled([
    orderApi
      .list({ page: 1, page_size: 200 })
      .then((result) => {
        orders.value = result.items
      }),
    warehouseApi.list().then((result) => {
      warehouses.value = result
    }),
  ])
  if (results.every((result) => result.status === 'rejected')) {
    ElMessage.error('售后基础选项加载失败')
  }
}

async function loadReturns(): Promise<void> {
  loading.value = true
  try {
    const result = await afterSaleApi.listReturns({
      keyword: keyword.value || undefined,
      status: statusFilter.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    returnRows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货单加载失败')
  } finally {
    loading.value = false
  }
}

async function loadExchanges(): Promise<void> {
  loading.value = true
  try {
    const result = await afterSaleApi.listExchanges({
      keyword: keyword.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    exchangeRows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '换货单加载失败')
  } finally {
    loading.value = false
  }
}

async function loadRegisters(): Promise<void> {
  registerLoading.value = true
  try {
    registers.value = await afterSaleApi.listRegisters()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货登记加载失败')
  } finally {
    registerLoading.value = false
  }
}

function search(): void {
  page.value = 1
  if (activeTab.value === 'exchanges') {
    void loadExchanges()
  } else {
    void loadReturns()
  }
}

function handleTabChange(name: string): void {
  page.value = 1
  keyword.value = ''
  statusFilter.value = ''
  if (name === 'returns') {
    void loadReturns()
  } else if (name === 'exchanges') {
    void loadExchanges()
  } else {
    void loadRegisters()
  }
}

function orderItems(order: OrderRecord): AfterSaleItemRow[] {
  return (order.items || []).map((item) => ({
    product_id: item.product_id,
    sku_id: item.sku_id || null,
    product_code: item.product_code,
    product_name: item.product_name,
    spec_code: item.spec_code,
    spec_name: item.sku_name,
    qty: String(item.qty || '0'),
    exchange_qty: String(item.qty || '0'),
    refund_amount: String(item.amount || '0.00'),
  }))
}

async function loadOrderItems(
  orderId: number,
  target: 'return' | 'exchange',
): Promise<void> {
  try {
    const order = await orderApi.get(orderId)
    if (target === 'return') {
      returnForm.items = orderItems(order)
    } else {
      exchangeForm.items = orderItems(order)
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '订单商品加载失败')
  }
}

function openCreateReturn(): void {
  Object.assign(returnForm, {
    order_id: orders.value[0]?.id || 0,
    warehouse_id: selectableWarehouses.value[0]?.id || 0,
    reason: '',
    logistics_company: '',
    tracking_no: '',
    buyer_phone: '',
    is_arrived: false,
    freight: '',
    is_rejected_sign: false,
    return_person: '',
    goods_status: '',
    need_return_goods: true,
    items: [],
  })
  if (returnForm.order_id) {
    void loadReturnOrderItems(returnForm.order_id)
  }
  returnFormVisible.value = true
}

function openCreateExchange(): void {
  Object.assign(exchangeForm, {
    order_id: orders.value[0]?.id || 0,
    reason: '',
    is_cod: false,
    is_return: false,
    compensate_order_no: '',
    compensate_amount: '',
    compensate_direction: null,
    items: [],
  })
  if (exchangeForm.order_id) {
    void loadExchangeOrderItems(exchangeForm.order_id)
  }
  exchangeFormVisible.value = true
}

function openCreateRegister(): void {
  registerEditId.value = null
  Object.assign(registerForm, {
    logistics_company: '',
    tracking_no: '',
    sender: '',
    sender_phone: '',
    product_info: '',
    image: '',
  })
  registerFormVisible.value = true
}

function openEditRegister(row: ReturnRegister): void {
  registerEditId.value = row.id
  Object.assign(registerForm, {
    logistics_company: row.logistics_company || '',
    tracking_no: row.tracking_no || '',
    sender: row.sender || '',
    sender_phone: row.sender_phone || '',
    product_info: row.product_info || '',
    image: row.image || '',
  })
  registerFormVisible.value = true
}

function loadReturnOrderItems(orderId: number): void {
  void loadOrderItems(orderId, 'return')
}

function loadExchangeOrderItems(orderId: number): void {
  void loadOrderItems(orderId, 'exchange')
}

async function submitReturn(): Promise<void> {
  const items = returnForm.items
    .filter((item) => Number(item.qty) > 0)
    .map((item) => ({
      product_id: item.product_id,
      sku_id: item.sku_id,
      qty: item.qty.trim(),
      refund_amount: item.refund_amount.trim() || null,
    }))
  if (!returnForm.order_id || !returnForm.warehouse_id || !items.length) {
    ElMessage.warning('请选择订单、仓库并填写退货数量')
    return
  }
  saving.value = true
  try {
    await afterSaleApi.createReturn({
      ...returnForm,
      reason: returnForm.reason?.trim() || null,
      logistics_company: returnForm.logistics_company?.trim() || null,
      tracking_no: returnForm.tracking_no?.trim() || null,
      buyer_phone: returnForm.buyer_phone?.trim() || null,
      freight: returnForm.freight?.trim() || null,
      return_person: returnForm.return_person?.trim() || null,
      goods_status: returnForm.goods_status?.trim() || null,
      items,
    })
    ElMessage.success('退货单已创建')
    returnFormVisible.value = false
    await loadReturns()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货单创建失败')
  } finally {
    saving.value = false
  }
}

async function submitExchange(): Promise<void> {
  const items = exchangeForm.items
    .filter((item) => Number(item.exchange_qty) > 0)
    .map((item) => ({
      product_id: item.product_id,
      sku_id: item.sku_id,
      qty: item.qty.trim() || null,
      exchange_qty: item.exchange_qty.trim(),
    }))
  if (!exchangeForm.order_id || !items.length) {
    ElMessage.warning('请选择订单并填写换货数量')
    return
  }
  saving.value = true
  try {
    await afterSaleApi.createExchange({
      ...exchangeForm,
      reason: exchangeForm.reason?.trim() || null,
      compensate_order_no: exchangeForm.compensate_order_no?.trim() || null,
      compensate_amount: exchangeForm.compensate_amount?.trim() || null,
      items,
    })
    ElMessage.success('换货单已创建')
    exchangeFormVisible.value = false
    await loadExchanges()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '换货单创建失败')
  } finally {
    saving.value = false
  }
}

async function submitRegister(): Promise<void> {
  saving.value = true
  try {
    const payload: ReturnRegisterPayload = {
      logistics_company: registerForm.logistics_company?.trim() || null,
      tracking_no: registerForm.tracking_no?.trim() || null,
      sender: registerForm.sender?.trim() || null,
      sender_phone: registerForm.sender_phone?.trim() || null,
      product_info: registerForm.product_info?.trim() || null,
      image: registerForm.image?.trim() || null,
    }
    if (registerEditId.value) {
      await afterSaleApi.updateRegister(registerEditId.value, payload)
    } else {
      await afterSaleApi.createRegister(payload)
    }
    ElMessage.success(registerEditId.value ? '退货登记已更新' : '退货登记已创建')
    registerFormVisible.value = false
    await loadRegisters()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货登记保存失败')
  } finally {
    saving.value = false
  }
}

async function removeRegister(row: ReturnRegister): Promise<void> {
  try {
    await ElMessageBox.confirm('确定删除该退货登记吗？', '删除退货登记', {
      type: 'warning',
    })
    await afterSaleApi.removeRegister(row.id)
    ElMessage.success('退货登记已删除')
    await loadRegisters()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '退货登记删除失败')
    }
  }
}

async function openReturnDetail(row: AfterSaleRecord): Promise<void> {
  detailType.value = 'return'
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await afterSaleApi.getReturn(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function openExchangeDetail(row: AfterSaleRecord): Promise<void> {
  detailType.value = 'exchange'
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await afterSaleApi.getExchange(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '换货详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (!detail.value) {
    return
  }
  detail.value =
    detailType.value === 'return'
      ? await afterSaleApi.getReturn(detail.value.id)
      : await afterSaleApi.getExchange(detail.value.id)
}

async function submitAfterSale(): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    if (detailType.value === 'return') {
      await afterSaleApi.submitReturn(detail.value.id)
      await loadReturns()
    } else {
      await afterSaleApi.submitExchange(detail.value.id)
      await loadExchanges()
    }
    ElMessage.success('售后单已提交')
    await refreshDetail()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '售后单提交失败')
  } finally {
    actionLoading.value = false
  }
}

async function confirmAfterSale(): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    const { value } = await ElMessageBox.prompt('可填写入库确认意见', '确认退货入库', {
      inputPlaceholder: '可留空',
    })
    await afterSaleApi.confirmReturn(detail.value.id, {
      comment: value || null,
    })
    ElMessage.success('退货已确认并入库')
    await Promise.all([loadReturns(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '退货确认失败')
    }
  } finally {
    actionLoading.value = false
  }
}

function openLogistics(): void {
  if (!detail.value) {
    return
  }
  logisticsForm.logistics_company = detail.value.logistics_company || ''
  logisticsForm.tracking_no = detail.value.tracking_no || ''
  logisticsVisible.value = true
}

async function submitLogistics(): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    await afterSaleApi.updateReturnLogistics(detail.value.id, {
      logistics_company: logisticsForm.logistics_company.trim() || null,
      tracking_no: logisticsForm.tracking_no.trim() || null,
    })
    ElMessage.success('退货物流已更新')
    logisticsVisible.value = false
    await refreshDetail()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货物流更新失败')
  } finally {
    actionLoading.value = false
  }
}

async function cancelAfterSale(): Promise<void> {
  if (!detail.value) {
    return
  }
  try {
    const { value } = await ElMessageBox.prompt('请填写取消原因', '取消售后', {
      inputValidator: (value) => Boolean(value?.trim()) || '请填写取消原因',
    })
    actionLoading.value = true
    if (detailType.value === 'return') {
      await afterSaleApi.cancelReturn(detail.value.id, value)
      await loadReturns()
    } else {
      await afterSaleApi.cancelExchange(detail.value.id, value)
      await loadExchanges()
    }
    ElMessage.success('售后单已取消')
    await refreshDetail()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '售后取消失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function exportReturns(): Promise<void> {
  try {
    const blob = await afterSaleApi.exportReturns({
      keyword: keyword.value || undefined,
      status: statusFilter.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
    })
    saveBlob(blob, '退货单.xlsx')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退货单导出失败')
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), loadReturns()])
})
</script>
