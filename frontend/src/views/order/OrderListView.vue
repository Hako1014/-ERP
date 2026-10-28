<template>
  <div class="settings-page order-page">
    <SettingsPageHeader :title="pageConfig.title" :description="pageConfig.description">
      <template #actions>
        <el-button @click="exportVisible = true">导入导出</el-button>
        <el-button
          v-if="pageConfig.showPrint"
          :icon="Printer"
          @click="router.push({ name: 'order-print-workbench' })"
        >
          打印订单
        </el-button>
        <el-button
          v-if="pageConfig.showShipment"
          :icon="Van"
          @click="router.push({ name: 'order-scan-shipment' })"
        >
          扫描发货
        </el-button>
        <el-button
          v-if="canManage && pageConfig.showCreate"
          type="primary"
          :icon="Plus"
          @click="openCreate"
        >
          新建订单
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
              v-for="shop in shops"
              :key="shop.id"
              :label="shop.name"
              :value="shop.id"
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
              v-for="warehouse in warehouses"
              :key="warehouse.id"
              :label="`${warehouse.code} · ${warehouse.name}`"
              :value="warehouse.id"
            />
          </el-select>
          <el-date-picker
            v-model="dateRange"
            class="order-filter--date"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            value-format="YYYY-MM-DDTHH:mm:ss"
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
        empty-description="暂无订单"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #status="{ row }">
          <StatusTag
            :status="row.status"
            :label="labelOf(ORDER_STATUS_LABELS, row.status)"
            :tone="statusTone(row.status)"
          />
        </template>
        <template #audit="{ row }">
          <StatusTag
            :status="row.audit_status"
            :label="labelOf(ORDER_AUDIT_LABELS, row.audit_status)"
            :tone="statusTone(row.audit_status)"
          />
        </template>
        <template #pay="{ row }">
          <StatusTag
            :status="row.pay_status"
            :label="labelOf(ORDER_PAY_LABELS, row.pay_status)"
            :tone="row.pay_status === 'PAID' ? 'success' : 'warning'"
          />
        </template>
        <template #amount="{ row }">
          <MoneyText :value="row.order_amount" />
        </template>
        <template #tracking="{ row }">
          <div>{{ row.logistics_name || row.logistics_company || '--' }}</div>
          <div class="order-muted">{{ row.tracking_no || '未获取运单号' }}</div>
        </template>
        <template #receiver="{ row }">
          <div>{{ row.receiver_name || '--' }}</div>
          <div class="order-muted">{{ row.receiver_phone || '--' }}</div>
        </template>
        <template #orderedAt="{ row }">
          {{ formatDateTime(row.order_time || row.created_at) }}
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="formVisible"
      :title="editId ? '编辑订单' : '新建订单'"
      :loading="saving"
      width="980px"
      @submit="submitForm"
    >
      <div class="order-form-grid">
        <el-form-item label="订单号">
          <el-input
            v-model="form.order_no"
            placeholder="留空由系统生成"
            maxlength="64"
          />
        </el-form-item>
        <el-form-item label="店铺" required>
          <el-select v-model="form.shop_id" filterable placeholder="选择店铺">
            <el-option
              v-for="shop in shops"
              :key="shop.id"
              :label="shop.name"
              :value="shop.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="发货仓库">
          <el-select
            v-model="form.warehouse_id"
            clearable
            filterable
            placeholder="可审核时自动匹配"
          >
            <el-option
              v-for="warehouse in selectableWarehouses"
              :key="warehouse.id"
              :label="`${warehouse.code} · ${warehouse.name}`"
              :value="warehouse.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="物流公司">
          <el-select
            v-model="form.logistics_id"
            clearable
            filterable
            placeholder="可审核时自动匹配"
          >
            <el-option
              v-for="item in logistics"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="买家昵称">
          <el-input v-model="form.buyer_nick" maxlength="64" />
        </el-form-item>
        <el-form-item label="付款状态">
          <el-select v-model="form.pay_status">
            <el-option label="未付款" value="UNPAID" />
            <el-option label="已付款" value="PAID" />
            <el-option label="部分付款" value="PARTIAL" />
            <el-option label="已退款" value="REFUNDED" />
          </el-select>
        </el-form-item>
        <el-form-item label="收货人">
          <el-input v-model="form.receiver_name" maxlength="64" />
        </el-form-item>
        <el-form-item label="收货电话">
          <el-input v-model="form.receiver_phone" maxlength="32" />
        </el-form-item>
        <el-form-item label="收货省">
          <el-input v-model="form.receiver_province" maxlength="32" />
        </el-form-item>
        <el-form-item label="收货市">
          <el-input v-model="form.receiver_city" maxlength="32" />
        </el-form-item>
        <el-form-item class="is-full-width" label="收货地址">
          <el-input v-model="form.receiver_address" maxlength="255" />
        </el-form-item>
        <el-form-item label="实付金额">
          <el-input
            v-model="form.paid_amount"
            inputmode="decimal"
            placeholder="0.00"
          />
        </el-form-item>
        <el-form-item label="运费">
          <el-input v-model="form.freight" inputmode="decimal" placeholder="0.00" />
        </el-form-item>
        <el-form-item label="平台优惠">
          <el-input
            v-model="form.platform_discount"
            inputmode="decimal"
            placeholder="0.00"
          />
        </el-form-item>
        <el-form-item label="卖家优惠">
          <el-input
            v-model="form.seller_discount"
            inputmode="decimal"
            placeholder="0.00"
          />
        </el-form-item>
        <el-form-item label="订单重量(kg)">
          <el-input v-model="form.weight" inputmode="decimal" placeholder="0.000" />
        </el-form-item>
        <el-form-item label="订单标识">
          <el-select v-model="form.flags" multiple clearable>
            <el-option
              v-for="flag in flags"
              :key="flag.code"
              :label="flag.label"
              :value="flag.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item class="is-full-width" label="买家留言">
          <el-input v-model="form.buyer_message" maxlength="255" />
        </el-form-item>
        <el-form-item class="is-full-width" label="卖家备注">
          <el-input v-model="form.seller_remark" maxlength="255" />
        </el-form-item>
      </div>

      <section class="order-section">
        <div class="order-item__header">
          <h3 class="order-section__title">订单商品</h3>
          <el-button link type="primary" :icon="Plus" @click="addItem">
            添加商品
          </el-button>
        </div>
        <div class="order-item-list">
          <div v-for="(item, index) in form.items" :key="item.key" class="order-item">
            <div class="order-item__header">
              <span class="order-item__title">商品 {{ index + 1 }}</span>
              <el-button
                link
                type="danger"
                :disabled="form.items.length === 1"
                @click="removeItem(index)"
              >
                删除
              </el-button>
            </div>
            <div class="order-item__grid">
              <el-select
                v-model="item.product_id"
                filterable
                placeholder="选择商品"
                @change="handleProductChange(item)"
              >
                <el-option
                  v-for="product in products"
                  :key="product.id"
                  :label="`${product.code} · ${product.name}`"
                  :value="product.id"
                />
              </el-select>
              <el-select
                v-model="item.sku_id"
                clearable
                placeholder="商品级"
                :disabled="!item.product_id"
              >
                <el-option
                  v-for="sku in item.skus"
                  :key="sku.id"
                  :label="sku.spec_code || sku.spec_name || `规格 #${sku.id}`"
                  :value="sku.id"
                />
              </el-select>
              <el-input v-model="item.qty" inputmode="decimal" placeholder="数量" />
              <el-input
                v-model="item.price"
                inputmode="decimal"
                placeholder="单价"
              />
              <el-checkbox v-model="item.is_gift">赠品</el-checkbox>
            </div>
          </div>
        </div>
      </section>
    </FormDialog>

    <el-drawer
      v-model="detailVisible"
      :title="`订单详情 · ${detail?.order_no || ''}`"
      size="min(1040px, 97vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <template v-if="detail">
          <div class="order-toolbar">
            <div>
              <h2>{{ detail.order_no }}</h2>
              <p class="order-muted">
                下单时间：{{ formatDateTime(detail.order_time || detail.created_at) }}
              </p>
            </div>
            <div class="settings-page__actions">
              <StatusTag
                :status="detail.status"
                :label="labelOf(ORDER_STATUS_LABELS, detail.status)"
                :tone="statusTone(detail.status)"
              />
              <StatusTag
                :status="detail.audit_status"
                :label="labelOf(ORDER_AUDIT_LABELS, detail.audit_status)"
                :tone="statusTone(detail.audit_status)"
              />
            </div>
          </div>

          <el-descriptions :column="2" border>
            <el-descriptions-item label="店铺">
              {{ detail.shop_name || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="平台">
              {{ detail.platform_name || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="买家">
              {{ detail.buyer_nick || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="付款状态">
              {{ labelOf(ORDER_PAY_LABELS, detail.pay_status) }}
            </el-descriptions-item>
            <el-descriptions-item label="发货仓库">
              {{ detail.warehouse_name || '待指定' }}
            </el-descriptions-item>
            <el-descriptions-item label="物流公司">
              {{ detail.logistics_name || detail.logistics_company || '待指定' }}
            </el-descriptions-item>
            <el-descriptions-item label="运单号">
              {{ detail.tracking_no || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="订单标识">
              {{ detail.flags_label || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="收货信息" :span="2">
              {{ detail.receiver_name || '--' }}
              {{ detail.receiver_phone || '' }}
              {{ detail.receiver_region || '' }}
              {{ detail.receiver_address || '' }}
            </el-descriptions-item>
            <el-descriptions-item label="买家留言" :span="2">
              {{ detail.buyer_message || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="卖家备注" :span="2">
              {{ detail.seller_remark || detail.remark || '--' }}
            </el-descriptions-item>
          </el-descriptions>

          <div class="order-detail-amounts">
            <div class="order-detail-amounts__item">
              <div class="order-detail-amounts__label">订单金额</div>
              <div class="order-detail-amounts__value">
                <MoneyText :value="detail.order_amount" />
              </div>
            </div>
            <div class="order-detail-amounts__item">
              <div class="order-detail-amounts__label">实付金额</div>
              <div class="order-detail-amounts__value">
                <MoneyText :value="detail.paid_amount" />
              </div>
            </div>
            <div class="order-detail-amounts__item">
              <div class="order-detail-amounts__label">运费</div>
              <div class="order-detail-amounts__value">
                <MoneyText :value="detail.freight" />
              </div>
            </div>
          </div>

          <section class="order-section">
            <h3 class="order-section__title">商品明细</h3>
            <el-table :data="detail.items || []" border max-height="360">
              <el-table-column prop="product_code" label="商品编码" min-width="120" />
              <el-table-column prop="product_name" label="商品名称" min-width="180" />
              <el-table-column label="规格" min-width="130">
                <template #default="{ row }">
                  {{ row.spec_code || row.sku_name || '商品级' }}
                </template>
              </el-table-column>
              <el-table-column prop="qty" label="数量" width="100" align="right" />
              <el-table-column label="单价" width="110" align="right">
                <template #default="{ row }"><MoneyText :value="row.price" /></template>
              </el-table-column>
              <el-table-column label="金额" width="110" align="right">
                <template #default="{ row }"><MoneyText :value="row.amount" /></template>
              </el-table-column>
              <el-table-column label="赠品" width="80" align="center">
                <template #default="{ row }">{{ row.is_gift ? '是' : '否' }}</template>
              </el-table-column>
            </el-table>
          </section>

          <section v-if="detail.approval" class="order-section">
            <h3 class="order-section__title">审批流程</h3>
            <ApprovalFlow
              :state="detail.approval.state"
              :records="detail.approval.records"
              :flow-name="detail.approval.flow_name || '订单审批流'"
            />
          </section>

          <section class="order-section">
            <h3 class="order-section__title">包裹发货</h3>
            <el-table :data="shipments" border>
              <el-table-column prop="shipment_no" label="包裹号" min-width="170" />
              <el-table-column prop="package_no" label="第几包" width="90" />
              <el-table-column prop="tracking_no" label="运单号" min-width="160" />
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <StatusTag
                    :status="row.status"
                    :label="labelOf(SHIPMENT_STATUS_LABELS, row.status)"
                    :tone="statusTone(row.status)"
                  />
                </template>
              </el-table-column>
              <el-table-column label="平台回传" width="110">
                <template #default="{ row }">
                  {{ labelOf(PLATFORM_SYNC_LABELS, row.platform_sync_status) }}
                </template>
              </el-table-column>
              <el-table-column label="发货时间" min-width="160">
                <template #default="{ row }">
                  {{ formatDateTime(row.shipped_at) }}
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section class="order-section">
            <h3 class="order-section__title">操作留痕</h3>
            <OperationTimeline :items="operationLogs" />
          </section>

          <div class="order-actions">
            <el-button
              v-if="canManage && detail && canEditOrder(detail)"
              @click="editDetail"
            >
              编辑
            </el-button>
            <el-button
              v-if="canManage && detail && canSubmitOrder(detail)"
              type="primary"
              :loading="actionLoading"
              @click="runAction('submit')"
            >
              提交审核
            </el-button>
            <el-button
              v-if="canAudit && detail && canAuditOrder(detail)"
              type="success"
              :loading="actionLoading"
              @click="runAction('approve')"
            >
              审核通过
            </el-button>
            <el-button
              v-if="canAudit && detail && canAuditOrder(detail)"
              type="danger"
              plain
              :loading="actionLoading"
              @click="runAction('reject')"
            >
              审核不通过
            </el-button>
            <el-button
              v-if="canAudit && detail && canUnauditOrder(detail)"
              type="warning"
              plain
              :loading="actionLoading"
              @click="runAction('unaudit')"
            >
              弃审
            </el-button>
            <el-button
              v-if="canFulfill && detail && canSetFulfillment(detail)"
              @click="openFulfillment"
            >
              设置仓库/物流
            </el-button>
            <el-button
              v-if="canManage && detail"
              @click="openFlags"
            >
              修改标识
            </el-button>
            <el-button
              v-if="canFulfill && detail && detail.status === 'PENDING'"
              type="primary"
              @click="router.push({ name: 'order-scan-shipment' })"
            >
              去发货
            </el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <FormDialog
      v-model:visible="fulfillmentVisible"
      title="设置仓库和物流"
      :loading="actionLoading"
      width="620px"
      @submit="submitFulfillment"
    >
      <el-form-item label="发货仓库">
        <el-select
          v-model="fulfillmentForm.warehouse_id"
          clearable
          filterable
          placeholder="选择仓库"
        >
          <el-option
            v-for="warehouse in selectableWarehouses"
            :key="warehouse.id"
            :label="`${warehouse.code} · ${warehouse.name}`"
            :value="warehouse.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="物流公司">
        <el-select
          v-model="fulfillmentForm.logistics_id"
          clearable
          filterable
          placeholder="选择物流"
        >
          <el-option
            v-for="item in logistics"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="运单号">
        <el-input v-model="fulfillmentForm.tracking_no" maxlength="64" />
      </el-form-item>
    </FormDialog>

    <FormDialog
      v-model:visible="flagsVisible"
      title="修改订单标识"
      :loading="actionLoading"
      width="560px"
      @submit="submitFlags"
    >
      <el-form-item label="订单标识">
        <el-select v-model="selectedFlags" multiple>
          <el-option
            v-for="flag in flags"
            :key="flag.code"
            :label="flag.label"
            :value="flag.code"
          />
        </el-select>
      </el-form-item>
    </FormDialog>

    <ExcelImportExportDialog
      v-model:visible="exportVisible"
      title="订单导入导出"
      :template-url="ORDER_TEMPLATE_URL"
      :import-url="canManage ? '/orders/import' : undefined"
      :export-url="ORDER_EXPORT_URL"
      :export-params="exportParams"
      filename="订单.xlsx"
      @imported="load"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Printer, Van } from '@element-plus/icons-vue'

import { logisticsApi, type Logistics } from '@/api/logistics'
import {
  ORDER_EXPORT_URL,
  ORDER_TEMPLATE_URL,
  orderApi,
  shipmentApi,
  type OrderFlag,
  type OrderItemPayload,
  type OrderLog,
  type OrderPayload,
  type OrderRecord,
  type Shipment,
} from '@/api/order'
import { productOptionsApi, type ProductOption, type ProductSkuOption } from '@/api/productOptions'
import { shopApi, type Shop } from '@/api/shop'
import { warehouseApi, type Warehouse } from '@/api/stock'
import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import ExcelImportExportDialog from '@/components/business/ExcelImportExportDialog.vue'
import OperationTimeline from '@/components/business/OperationTimeline.vue'
import type { OperationLogItem } from '@/components/business/types'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import {
  clearAssistantSelectedDocument,
  setAssistantSelectedDocument,
} from '@/layouts/components/assistantShell'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  ORDER_AUDIT_LABELS,
  ORDER_PAY_LABELS,
  ORDER_STATUS_LABELS,
  PLATFORM_SYNC_LABELS,
  SHIPMENT_STATUS_LABELS,
  canAuditOrder,
  canEditOrder,
  canSetFulfillment,
  canSubmitOrder,
  canUnauditOrder,
  labelOf,
  orderPageConfig,
  statusTone,
} from '@/views/order/orderHelpers'
import '@/views/settings/settings.css'
import '@/views/order/order.css'

interface ItemFormRow {
  key: number
  product_id: number | null
  sku_id: number | null
  qty: string
  price: string
  is_gift: boolean
  skus: ProductSkuOption[]
}

interface OrderFormState {
  order_no: string
  shop_id: number
  warehouse_id: number | null
  logistics_id: number | null
  buyer_nick: string
  buyer_message: string
  seller_remark: string
  receiver_name: string
  receiver_phone: string
  receiver_province: string
  receiver_city: string
  receiver_address: string
  pay_status: string
  paid_amount: string
  freight: string
  platform_discount: string
  seller_discount: string
  weight: string
  flags: string[]
  items: ItemFormRow[]
}

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const canManage = computed(
  () => authStore.hasPermission('order:audit') || authStore.hasPermission('order:ship'),
)
const canAudit = computed(() => authStore.hasPermission('order:audit'))
const canFulfill = computed(() => authStore.hasPermission('order:ship'))
const pageConfig = computed(() => orderPageConfig(route.name))

const columns: DataTableColumn<OrderRecord>[] = [
  { prop: 'order_no', label: '订单号', minWidth: 170 },
  { prop: 'shop_name', label: '店铺', minWidth: 130 },
  { prop: 'buyer_nick', label: '买家', minWidth: 110 },
  { label: '收货人', slot: 'receiver', minWidth: 140 },
  { prop: 'warehouse_name', label: '发货仓库', minWidth: 120 },
  { label: '物流/运单号', slot: 'tracking', minWidth: 170 },
  { label: '金额', slot: 'amount', width: 120, align: 'right' },
  { prop: 'total_qty', label: '商品数量', width: 100, align: 'right' },
  { label: '订单状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '付款状态', slot: 'pay', width: 100, align: 'center' },
  { label: '下单时间', slot: 'orderedAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const rows = ref<OrderRecord[]>([])
const shops = ref<Shop[]>([])
const warehouses = ref<Warehouse[]>([])
const logistics = ref<Logistics[]>([])
const products = ref<ProductOption[]>([])
const flags = ref<OrderFlag[]>([])
const shipments = ref<Shipment[]>([])
const logs = ref<OrderLog[]>([])
const loading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const shopFilter = ref<number | null>(null)
const warehouseFilter = ref<number | null>(null)
const dateRange = ref<[string, string] | null>(null)
const formVisible = ref(false)
const detailVisible = ref(false)
const fulfillmentVisible = ref(false)
const flagsVisible = ref(false)
const exportVisible = ref(false)
const editId = ref<number | null>(null)
const detail = ref<OrderRecord | null>(null)
const selectedFlags = ref<string[]>([])
let detailLoadSequence = 0
let rowKey = 1

const form = reactive<OrderFormState>({
  order_no: '',
  shop_id: 0,
  warehouse_id: null,
  logistics_id: null,
  buyer_nick: '',
  buyer_message: '',
  seller_remark: '',
  receiver_name: '',
  receiver_phone: '',
  receiver_province: '',
  receiver_city: '',
  receiver_address: '',
  pay_status: 'PAID',
  paid_amount: '',
  freight: '',
  platform_discount: '',
  seller_discount: '',
  weight: '',
  flags: [],
  items: [],
})

const fulfillmentForm = reactive({
  warehouse_id: null as number | null,
  logistics_id: null as number | null,
  tracking_no: '',
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    shopFilter.value !== null ||
    warehouseFilter.value !== null ||
    Boolean(dateRange.value),
)
const selectableWarehouses = computed(() =>
  warehouses.value.filter((item) => item.selectable),
)
const exportParams = computed(() => ({
  shop_id: shopFilter.value || undefined,
  warehouse_id: warehouseFilter.value || undefined,
  status: pageConfig.value.status,
  audit_status: pageConfig.value.auditStatus,
  keyword: keyword.value || undefined,
  start_time: dateRange.value?.[0],
  end_time: dateRange.value?.[1],
}))
const operationLogs = computed<OperationLogItem[]>(() =>
  logs.value.map((item, index) => ({
    id: `${item.source}-${item.action}-${item.created_at || index}`,
    action: item.action,
    actor_name: item.operator_name,
    target_type: item.source,
    summary: item.summary,
    reason: item.reason,
    detail: item.detail,
    created_at: item.created_at,
  })),
)

function emptyItem(): ItemFormRow {
  return {
    key: rowKey++,
    product_id: null,
    sku_id: null,
    qty: '1',
    price: '',
    is_gift: false,
    skus: [],
  }
}

async function loadOptions(): Promise<void> {
  const tasks: Array<Promise<unknown>> = [
    shopApi.listShops({ page: 1, page_size: 200 }).then((result) => {
      shops.value = result.items
    }),
    warehouseApi.list().then((result) => {
      warehouses.value = result
    }),
    logisticsApi.list(undefined, true).then((result) => {
      logistics.value = result
    }),
    orderApi.flags().then((result) => {
      flags.value = result
    }),
  ]
  if (canManage.value) {
    tasks.push(
      productOptionsApi.listProducts(undefined, 1, 200).then((result) => {
        products.value = result.items
      }),
    )
  }
  const results = await Promise.allSettled(tasks)
  if (results.every((result) => result.status === 'rejected')) {
    ElMessage.error('订单基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await orderApi.list({
      shop_id: shopFilter.value || undefined,
      warehouse_id: warehouseFilter.value || undefined,
      status: pageConfig.value.status,
      audit_status: pageConfig.value.auditStatus,
      keyword: keyword.value || undefined,
      start_time: dateRange.value?.[0],
      end_time: dateRange.value?.[1],
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '订单加载失败')
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
  dateRange.value = null
  search()
}

function resetForm(): void {
  Object.assign(form, {
    order_no: '',
    shop_id: shops.value[0]?.id || 0,
    warehouse_id: selectableWarehouses.value[0]?.id || null,
    logistics_id: logistics.value[0]?.id || null,
    buyer_nick: '',
    buyer_message: '',
    seller_remark: '',
    receiver_name: '',
    receiver_phone: '',
    receiver_province: '',
    receiver_city: '',
    receiver_address: '',
    pay_status: 'PAID',
    paid_amount: '',
    freight: '',
    platform_discount: '',
    seller_discount: '',
    weight: '',
    flags: [],
    items: [emptyItem()],
  })
}

function openCreate(): void {
  editId.value = null
  resetForm()
  formVisible.value = true
}

function addItem(): void {
  form.items.push(emptyItem())
}

function removeItem(index: number): void {
  form.items.splice(index, 1)
}

async function handleProductChange(item: ItemFormRow): Promise<void> {
  item.sku_id = null
  item.skus = []
  if (!item.product_id) {
    return
  }
  try {
    item.skus = await productOptionsApi.listSkus(item.product_id)
    if (item.skus.length === 1) {
      item.sku_id = item.skus[0].id
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品规格加载失败')
  }
}

function itemPayloads(): OrderItemPayload[] {
  return form.items.map((item) => ({
    product_id: Number(item.product_id),
    sku_id: item.sku_id,
    qty: item.qty.trim(),
    price: item.price.trim() || null,
    is_gift: item.is_gift,
  }))
}

async function submitForm(): Promise<void> {
  if (!form.shop_id) {
    ElMessage.warning('请选择店铺')
    return
  }
  const items = itemPayloads()
  if (
    !items.length ||
    items.some((item) => !item.product_id || !item.qty || Number(item.qty) <= 0)
  ) {
    ElMessage.warning('请完整填写商品和数量')
    return
  }
  saving.value = true
  try {
    const payload: OrderPayload = {
      order_no: form.order_no.trim() || null,
      shop_id: form.shop_id,
      warehouse_id: form.warehouse_id,
      logistics_id: form.logistics_id,
      buyer_nick: form.buyer_nick.trim() || null,
      buyer_message: form.buyer_message.trim() || null,
      seller_remark: form.seller_remark.trim() || null,
      receiver_name: form.receiver_name.trim() || null,
      receiver_phone: form.receiver_phone.trim() || null,
      receiver_province: form.receiver_province.trim() || null,
      receiver_city: form.receiver_city.trim() || null,
      receiver_address: form.receiver_address.trim() || null,
      pay_status: form.pay_status,
      paid_amount: form.paid_amount.trim() || null,
      freight: form.freight.trim() || null,
      platform_discount: form.platform_discount.trim() || null,
      seller_discount: form.seller_discount.trim() || null,
      weight: form.weight.trim() || null,
      flags: form.flags,
      items,
    }
    if (editId.value) {
      await orderApi.update(editId.value, payload)
    } else {
      await orderApi.create(payload)
    }
    ElMessage.success(editId.value ? '订单已更新' : '订单已创建')
    formVisible.value = false
    await load()
    if (detail.value) {
      await refreshDetail()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '订单保存失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(row: OrderRecord): Promise<void> {
  const sequence = ++detailLoadSequence
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  clearAssistantSelectedDocument()
  try {
    const [order, shipmentRows, logRows] = await Promise.all([
      orderApi.get(row.id),
      shipmentApi.listByOrder(row.id),
      orderApi.logs(row.id),
    ])
    if (sequence !== detailLoadSequence || !detailVisible.value) return
    detail.value = order
    shipments.value = shipmentRows
    logs.value = logRows
    const staffId = authStore.profile?.staff_id
    if (staffId) {
      setAssistantSelectedDocument(staffId, {
        doc_type: 'order',
        doc_id: order.id,
      })
    }
  } catch (error) {
    if (sequence === detailLoadSequence) {
      ElMessage.error(error instanceof Error ? error.message : '订单详情加载失败')
    }
  } finally {
    if (sequence === detailLoadSequence) detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (!detail.value) {
    return
  }
  detail.value = await orderApi.get(detail.value.id)
}

function editDetail(): void {
  if (!detail.value) {
    return
  }
  editId.value = detail.value.id
  const itemRows: ItemFormRow[] = (detail.value.items || []).map((item) => ({
    key: rowKey++,
    product_id: item.product_id,
    sku_id: item.sku_id || null,
    qty: String(item.qty || '1'),
    price: item.price === null || item.price === undefined ? '' : String(item.price),
    is_gift: item.is_gift,
    skus: item.sku_id
      ? [
          {
            id: item.sku_id,
            product_id: item.product_id,
            spec_code: item.spec_code,
            spec_name: item.sku_name,
          },
        ]
      : [],
  }))
  Object.assign(form, {
    order_no: detail.value.order_no,
    shop_id: detail.value.shop_id,
    warehouse_id: detail.value.warehouse_id || null,
    logistics_id: detail.value.logistics_id || null,
    buyer_nick: detail.value.buyer_nick || '',
    buyer_message: detail.value.buyer_message || '',
    seller_remark: detail.value.seller_remark || '',
    receiver_name: detail.value.receiver_name || '',
    receiver_phone: detail.value.receiver_phone || '',
    receiver_province: detail.value.receiver_province || '',
    receiver_city: detail.value.receiver_city || '',
    receiver_address: detail.value.receiver_address || '',
    pay_status: detail.value.pay_status || 'UNPAID',
    paid_amount: String(detail.value.paid_amount || ''),
    freight: String(detail.value.freight || ''),
    platform_discount: String(detail.value.platform_discount || ''),
    seller_discount: String(detail.value.seller_discount || ''),
    weight: detail.value.weight === null ? '' : String(detail.value.weight || ''),
    flags: [...detail.value.flags],
    items: itemRows.length ? itemRows : [emptyItem()],
  })
  detailVisible.value = false
  formVisible.value = true
}

async function runAction(
  action: 'submit' | 'approve' | 'reject' | 'unaudit',
): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await orderApi.submit(detail.value.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核通过', {
        inputPlaceholder: '可留空',
      })
      await orderApi.approve(detail.value.id, value || undefined)
    } else if (action === 'reject') {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await orderApi.reject(detail.value.id, value)
    } else {
      const { value } = await ElMessageBox.prompt('请填写弃审原因', '弃审订单', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写原因',
      })
      await orderApi.unaudit(detail.value.id, value)
    }
    ElMessage.success('订单状态已更新')
    await Promise.all([load(), openDetail(detail.value)])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '订单操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

function openFulfillment(): void {
  if (!detail.value) {
    return
  }
  Object.assign(fulfillmentForm, {
    warehouse_id: detail.value.warehouse_id || null,
    logistics_id: detail.value.logistics_id || null,
    tracking_no: detail.value.tracking_no || '',
  })
  fulfillmentVisible.value = true
}

async function submitFulfillment(): Promise<void> {
  if (!detail.value) {
    return
  }
  if (!fulfillmentForm.warehouse_id && !fulfillmentForm.logistics_id) {
    ElMessage.warning('请至少设置仓库或物流公司')
    return
  }
  actionLoading.value = true
  try {
    if (fulfillmentForm.warehouse_id) {
      await orderApi.setWarehouse(detail.value.id, fulfillmentForm.warehouse_id)
    }
    if (fulfillmentForm.logistics_id || fulfillmentForm.tracking_no.trim()) {
      await orderApi.setLogistics(detail.value.id, {
        logistics_id: fulfillmentForm.logistics_id,
        tracking_no: fulfillmentForm.tracking_no.trim() || null,
      })
    }
    ElMessage.success('订单履约信息已更新')
    fulfillmentVisible.value = false
    await Promise.all([load(), openDetail(detail.value)])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '订单履约信息保存失败')
  } finally {
    actionLoading.value = false
  }
}

function openFlags(): void {
  if (!detail.value) {
    return
  }
  selectedFlags.value = [...detail.value.flags]
  flagsVisible.value = true
}

async function submitFlags(): Promise<void> {
  if (!detail.value) {
    return
  }
  actionLoading.value = true
  try {
    await orderApi.setFlags(detail.value.id, selectedFlags.value, 'SET')
    ElMessage.success('订单标识已更新')
    flagsVisible.value = false
    await Promise.all([load(), openDetail(detail.value)])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '订单标识保存失败')
  } finally {
    actionLoading.value = false
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})

watch(
  detailVisible,
  (visible) => {
    if (!visible) {
      detailLoadSequence += 1
      detail.value = null
      clearAssistantSelectedDocument()
    }
  },
)

watch(
  () => route.name,
  () => {
    page.value = 1
    detailVisible.value = false
    void load()
  },
)

onBeforeUnmount(() => clearAssistantSelectedDocument())
</script>
