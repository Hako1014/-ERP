<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="调拨管理"
      description="创建仓库调拨单，跟踪调拨出库、下架、调拨入库、审核与签收。"
    >
      <template #actions>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新建调拨单
        </el-button>
      </template>
    </SettingsPageHeader>

    <el-tabs v-model="activeTab" class="stock-tabs" @tab-change="handleTabChange">
      <el-tab-pane label="调拨单" name="transfers">
        <section class="settings-page__panel">
          <div class="stock-toolbar">
            <div class="stock-toolbar__filters">
              <el-input
                v-model="keyword"
                class="settings-page__search"
                clearable
                placeholder="调拨单号"
                @keyup.enter="search"
                @clear="search"
              />
              <el-select
                v-model="fromWarehouseFilter"
                class="stock-filter--wide"
                clearable
                filterable
                placeholder="全部调出仓库"
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
                v-model="statusFilter"
                class="stock-filter"
                placeholder="全部状态"
                @change="search"
              >
                <el-option label="全部状态" value="" />
                <el-option label="待处理" value="PENDING" />
                <el-option label="已完成" value="FINISHED" />
                <el-option label="已作废" value="TERMINATED" />
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
            empty-description="暂无调拨单"
            empty-action-text="重置筛选"
            :show-empty-action="hasFilters"
            @query-change="load"
            @empty-action="resetAndLoad"
          >
            <template #route="{ row }">
              {{ row.from_warehouse_name }} → {{ row.to_warehouse_name }}
            </template>
            <template #status="{ row }">
              <StatusTag
                :status="row.status"
                :label="labelOf(STOCK_DOC_STATUS_LABELS, row.status)"
                :tone="stockStatusTone(row.status)"
              />
            </template>
            <template #audit="{ row }">
              <StatusTag
                :status="row.audit_status"
                :label="labelOf(STOCK_AUDIT_LABELS, row.audit_status)"
                :tone="stockStatusTone(row.audit_status)"
              />
            </template>
            <template #outStatus="{ row }">
              <StatusTag
                :status="row.transfer_out?.status || 'PENDING'"
                :label="
                  row.transfer_out
                    ? labelOf(
                        STOCK_DOC_STATUS_LABELS,
                        row.transfer_out.status,
                      )
                    : '未生成'
                "
                :tone="
                  row.transfer_out?.status === 'COMPLETED'
                    ? 'success'
                    : 'warning'
                "
              />
            </template>
            <template #inStatus="{ row }">
              <StatusTag
                :status="row.transfer_in?.status || 'PENDING'"
                :label="
                  row.transfer_in
                    ? labelOf(STOCK_DOC_STATUS_LABELS, row.transfer_in.status)
                    : '未生成'
                "
                :tone="stockStatusTone(row.transfer_in?.status)"
              />
            </template>
            <template #createdAt="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
            <template #actions="{ row }">
              <el-button link type="primary" @click="openTransferDetail(row)">
                详情
              </el-button>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>

      <el-tab-pane label="调拨出库" name="outs">
        <section class="settings-page__panel">
          <div class="stock-toolbar">
            <div class="stock-toolbar__filters">
              <el-select
                v-model="outWarehouseFilter"
                class="stock-filter--wide"
                clearable
                filterable
                placeholder="全部调出仓库"
                @change="loadOuts"
              >
                <el-option
                  v-for="item in warehouses"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                />
              </el-select>
              <el-button @click="loadOuts">刷新</el-button>
            </div>
          </div>
          <DataTable
            v-model:page="outPage"
            v-model:page-size="outPageSize"
            :rows="outRows"
            :columns="outColumns"
            :loading="outLoading"
            :total="outTotal"
            :sync-query="false"
            empty-description="暂无调拨出库单"
            :show-empty-action="false"
            @query-change="loadOuts"
          >
            <template #status="{ row }">
              <StatusTag
                :status="row.status"
                :label="labelOf(STOCK_DOC_STATUS_LABELS, row.status)"
                :tone="stockStatusTone(row.status)"
              />
            </template>
            <template #audit="{ row }">
              <StatusTag
                :status="row.audit_status"
                :label="labelOf(STOCK_AUDIT_LABELS, row.audit_status)"
                :tone="stockStatusTone(row.audit_status)"
              />
            </template>
            <template #createdAt="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
            <template #actions="{ row }">
              <el-button link type="primary" @click="openOutDetail(row)">
                详情
              </el-button>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>

      <el-tab-pane label="调拨入库" name="ins">
        <section class="settings-page__panel">
          <div class="stock-toolbar">
            <div class="stock-toolbar__filters">
              <el-select
                v-model="inWarehouseFilter"
                class="stock-filter--wide"
                clearable
                filterable
                placeholder="全部调入仓库"
                @change="loadIns"
              >
                <el-option
                  v-for="item in warehouses"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                />
              </el-select>
              <el-button @click="loadIns">刷新</el-button>
            </div>
          </div>
          <DataTable
            v-model:page="inPage"
            v-model:page-size="inPageSize"
            :rows="inRows"
            :columns="inColumns"
            :loading="inLoading"
            :total="inTotal"
            :sync-query="false"
            empty-description="暂无调拨入库单"
            :show-empty-action="false"
            @query-change="loadIns"
          >
            <template #status="{ row }">
              <StatusTag
                :status="row.status"
                :label="labelOf(STOCK_DOC_STATUS_LABELS, row.status)"
                :tone="stockStatusTone(row.status)"
              />
            </template>
            <template #audit="{ row }">
              <StatusTag
                :status="row.audit_status"
                :label="labelOf(STOCK_AUDIT_LABELS, row.audit_status)"
                :tone="stockStatusTone(row.audit_status)"
              />
            </template>
            <template #createdAt="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
            <template #actions="{ row }">
              <el-button link type="primary" @click="openInDetail(row)">
                详情
              </el-button>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>
    </el-tabs>

    <FormDialog
      v-model:visible="createVisible"
      title="新建调拨单"
      :loading="saving"
      width="900px"
      @submit="submitCreate"
    >
      <div class="stock-form-grid">
        <el-form-item label="调出仓库" required>
          <el-select v-model="createForm.from_warehouse_id" filterable>
            <el-option
              v-for="item in selectableWarehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="调入仓库" required>
          <el-select v-model="createForm.to_warehouse_id" filterable>
            <el-option
              v-for="item in enabledWarehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="同步线上库存">
          <el-switch v-model="createForm.sync_stock" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" maxlength="255" />
        </el-form-item>
      </div>

      <section class="stock-detail-section">
        <div class="stock-item__header">
          <h3 class="stock-detail-section__title">调拨商品</h3>
          <el-button link type="primary" @click="addCreateItem">
            添加一行
          </el-button>
        </div>
        <div class="stock-item-list">
          <div
            v-for="(item, index) in createForm.items"
            :key="item.key"
            class="stock-item"
          >
            <div class="stock-item__header">
              <span class="stock-operation__title">明细 {{ index + 1 }}</span>
              <el-button
                link
                type="danger"
                :disabled="createForm.items.length === 1"
                @click="createForm.items.splice(index, 1)"
              >
                删除
              </el-button>
            </div>
            <div class="stock-item__grid stock-item__grid--transfer">
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
                placeholder="选择规格"
                :disabled="!item.product_id"
              >
                <el-option
                  v-for="sku in item.skus"
                  :key="sku.id"
                  :label="sku.spec_code || sku.spec_name || `规格 #${sku.id}`"
                  :value="sku.id"
                />
              </el-select>
              <el-input
                v-model="item.qty"
                inputmode="decimal"
                placeholder="调拨数量"
              />
            </div>
          </div>
        </div>
      </section>
    </FormDialog>

    <el-drawer
      v-model="transferDetailVisible"
      :title="`调拨单详情 · ${transferDetail?.transfer_no || ''}`"
      size="min(880px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="调出仓库">
            {{ transferDetail?.from_warehouse_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="调入仓库">
            {{ transferDetail?.to_warehouse_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="调拨总数量">
            {{ transferDetail?.total_qty || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="同步线上库存">
            {{ transferDetail?.sync_stock ? '是' : '否' }}
          </el-descriptions-item>
          <el-descriptions-item label="单据状态">
            <StatusTag
              v-if="transferDetail"
              :status="transferDetail.status"
              :label="labelOf(STOCK_DOC_STATUS_LABELS, transferDetail.status)"
              :tone="stockStatusTone(transferDetail.status)"
            />
          </el-descriptions-item>
          <el-descriptions-item label="审核状态">
            <StatusTag
              v-if="transferDetail"
              :status="transferDetail.audit_status"
              :label="labelOf(STOCK_AUDIT_LABELS, transferDetail.audit_status)"
              :tone="stockStatusTone(transferDetail.audit_status)"
            />
          </el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">
            {{ transferDetail?.remark || '--' }}
          </el-descriptions-item>
        </el-descriptions>

        <section class="stock-detail-section">
          <h3 class="stock-detail-section__title">调拨明细</h3>
          <el-table :data="transferDetail?.items || []" border max-height="360">
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="170" />
            <el-table-column label="规格" min-width="120">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="qty" label="调拨数量" width="100" align="right" />
            <el-table-column
              prop="available_qty"
              label="当前可用"
              width="100"
              align="right"
            />
          </el-table>
        </section>

        <section
          v-if="transferDetail?.transfer_out || transferDetail?.transfer_in"
          class="stock-detail-section"
        >
          <h3 class="stock-detail-section__title">关联单据</h3>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="调拨出库单">
              {{ transferDetail?.transfer_out?.transfer_out_no || '未生成' }}
            </el-descriptions-item>
            <el-descriptions-item label="出库状态">
              {{ labelOf(STOCK_DOC_STATUS_LABELS, transferDetail?.transfer_out?.status) }}
            </el-descriptions-item>
            <el-descriptions-item label="调拨入库单">
              {{ transferDetail?.transfer_in?.transfer_in_no || '未生成' }}
            </el-descriptions-item>
            <el-descriptions-item label="入库状态">
              {{ labelOf(STOCK_DOC_STATUS_LABELS, transferDetail?.transfer_in?.status) }}
            </el-descriptions-item>
          </el-descriptions>
        </section>

        <section v-if="transferDetail?.approval" class="stock-detail-section">
          <ApprovalFlow
            :doc-type="transferDetail.approval.doc_type"
            :doc-id="transferDetail.approval.doc_id"
            :flow-name="transferDetail.approval.flow_name || '调拨单审批流'"
            :state="transferDetail.approval.state"
            :records="transferDetail.approval.records"
          />
        </section>

        <div class="stock-detail-actions">
          <el-button
            v-if="canManage && transferDetail && canSubmitApproval(transferDetail)"
            type="primary"
            :loading="actionLoading"
            @click="runTransferAction('submit')"
          >
            提交审批
          </el-button>
          <el-button
            v-if="canManage && transferDetail && canAuditApproval(transferDetail)"
            type="success"
            :loading="actionLoading"
            @click="runTransferAction('approve')"
          >
            审核通过
          </el-button>
          <el-button
            v-if="canManage && transferDetail && canAuditApproval(transferDetail)"
            type="danger"
            plain
            :loading="actionLoading"
            @click="runTransferAction('reject')"
          >
            审核不通过
          </el-button>
          <el-button
            v-if="
              canManage &&
              transferDetail?.transfer_out &&
              transferDetail.status === 'APPROVED'
            "
            @click="runOutAction(transferDetail.transfer_out, 'submit')"
          >
            提交出库审批
          </el-button>
          <el-button
            v-if="
              canManage &&
              transferDetail?.transfer_out &&
              transferDetail.transfer_out.audit_status === 'PENDING'
            "
            type="success"
            @click="runOutAction(transferDetail.transfer_out, 'approve')"
          >
            审核出库
          </el-button>
          <el-button
            v-if="
              canManage &&
              transferDetail?.transfer_out &&
              transferDetail.transfer_out.status === 'APPROVED'
            "
            type="primary"
            @click="openOutShelf(transferDetail.transfer_out)"
          >
            调拨出库下架
          </el-button>
          <el-button
            v-if="
              canManage &&
              transferDetail?.transfer_in &&
              transferDetail.transfer_in.audit_status === 'PENDING'
            "
            @click="runInAction(transferDetail.transfer_in, 'submit')"
          >
            提交入库审批
          </el-button>
          <el-button
            v-if="
              canManage &&
              transferDetail?.transfer_in &&
              transferDetail.transfer_in.audit_status === 'PENDING'
            "
            type="success"
            @click="runInAction(transferDetail.transfer_in, 'approve')"
          >
            审核入库
          </el-button>
          <el-button
            v-if="
              canManage &&
              transferDetail?.transfer_in &&
              transferDetail.transfer_in.status === 'APPROVED'
            "
            type="primary"
            @click="openReceive(transferDetail.transfer_in)"
          >
            入库签收
          </el-button>
          <el-button
            v-if="canManage && transferDetail && canEditStockDocument(transferDetail)"
            type="danger"
            plain
            @click="removeTransfer"
          >
            删除
          </el-button>
        </div>
      </div>
    </el-drawer>

    <el-drawer
      v-model="outDetailVisible"
      :title="`调拨出库详情 · ${outDetail?.transfer_out_no || ''}`"
      size="min(780px, 96vw)"
      destroy-on-close
    >
      <el-descriptions :column="2" border>
        <el-descriptions-item label="调出仓库">
          {{ outDetail?.warehouse_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="出库数量">
          {{ outDetail?.total_qty || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="单据状态">
          {{ labelOf(STOCK_DOC_STATUS_LABELS, outDetail?.status) }}
        </el-descriptions-item>
        <el-descriptions-item label="审核状态">
          {{ labelOf(STOCK_AUDIT_LABELS, outDetail?.audit_status) }}
        </el-descriptions-item>
      </el-descriptions>
      <section class="stock-detail-section">
        <h3 class="stock-detail-section__title">出库明细</h3>
        <el-table :data="outDetail?.items || []" border>
          <el-table-column prop="product_code" label="商品编码" min-width="120" />
          <el-table-column prop="product_name" label="商品名称" min-width="160" />
          <el-table-column prop="qty" label="数量" width="90" align="right" />
          <el-table-column prop="location_code" label="下架库位" min-width="160">
            <template #default="{ row }">{{ row.location_code || '待下架' }}</template>
          </el-table-column>
        </el-table>
      </section>
      <div class="stock-detail-actions">
        <el-button
          v-if="canManage && outDetail && canSubmitApproval(outDetail)"
          type="primary"
          @click="runOutAction(outDetail, 'submit')"
        >
          提交审批
        </el-button>
        <el-button
          v-if="canManage && outDetail && canAuditApproval(outDetail)"
          type="success"
          @click="runOutAction(outDetail, 'approve')"
        >
          审核通过
        </el-button>
        <el-button
          v-if="
            canManage &&
            outDetail &&
            outDetail.status === 'APPROVED' &&
            outDetail.items?.some((item) => !item.location_id)
          "
          @click="openOutShelf(outDetail)"
        >
          下架
        </el-button>
      </div>
    </el-drawer>

    <el-drawer
      v-model="inDetailVisible"
      :title="`调拨入库详情 · ${inDetail?.transfer_in_no || ''}`"
      size="min(780px, 96vw)"
      destroy-on-close
    >
      <el-descriptions :column="2" border>
        <el-descriptions-item label="调入仓库">
          {{ inDetail?.warehouse_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="入库数量">
          {{ inDetail?.total_qty || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="单据状态">
          {{ labelOf(STOCK_DOC_STATUS_LABELS, inDetail?.status) }}
        </el-descriptions-item>
        <el-descriptions-item label="审核状态">
          {{ labelOf(STOCK_AUDIT_LABELS, inDetail?.audit_status) }}
        </el-descriptions-item>
      </el-descriptions>
      <section class="stock-detail-section">
        <h3 class="stock-detail-section__title">入库明细</h3>
        <el-table :data="inDetail?.items || []" border>
          <el-table-column prop="product_code" label="商品编码" min-width="120" />
          <el-table-column prop="product_name" label="商品名称" min-width="160" />
          <el-table-column prop="qty" label="数量" width="90" align="right" />
          <el-table-column prop="location_code" label="签收库位" min-width="160">
            <template #default="{ row }">{{ row.location_code || '待签收' }}</template>
          </el-table-column>
        </el-table>
      </section>
      <div class="stock-detail-actions">
        <el-button
          v-if="canManage && inDetail && canSubmitApproval(inDetail)"
          type="primary"
          @click="runInAction(inDetail, 'submit')"
        >
          提交审批
        </el-button>
        <el-button
          v-if="canManage && inDetail && canAuditApproval(inDetail)"
          type="success"
          @click="runInAction(inDetail, 'approve')"
        >
          审核通过
        </el-button>
        <el-button
          v-if="
            canManage &&
            inDetail &&
            inDetail.status === 'APPROVED' &&
            inDetail.items?.some((item) => !item.location_id)
          "
          @click="openReceive(inDetail)"
        >
          入库签收
        </el-button>
      </div>
    </el-drawer>

    <FormDialog
      v-model:visible="operationVisible"
      :title="operationType === 'out' ? '调拨出库下架' : '调拨入库签收'"
      :loading="saving"
      width="780px"
      @submit="submitOperation"
    >
      <p class="stock-muted">
        {{ operationType === 'out' ? '选择调出仓可下架库位并确认数量。' : '选择调入仓上架库位并确认签收数量。' }}
      </p>
      <div class="stock-operation-list">
        <div
          v-for="item in operationItems"
          :key="item.itemId"
          class="stock-operation"
        >
          <div class="stock-operation__header">
            <div>
              <div class="stock-operation__title">
                {{ item.product_code }} · {{ item.product_name }}
              </div>
              <div class="stock-operation__meta">
                {{ item.spec_code || item.spec_name || '商品级' }}，数量
                {{ item.maxQty }}
              </div>
            </div>
          </div>
          <div class="stock-operation__fields">
            <el-select
              v-model="item.location_id"
              filterable
              placeholder="选择库位"
            >
              <el-option
                v-for="location in item.locations"
                :key="location.id"
                :label="`${location.code}${operationType === 'out' ? ` · 库存 ${location.used_qty}` : location.remaining_qty === null ? ' · 不限容量' : ` · 剩余 ${location.remaining_qty}`}`"
                :value="location.id"
              />
            </el-select>
            <el-input
              v-model="item.qty"
              inputmode="decimal"
              placeholder="作业数量"
            />
          </div>
        </div>
      </div>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { productOptionsApi, type ProductOption, type ProductSkuOption } from '@/api/productOptions'
import {
  stockQueryApi,
  transferApi,
  warehouseApi,
  type Location,
  type Transfer,
  type TransferDocQuery,
  type TransferIn,
  type TransferOut,
  type TransferPayload,
  type Warehouse,
} from '@/api/stock'
import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  OFF_SHELF_STATUS_LABELS,
  STOCK_AUDIT_LABELS,
  STOCK_DOC_STATUS_LABELS,
  canAuditApproval,
  canEditStockDocument,
  canSubmitApproval,
  labelOf,
  stockStatusTone,
} from '@/views/stock/stockHelpers'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

interface CreateItemRow {
  key: number
  product_id: number | null
  sku_id: number | null
  qty: string
  skus: ProductSkuOption[]
}

interface OperationRow {
  itemId: number
  product_code?: string | null
  product_name?: string | null
  spec_code?: string | null
  spec_name?: string | null
  maxQty: string
  location_id: number | null
  qty: string
  locations: Location[]
}

interface CreateForm {
  from_warehouse_id: number
  to_warehouse_id: number
  sync_stock: boolean
  remark: string
  items: CreateItemRow[]
}

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<Transfer>[] = [
  { prop: 'transfer_no', label: '调拨单号', minWidth: 180 },
  { label: '调拨路线', slot: 'route', minWidth: 230 },
  { prop: 'total_qty', label: '调拨数量', width: 100, align: 'right' },
  { label: '单据状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '出库进度', slot: 'outStatus', width: 110, align: 'center' },
  { label: '入库进度', slot: 'inStatus', width: 110, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]
const outColumns: DataTableColumn<TransferOut>[] = [
  { prop: 'transfer_out_no', label: '调拨出库单号', minWidth: 180 },
  { prop: 'warehouse_name', label: '调出仓库', minWidth: 130 },
  { prop: 'total_qty', label: '出库数量', width: 100, align: 'right' },
  { label: '状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]
const inColumns: DataTableColumn<TransferIn>[] = [
  { prop: 'transfer_in_no', label: '调拨入库单号', minWidth: 180 },
  { prop: 'warehouse_name', label: '调入仓库', minWidth: 130 },
  { prop: 'total_qty', label: '入库数量', width: 100, align: 'right' },
  { label: '状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 80, fixed: 'right' },
]

const activeTab = ref('transfers')
const rows = ref<Transfer[]>([])
const outRows = ref<TransferOut[]>([])
const inRows = ref<TransferIn[]>([])
const warehouses = ref<Warehouse[]>([])
const products = ref<ProductOption[]>([])
const loading = ref(false)
const outLoading = ref(false)
const inLoading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const outPage = ref(1)
const inPage = ref(1)
const pageSize = ref(20)
const outPageSize = ref(20)
const inPageSize = ref(20)
const total = ref(0)
const outTotal = ref(0)
const inTotal = ref(0)
const keyword = ref('')
const fromWarehouseFilter = ref<number | null>(null)
const statusFilter = ref('')
const outWarehouseFilter = ref<number | null>(null)
const inWarehouseFilter = ref<number | null>(null)
const createVisible = ref(false)
const transferDetailVisible = ref(false)
const outDetailVisible = ref(false)
const inDetailVisible = ref(false)
const operationVisible = ref(false)
const transferDetail = ref<Transfer | null>(null)
const outDetail = ref<TransferOut | null>(null)
const inDetail = ref<TransferIn | null>(null)
const operationType = ref<'out' | 'in'>('out')
const operationRecordId = ref<number | null>(null)
const operationItems = ref<OperationRow[]>([])
let rowKey = 1

const createForm = reactive<CreateForm>({
  from_warehouse_id: 0,
  to_warehouse_id: 0,
  sync_stock: false,
  remark: '',
  items: [],
})
const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    fromWarehouseFilter.value !== null ||
    Boolean(statusFilter.value),
)
const selectableWarehouses = computed(() =>
  warehouses.value.filter((item) => item.selectable && !item.is_virtual),
)
const enabledWarehouses = computed(() =>
  warehouses.value.filter((item) => item.is_enabled && !item.is_virtual),
)

function emptyItem(): CreateItemRow {
  return {
    key: rowKey++,
    product_id: null,
    sku_id: null,
    qty: '',
    skus: [],
  }
}

async function loadOptions(): Promise<void> {
  try {
    const [warehouseRows, productRows] = await Promise.all([
      warehouseApi.list(),
      productOptionsApi.listProducts(undefined, 1, 200),
    ])
    warehouses.value = warehouseRows
    products.value = productRows.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await transferApi.list({
      keyword: keyword.value || undefined,
      from_warehouse_id: fromWarehouseFilter.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨单加载失败')
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
  fromWarehouseFilter.value = null
  statusFilter.value = ''
  search()
}

function handleTabChange(name: string): void {
  if (name === 'outs') {
    void loadOuts()
  } else if (name === 'ins') {
    void loadIns()
  }
}

async function loadOuts(): Promise<void> {
  outLoading.value = true
  try {
    const result = await transferApi.listOuts({
      warehouse_id: outWarehouseFilter.value || undefined,
      page: outPage.value,
      page_size: outPageSize.value,
    })
    outRows.value = result.items
    outTotal.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨出库单加载失败')
  } finally {
    outLoading.value = false
  }
}

async function loadIns(): Promise<void> {
  inLoading.value = true
  try {
    const result = await transferApi.listIns({
      warehouse_id: inWarehouseFilter.value || undefined,
      page: inPage.value,
      page_size: inPageSize.value,
    })
    inRows.value = result.items
    inTotal.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨入库单加载失败')
  } finally {
    inLoading.value = false
  }
}

function openCreate(): void {
  Object.assign(createForm, {
    from_warehouse_id: selectableWarehouses.value[0]?.id || 0,
    to_warehouse_id: enabledWarehouses.value.find(
      (item) => item.id !== selectableWarehouses.value[0]?.id,
    )?.id || 0,
    sync_stock: false,
    remark: '',
    items: [emptyItem()],
  })
  createVisible.value = true
}

function addCreateItem(): void {
  createForm.items.push(emptyItem())
}

async function handleProductChange(item: CreateItemRow): Promise<void> {
  item.sku_id = null
  item.skus = item.product_id
    ? await productOptionsApi.listSkus(item.product_id)
    : []
  if (item.skus.length === 1) {
    item.sku_id = item.skus[0].id
  }
}

async function submitCreate(): Promise<void> {
  if (!createForm.from_warehouse_id || !createForm.to_warehouse_id) {
    ElMessage.warning('请选择调出仓库和调入仓库')
    return
  }
  if (createForm.from_warehouse_id === createForm.to_warehouse_id) {
    ElMessage.warning('调出仓库和调入仓库不能相同')
    return
  }
  const items = createForm.items.map((item) => ({
    product_id: Number(item.product_id),
    sku_id: Number(item.sku_id),
    qty: item.qty.trim(),
  }))
  if (items.some((item) => !item.product_id || !item.sku_id || Number(item.qty) <= 0)) {
    ElMessage.warning('请完整填写商品、规格和调拨数量')
    return
  }
  saving.value = true
  try {
    const result = await transferApi.create({
      from_warehouse_id: createForm.from_warehouse_id,
      to_warehouse_id: createForm.to_warehouse_id,
      sync_stock: createForm.sync_stock,
      remark: createForm.remark?.trim() || null,
      items,
    })
    ElMessage.success(`调拨单 ${result.transfer_no} 已创建`)
    createVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨单创建失败')
  } finally {
    saving.value = false
  }
}

async function openTransferDetail(row: Transfer): Promise<void> {
  transferDetailVisible.value = true
  detailLoading.value = true
  try {
    transferDetail.value = await transferApi.get(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨单详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshTransferDetail(): Promise<void> {
  if (transferDetail.value) {
    transferDetail.value = await transferApi.get(transferDetail.value.id)
  }
}

async function runTransferAction(
  action: 'submit' | 'approve' | 'reject',
): Promise<void> {
  if (!transferDetail.value) {
    return
  }
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await transferApi.submit(transferDetail.value.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核通过', {
        inputPlaceholder: '可留空',
      })
      await transferApi.approve(transferDetail.value.id, value || undefined)
    } else {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await transferApi.reject(transferDetail.value.id, value)
    }
    ElMessage.success('调拨单状态已更新')
    await Promise.all([load(), refreshTransferDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '调拨单操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

async function openOutDetail(row: TransferOut): Promise<void> {
  outDetailVisible.value = true
  try {
    outDetail.value = await transferApi.getOut(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨出库详情加载失败')
  }
}

async function openInDetail(row: TransferIn): Promise<void> {
  inDetailVisible.value = true
  try {
    inDetail.value = await transferApi.getIn(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调拨入库详情加载失败')
  }
}

async function runOutAction(
  row: TransferOut,
  action: 'submit' | 'approve' | 'reject',
): Promise<void> {
  try {
    if (action === 'submit') {
      await transferApi.submitOut(row.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核调拨出库', {
        inputPlaceholder: '可留空',
      })
      await transferApi.approveOut(row.id, value || undefined)
    } else {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '调拨出库审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await transferApi.rejectOut(row.id, value)
    }
    ElMessage.success('调拨出库状态已更新')
    await Promise.all([loadOuts(), refreshTransferDetail()])
    if (outDetail.value?.id === row.id) {
      outDetail.value = await transferApi.getOut(row.id)
    }
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '调拨出库操作失败')
    }
  }
}

async function runInAction(
  row: TransferIn,
  action: 'submit' | 'approve' | 'reject',
): Promise<void> {
  try {
    if (action === 'submit') {
      await transferApi.submitIn(row.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt('可填写审批意见', '审核调拨入库', {
        inputPlaceholder: '可留空',
      })
      await transferApi.approveIn(row.id, value || undefined)
    } else {
      const { value } = await ElMessageBox.prompt('请填写不通过原因', '调拨入库审核不通过', {
        inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
      })
      await transferApi.rejectIn(row.id, value)
    }
    ElMessage.success('调拨入库状态已更新')
    await Promise.all([loadIns(), refreshTransferDetail()])
    if (inDetail.value?.id === row.id) {
      inDetail.value = await transferApi.getIn(row.id)
    }
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '调拨入库操作失败')
    }
  }
}

async function openOutShelf(row: TransferOut): Promise<void> {
  const detailRow = row.items?.length ? row : await transferApi.getOut(row.id)
  const locations = await warehouseApi.listLocations({
    warehouse_id: detailRow.warehouse_id,
    is_enabled: true,
  })
  const stockLocations = await stockQueryApi.locations({
    warehouse_id: detailRow.warehouse_id,
  })
  operationType.value = 'out'
  operationRecordId.value = detailRow.id
  operationItems.value = (detailRow.items || []).map((item) => {
    const matched = locations.filter((location) =>
      stockLocations.some(
        (stock) =>
          stock.location_id === location.id &&
          stock.product_id === item.product_id &&
          stock.sku_id === item.sku_id &&
          Number(stock.qty) > 0,
      ),
    )
    return {
      itemId: item.id,
      product_code: item.product_code,
      product_name: item.product_name,
      spec_code: item.spec_code,
      spec_name: item.spec_name,
      maxQty: String(item.qty),
      location_id: matched[0]?.id || null,
      qty: String(item.qty),
      locations: matched,
    }
  })
  operationVisible.value = true
}

async function openReceive(row: TransferIn): Promise<void> {
  const detailRow = row.items?.length ? row : await transferApi.getIn(row.id)
  const locations = await warehouseApi.listLocations({
    warehouse_id: detailRow.warehouse_id,
    is_enabled: true,
  })
  operationType.value = 'in'
  operationRecordId.value = detailRow.id
  operationItems.value = (detailRow.items || []).map((item) => ({
    itemId: item.id,
    product_code: item.product_code,
    product_name: item.product_name,
    spec_code: item.spec_code,
    spec_name: item.spec_name,
    maxQty: String(item.qty),
    location_id: locations[0]?.id || null,
    qty: String(item.qty),
    locations,
  }))
  operationVisible.value = true
}

async function submitOperation(): Promise<void> {
  if (!operationRecordId.value) {
    return
  }
  const items = operationItems.value.map((item) => ({
    itemId: item.itemId,
    location_id: Number(item.location_id),
    qty: item.qty.trim(),
  }))
  if (items.some((item) => !item.location_id || Number(item.qty) <= 0)) {
    ElMessage.warning('请为每条明细选择库位并填写数量')
    return
  }
  saving.value = true
  try {
    if (operationType.value === 'out') {
      await transferApi.offShelfOut(
        operationRecordId.value,
        items.map((item) => ({
          transfer_out_item_id: item.itemId,
          location_id: item.location_id,
          qty: item.qty,
        })),
      )
      ElMessage.success('调拨出库下架完成')
    } else {
      await transferApi.receiveIn(
        operationRecordId.value,
        items.map((item) => ({
          transfer_in_item_id: item.itemId,
          location_id: item.location_id,
          qty: item.qty,
        })),
      )
      ElMessage.success('调拨入库签收完成')
    }
    operationVisible.value = false
    await Promise.all([loadOuts(), loadIns(), refreshTransferDetail()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库位作业失败')
  } finally {
    saving.value = false
  }
}

async function removeTransfer(): Promise<void> {
  if (!transferDetail.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除调拨单 ${transferDetail.value.transfer_no} 吗？`,
      '删除调拨单',
      { type: 'warning' },
    )
    await transferApi.remove(transferDetail.value.id)
    transferDetailVisible.value = false
    ElMessage.success('调拨单已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '调拨单删除失败')
    }
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>
