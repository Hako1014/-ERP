<template>
  <div class="settings-page order-page">
    <SettingsPageHeader
      title="波次管理"
      description="按仓库、物流、店铺和排序策略生成拣货波次与装载号。"
    >
      <template #actions>
        <el-button
          v-if="canManage"
          :icon="VideoPlay"
          @click="openRun"
        >
          运行波次
        </el-button>
        <el-button
          v-if="canManage"
          type="primary"
          :icon="Plus"
          @click="openCreateStrategy"
        >
          新建策略
        </el-button>
      </template>
    </SettingsPageHeader>

    <el-tabs v-model="activeTab" class="order-tabs" @tab-change="handleTabChange">
      <el-tab-pane label="波次策略" name="strategies">
        <section class="settings-page__panel">
          <div class="order-toolbar">
            <div class="order-toolbar__filters">
              <el-input
                v-model="strategyKeyword"
                class="settings-page__search"
                clearable
                placeholder="策略名称"
                @keyup.enter="searchStrategies"
                @clear="searchStrategies"
              />
              <el-select
                v-model="enabledFilter"
                class="order-filter"
                placeholder="全部状态"
                @change="searchStrategies"
              >
                <el-option label="全部状态" :value="null" />
                <el-option label="启用" :value="true" />
                <el-option label="停用" :value="false" />
              </el-select>
              <el-button @click="searchStrategies">查询</el-button>
            </div>
          </div>

          <DataTable
            v-model:page="strategyPage"
            v-model:page-size="strategyPageSize"
            :rows="strategies"
            :columns="strategyColumns"
            :loading="strategyLoading"
            :total="strategyTotal"
            :sync-query="false"
            empty-description="暂无波次策略"
            @query-change="loadStrategies"
          >
            <template #warehouse="{ row }">
              {{ row.warehouse_name || '不限仓库' }}
            </template>
            <template #logistics="{ row }">
              {{ row.logistics_name || '不限物流' }}
            </template>
            <template #shopCount="{ row }">
              {{ row.shop_ids.length ? `${row.shop_ids.length} 个店铺` : '全部店铺' }}
            </template>
            <template #sort="{ row }">
              {{ labelOf(WAVE_SORT_LABELS, row.sort_type) }}
            </template>
            <template #enabled="{ row }">
              <StatusTag
                :status="row.is_enabled ? 'ENABLED' : 'DISABLED'"
                :label="row.is_enabled ? '启用' : '停用'"
                :tone="row.is_enabled ? 'success' : 'info'"
              />
            </template>
            <template #actions="{ row }">
              <div class="settings-page__actions">
                <el-button link type="primary" @click="openEditStrategy(row)">
                  编辑
                </el-button>
                <el-button
                  v-if="canManage"
                  link
                  type="danger"
                  @click="removeStrategy(row)"
                >
                  删除
                </el-button>
              </div>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>

      <el-tab-pane label="波次记录" name="waves">
        <section class="settings-page__panel">
          <div class="order-toolbar">
            <div class="order-toolbar__filters">
              <el-input
                v-model="waveKeyword"
                class="settings-page__search"
                clearable
                placeholder="波次号或名称"
                @keyup.enter="searchWaves"
                @clear="searchWaves"
              />
              <el-select
                v-model="strategyFilter"
                class="order-filter--wide"
                clearable
                filterable
                placeholder="全部策略"
                @change="searchWaves"
              >
                <el-option
                  v-for="item in allStrategies"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                />
              </el-select>
              <el-button @click="searchWaves">查询</el-button>
            </div>
          </div>

          <DataTable
            v-model:page="wavePage"
            v-model:page-size="wavePageSize"
            :rows="waves"
            :columns="waveColumns"
            :loading="waveLoading"
            :total="waveTotal"
            :sync-query="false"
            empty-description="暂无波次记录"
            @query-change="loadWaves"
          >
            <template #loadNumbers="{ row }">
              <div class="order-load-number">
                <el-tag
                  v-for="item in row.load_numbers"
                  :key="item.id"
                  type="info"
                >
                  {{ item.load_no }}
                </el-tag>
                <span v-if="!row.load_numbers.length">--</span>
              </div>
            </template>
            <template #createdAt="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
            <template #actions="{ row }">
              <div class="settings-page__actions">
                <el-button link type="primary" @click="openWaveDetail(row)">
                  详情
                </el-button>
                <el-button
                  v-if="canManage"
                  link
                  type="danger"
                  @click="removeWave(row)"
                >
                  删除
                </el-button>
              </div>
            </template>
          </DataTable>
        </section>
      </el-tab-pane>
    </el-tabs>

    <FormDialog
      v-model:visible="strategyVisible"
      :title="editingStrategyId ? '编辑波次策略' : '新建波次策略'"
      :loading="saving"
      width="760px"
      @submit="submitStrategy"
    >
      <div class="order-form-grid">
        <el-form-item label="策略名称" required>
          <el-input v-model="strategyForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="单波次订单数">
          <el-input-number
            v-model="strategyForm.wave_order_count"
            :min="1"
            :max="5000"
          />
        </el-form-item>
        <el-form-item label="发货仓库">
          <el-select
            v-model="strategyForm.warehouse_id"
            clearable
            filterable
            placeholder="不限仓库"
          >
            <el-option
              v-for="item in warehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="物流公司">
          <el-select
            v-model="strategyForm.logistics_id"
            clearable
            filterable
            placeholder="不限物流"
          >
            <el-option
              v-for="item in logistics"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="排序方式">
          <el-select v-model="strategyForm.sort_type">
            <el-option
              v-for="(label, value) in WAVE_SORT_LABELS"
              :key="value"
              :label="label"
              :value="value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="装载号规则">
          <el-select v-model="strategyForm.load_no_type">
            <el-option
              v-for="(label, value) in LOAD_NO_TYPE_LABELS"
              :key="value"
              :label="label"
              :value="value"
            />
          </el-select>
        </el-form-item>
        <el-form-item class="is-full-width" label="适用店铺">
          <el-select
            v-model="strategyForm.shop_ids"
            multiple
            clearable
            filterable
            placeholder="不选择表示全部店铺"
          >
            <el-option
              v-for="item in shops"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="strategyForm.is_enabled" />
        </el-form-item>
        <el-form-item class="is-full-width" label="说明">
          <el-input v-model="strategyForm.description" maxlength="255" />
        </el-form-item>
      </div>
    </FormDialog>

    <FormDialog
      v-model:visible="runVisible"
      title="运行波次"
      :loading="running"
      width="640px"
      @submit="submitRun"
    >
      <el-form-item label="波次策略" required>
        <el-select v-model="runStrategyIds" multiple filterable>
          <el-option
            v-for="item in allStrategies.filter((row) => row.is_enabled)"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="指定订单">
        <el-input
          v-model="runOrderIds"
          placeholder="可留空；多个订单 ID 用逗号分隔"
        />
      </el-form-item>
    </FormDialog>

    <el-drawer
      v-model="detailVisible"
      :title="`波次详情 · ${waveDetail?.wave_no || ''}`"
      size="min(960px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading">
        <template v-if="waveDetail">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="波次名称">
              {{ waveDetail.name || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="订单数量">
              {{ waveDetail.total_order_count }}
            </el-descriptions-item>
            <el-descriptions-item label="创建人">
              {{ waveDetail.created_by_name || '--' }}
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">
              {{ formatDateTime(waveDetail.created_at) }}
            </el-descriptions-item>
          </el-descriptions>

          <section class="order-section">
            <h3 class="order-section__title">装载号</h3>
            <div class="order-load-number">
              <el-tag
                v-for="item in waveDetail.load_numbers"
                :key="item.id"
                type="info"
              >
                {{ item.load_no }} · {{ item.express_type }}
              </el-tag>
              <span v-if="!waveDetail.load_numbers.length">--</span>
            </div>
          </section>

          <section class="order-section">
            <h3 class="order-section__title">波次订单</h3>
            <el-table :data="waveDetail.orders || []" border max-height="480">
              <el-table-column prop="sequence" label="顺序" width="80" />
              <el-table-column prop="order_no" label="订单号" min-width="170" />
              <el-table-column prop="warehouse_name" label="仓库" min-width="120" />
              <el-table-column prop="logistics_name" label="物流" min-width="120" />
              <el-table-column prop="load_no" label="装载号" min-width="130" />
              <el-table-column prop="receiver_name" label="收货人" min-width="110" />
              <el-table-column label="金额" width="110" align="right">
                <template #default="{ row }"><MoneyText :value="row.paid_amount" /></template>
              </el-table-column>
            </el-table>
          </section>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, VideoPlay } from '@element-plus/icons-vue'

import { logisticsApi, type Logistics } from '@/api/logistics'
import {
  waveApi,
  type WaveRecord,
  type WaveStrategy,
  type WaveStrategyPayload,
} from '@/api/order'
import { shopApi, type Shop } from '@/api/shop'
import { warehouseApi, type Warehouse } from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  LOAD_NO_TYPE_LABELS,
  WAVE_SORT_LABELS,
  labelOf,
} from '@/views/order/orderHelpers'
import '@/views/settings/settings.css'
import '@/views/order/order.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('order:ship'))

const strategyColumns: DataTableColumn<WaveStrategy>[] = [
  { prop: 'name', label: '策略名称', minWidth: 160 },
  { label: '仓库', slot: 'warehouse', minWidth: 130 },
  { label: '物流', slot: 'logistics', minWidth: 130 },
  { label: '店铺范围', slot: 'shopCount', minWidth: 110 },
  { prop: 'wave_order_count', label: '单波次数', width: 100, align: 'right' },
  { label: '排序', slot: 'sort', width: 110 },
  { label: '状态', slot: 'enabled', width: 90, align: 'center' },
  { label: '操作', slot: 'actions', width: 110, fixed: 'right' },
]

const waveColumns: DataTableColumn<WaveRecord>[] = [
  { prop: 'wave_no', label: '波次号', minWidth: 180 },
  { prop: 'name', label: '波次名称', minWidth: 140 },
  { prop: 'total_order_count', label: '订单数', width: 90, align: 'right' },
  { label: '装载号', slot: 'loadNumbers', minWidth: 180 },
  { prop: 'created_by_name', label: '创建人', minWidth: 110 },
  { label: '创建时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 110, fixed: 'right' },
]

const activeTab = ref('strategies')
const strategies = ref<WaveStrategy[]>([])
const allStrategies = ref<WaveStrategy[]>([])
const waves = ref<WaveRecord[]>([])
const warehouses = ref<Warehouse[]>([])
const logistics = ref<Logistics[]>([])
const shops = ref<Shop[]>([])
const strategyLoading = ref(false)
const waveLoading = ref(false)
const saving = ref(false)
const running = ref(false)
const detailLoading = ref(false)
const strategyPage = ref(1)
const strategyPageSize = ref(20)
const strategyTotal = ref(0)
const wavePage = ref(1)
const wavePageSize = ref(20)
const waveTotal = ref(0)
const strategyKeyword = ref('')
const waveKeyword = ref('')
const strategyFilter = ref<number | null>(null)
const enabledFilter = ref<boolean | null>(null)
const strategyVisible = ref(false)
const runVisible = ref(false)
const detailVisible = ref(false)
const editingStrategyId = ref<number | null>(null)
const waveDetail = ref<WaveRecord | null>(null)
const runStrategyIds = ref<number[]>([])
const runOrderIds = ref('')

const strategyForm = reactive<WaveStrategyPayload>({
  name: '',
  warehouse_id: null,
  logistics_id: null,
  shop_ids: [],
  wave_order_count: 50,
  load_no_type: 'BY_EXPRESS',
  sort_type: 'LOCATION',
  is_enabled: true,
  description: '',
})

async function loadOptions(): Promise<void> {
  const results = await Promise.allSettled([
    warehouseApi.list().then((result) => {
      warehouses.value = result
    }),
    logisticsApi.list(undefined, true).then((result) => {
      logistics.value = result
    }),
    shopApi.listShops({ page: 1, page_size: 200 }).then((result) => {
      shops.value = result.items
    }),
  ])
  if (results.every((result) => result.status === 'rejected')) {
    ElMessage.error('波次基础选项加载失败')
  }
}

async function loadStrategies(): Promise<void> {
  strategyLoading.value = true
  try {
    const result = await waveApi.listStrategies({
      keyword: strategyKeyword.value || undefined,
      is_enabled: enabledFilter.value ?? undefined,
      page: strategyPage.value,
      page_size: strategyPageSize.value,
    })
    strategies.value = result.items
    strategyTotal.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '波次策略加载失败')
  } finally {
    strategyLoading.value = false
  }
}

async function loadAllStrategies(): Promise<void> {
  try {
    const result = await waveApi.listStrategies({ page: 1, page_size: 200 })
    allStrategies.value = result.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '策略选项加载失败')
  }
}

async function loadWaves(): Promise<void> {
  waveLoading.value = true
  try {
    const result = await waveApi.list({
      keyword: waveKeyword.value || undefined,
      strategy_id: strategyFilter.value || undefined,
      page: wavePage.value,
      page_size: wavePageSize.value,
    })
    waves.value = result.items
    waveTotal.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '波次记录加载失败')
  } finally {
    waveLoading.value = false
  }
}

function searchStrategies(): void {
  strategyPage.value = 1
  void loadStrategies()
}

function searchWaves(): void {
  wavePage.value = 1
  void loadWaves()
}

function handleTabChange(name: string): void {
  if (name === 'waves') {
    void loadWaves()
  }
}

function resetStrategyForm(): void {
  Object.assign(strategyForm, {
    name: '',
    warehouse_id: null,
    logistics_id: null,
    shop_ids: [],
    wave_order_count: 50,
    load_no_type: 'BY_EXPRESS',
    sort_type: 'LOCATION',
    is_enabled: true,
    description: '',
  })
}

function openCreateStrategy(): void {
  editingStrategyId.value = null
  resetStrategyForm()
  strategyVisible.value = true
}

function openEditStrategy(row: WaveStrategy): void {
  editingStrategyId.value = row.id
  Object.assign(strategyForm, {
    name: row.name,
    warehouse_id: row.warehouse_id || null,
    logistics_id: row.logistics_id || null,
    shop_ids: [...row.shop_ids],
    wave_order_count: row.wave_order_count,
    load_no_type: row.load_no_type,
    sort_type: row.sort_type,
    is_enabled: row.is_enabled,
    description: row.description || '',
  })
  strategyVisible.value = true
}

async function submitStrategy(): Promise<void> {
  if (!strategyForm.name.trim()) {
    ElMessage.warning('请填写策略名称')
    return
  }
  saving.value = true
  try {
    const payload: WaveStrategyPayload = {
      ...strategyForm,
      name: strategyForm.name.trim(),
      description: strategyForm.description?.trim() || null,
    }
    if (editingStrategyId.value) {
      await waveApi.updateStrategy(editingStrategyId.value, payload)
    } else {
      await waveApi.createStrategy(payload)
    }
    ElMessage.success(editingStrategyId.value ? '波次策略已更新' : '波次策略已创建')
    strategyVisible.value = false
    await Promise.all([loadStrategies(), loadAllStrategies()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '波次策略保存失败')
  } finally {
    saving.value = false
  }
}

async function removeStrategy(row: WaveStrategy): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除波次策略「${row.name}」吗？`, '删除策略', {
      type: 'warning',
    })
    await waveApi.removeStrategy(row.id)
    ElMessage.success('波次策略已删除')
    await Promise.all([loadStrategies(), loadAllStrategies()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '波次策略删除失败')
    }
  }
}

function openRun(): void {
  runStrategyIds.value = allStrategies.value
    .filter((item) => item.is_enabled)
    .slice(0, 1)
    .map((item) => item.id)
  runOrderIds.value = ''
  runVisible.value = true
}

async function submitRun(): Promise<void> {
  if (!runStrategyIds.value.length) {
    ElMessage.warning('请选择波次策略')
    return
  }
  const orderIds = runOrderIds.value
    .split(',')
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isInteger(item) && item > 0)
  running.value = true
  try {
    const result = await waveApi.run({
      strategy_ids: runStrategyIds.value,
      order_ids: orderIds.length ? orderIds : null,
    })
    ElMessage.success(
      `波次运行完成：${result.wave_count} 个波次，${result.order_count} 个订单`,
    )
    runVisible.value = false
    activeTab.value = 'waves'
    await Promise.all([loadWaves(), loadStrategies(), loadAllStrategies()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '波次运行失败')
  } finally {
    running.value = false
  }
}

async function openWaveDetail(row: WaveRecord): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    waveDetail.value = await waveApi.get(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '波次详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function removeWave(row: WaveRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除波次 ${row.wave_no} 吗？`, '删除波次', {
      type: 'warning',
    })
    await waveApi.remove(row.id)
    ElMessage.success('波次已删除')
    await loadWaves()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '波次删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), loadAllStrategies(), loadStrategies()])
})
</script>
