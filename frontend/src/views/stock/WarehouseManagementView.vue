<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="仓库管理"
      description="维护仓库启停、默认仓、锁定状态、发货区域与库区。"
    >
      <template #actions>
        <el-button
          v-if="canManage"
          type="primary"
          @click="openWarehouseCreate"
        >
          新建仓库
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
            placeholder="仓库编号、名称、地区或地址"
            @keyup.enter="load"
            @clear="load"
          />
          <el-select
            v-model="typeFilter"
            class="stock-filter"
            clearable
            placeholder="全部仓库类型"
            @change="load"
          >
            <el-option label="成品仓" value="FINISHED" />
            <el-option label="次品仓" value="DEFECTIVE" />
          </el-select>
          <el-select
            v-model="enabledFilter"
            class="stock-filter"
            clearable
            placeholder="全部状态"
            @change="load"
          >
            <el-option label="已启用" :value="true" />
            <el-option label="已停用" :value="false" />
          </el-select>
          <el-button @click="load">查询</el-button>
        </div>
      </div>

      <DataTable
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="rows.length"
        :sync-query="false"
        empty-description="暂无仓库"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @empty-action="resetFilters"
      >
        <template #type="{ row }">
          <StatusTag
            :status="row.warehouse_type"
            :label="labelOf(WAREHOUSE_TYPE_LABELS, row.warehouse_type)"
            :tone="row.warehouse_type === 'DEFECTIVE' ? 'warning' : 'primary'"
          />
        </template>
        <template #default="{ row }">
          <StatusTag
            v-if="row.is_default"
            status="DEFAULT"
            label="默认仓"
            tone="success"
          />
          <span v-else class="stock-muted">--</span>
        </template>
        <template #lock="{ row }">
          <StatusTag
            :status="row.lock_type"
            :label="labelOf(LOCK_TYPE_LABELS, row.lock_type)"
            :tone="stockStatusTone(row.lock_type)"
          />
        </template>
        <template #enabled="{ row }">
          <StatusTag
            :status="row.is_enabled ? 'ENABLED' : 'DISABLED'"
            :label="row.is_enabled ? '已启用' : '已停用'"
            :tone="row.is_enabled ? 'success' : 'info'"
          />
        </template>
        <template #actions="{ row }">
          <div class="settings-page__actions">
            <el-button link type="primary" @click="openZoneDialog(row)">
              库区
            </el-button>
            <el-button
              v-if="canManage"
              link
              type="primary"
              @click="openWarehouseEdit(row)"
            >
              编辑
            </el-button>
            <el-dropdown v-if="canManage" trigger="click">
              <el-button link type="primary">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-if="!row.is_default && row.is_enabled"
                    @click="runWarehouseAction(row, 'default')"
                  >
                    设为默认仓
                  </el-dropdown-item>
                  <el-dropdown-item
                    v-if="row.lock_type === 'NONE'"
                    @click="runWarehouseAction(row, 'lock')"
                  >
                    锁定仓库
                  </el-dropdown-item>
                  <el-dropdown-item v-else @click="runWarehouseAction(row, 'unlock')">
                    解锁仓库
                  </el-dropdown-item>
                  <el-dropdown-item
                    v-if="row.is_enabled"
                    @click="runWarehouseAction(row, 'disable')"
                  >
                    停用仓库
                  </el-dropdown-item>
                  <el-dropdown-item
                    v-else
                    @click="runWarehouseAction(row, 'enable')"
                  >
                    启用仓库
                  </el-dropdown-item>
                  <el-dropdown-item divided @click="removeWarehouse(row)">
                    删除仓库
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="warehouseVisible"
      :title="warehouseForm.id ? '编辑仓库' : '新建仓库'"
      :loading="saving"
      width="720px"
      @submit="submitWarehouse"
    >
      <div class="stock-form-grid">
        <el-form-item label="仓库编号" required>
          <el-input
            v-model="warehouseForm.code"
            maxlength="32"
            placeholder="如 WH-01"
          />
        </el-form-item>
        <el-form-item label="仓库名称" required>
          <el-input v-model="warehouseForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="仓库类型">
          <el-select v-model="warehouseForm.warehouse_type">
            <el-option label="成品仓" value="FINISHED" />
            <el-option label="次品仓" value="DEFECTIVE" />
          </el-select>
        </el-form-item>
        <el-form-item label="锁定状态">
          <el-select v-model="warehouseForm.lock_type">
            <el-option label="未锁定" value="NONE" />
            <el-option label="手动锁定" value="MANUAL" />
            <el-option label="盘点锁定" value="CHECK" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属地区">
          <el-input
            v-model="warehouseForm.region"
            maxlength="64"
            placeholder="省 / 市"
          />
        </el-form-item>
        <el-form-item label="可发货区域">
          <el-input
            v-model="warehouseForm.ship_regions"
            maxlength="128"
            placeholder="多个区域用逗号分隔"
          />
        </el-form-item>
        <el-form-item class="is-full-width" label="详细地址">
          <el-input v-model="warehouseForm.address" maxlength="255" />
        </el-form-item>
        <el-form-item label="默认仓库">
          <el-switch v-model="warehouseForm.is_default" />
        </el-form-item>
        <el-form-item label="参与同步库存">
          <el-switch
            v-model="warehouseForm.sync_stock"
            :disabled="
              warehouseForm.is_virtual ||
              warehouseForm.warehouse_type === 'DEFECTIVE'
            "
          />
        </el-form-item>
        <el-form-item label="开启库位">
          <el-switch v-model="warehouseForm.enable_location" />
        </el-form-item>
        <el-form-item label="零库存出库">
          <el-switch v-model="warehouseForm.zero_stock_out" />
        </el-form-item>
        <el-form-item label="虚拟仓">
          <el-switch v-model="warehouseForm.is_virtual" />
        </el-form-item>
        <el-form-item label="启用状态">
          <el-switch v-model="warehouseForm.is_enabled" />
        </el-form-item>
        <el-form-item class="is-full-width" label="备注">
          <el-input
            v-model="warehouseForm.remark"
            type="textarea"
            :rows="2"
            maxlength="255"
            show-word-limit
          />
        </el-form-item>
      </div>
    </FormDialog>

    <el-drawer
      v-model="zoneVisible"
      :title="`库区管理 · ${activeWarehouse?.name || ''}`"
      size="min(680px, 94vw)"
      destroy-on-close
    >
      <div class="stock-toolbar">
        <span class="stock-muted">
          仅开启库位的仓库可维护库区。
        </span>
        <el-button v-if="canManage" type="primary" @click="openZoneCreate">
          新建库区
        </el-button>
      </div>
      <el-table v-loading="zoneLoading" :data="zones" border>
        <el-table-column prop="code" label="库区编号" min-width="130" />
        <el-table-column prop="name" label="库区名称" min-width="140" />
        <el-table-column prop="sort_order" label="排序" width="90" />
        <el-table-column
          v-if="canManage"
          label="操作"
          width="130"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button link type="primary" @click="openZoneEdit(row)">
              编辑
            </el-button>
            <el-button link type="danger" @click="removeZone(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <FormDialog
      v-model:visible="zoneFormVisible"
      :title="zoneForm.id ? '编辑库区' : '新建库区'"
      :loading="saving"
      width="520px"
      @submit="submitZone"
    >
      <el-form-item label="库区编号" required>
        <el-input v-model="zoneForm.code" maxlength="32" />
      </el-form-item>
      <el-form-item label="库区名称">
        <el-input v-model="zoneForm.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="排序">
        <el-input-number v-model="zoneForm.sort_order" :min="0" />
      </el-form-item>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  warehouseApi,
  type Warehouse,
  type WarehousePayload,
  type Zone,
  type ZonePayload,
} from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import {
  LOCK_TYPE_LABELS,
  WAREHOUSE_TYPE_LABELS,
  labelOf,
  stockStatusTone,
} from '@/views/stock/stockHelpers'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<Warehouse>[] = [
  { prop: 'code', label: '仓库编号', minWidth: 130 },
  { prop: 'name', label: '仓库名称', minWidth: 160 },
  { label: '类型', slot: 'type', width: 100, align: 'center' },
  { label: '默认', slot: 'default', width: 90, align: 'center' },
  { label: '锁定状态', slot: 'lock', width: 110, align: 'center' },
  { prop: 'region', label: '地区', minWidth: 120 },
  { prop: 'address', label: '详细地址', minWidth: 180 },
  { label: '状态', slot: 'enabled', width: 95, align: 'center' },
  { label: '操作', slot: 'actions', width: 210, fixed: 'right' },
]

const rows = ref<Warehouse[]>([])
const zones = ref<Zone[]>([])
const loading = ref(false)
const zoneLoading = ref(false)
const saving = ref(false)
const keyword = ref('')
const typeFilter = ref('')
const enabledFilter = ref<boolean | null>(null)
const warehouseVisible = ref(false)
const zoneVisible = ref(false)
const zoneFormVisible = ref(false)
const activeWarehouse = ref<Warehouse | null>(null)

const warehouseForm = reactive<
  WarehousePayload & { id?: number }
>({
  code: '',
  name: '',
  region: '',
  address: '',
  warehouse_type: 'FINISHED',
  is_default: false,
  sync_stock: false,
  enable_location: false,
  zero_stock_out: false,
  is_virtual: false,
  ship_regions: '',
  is_enabled: true,
  lock_type: 'NONE',
  remark: '',
})
const zoneForm = reactive<ZonePayload & { id?: number }>({
  warehouse_id: 0,
  code: '',
  name: '',
  sort_order: 0,
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    Boolean(typeFilter.value) ||
    enabledFilter.value !== null,
)

function resetWarehouseForm(): void {
  Object.assign(warehouseForm, {
    id: undefined,
    code: '',
    name: '',
    region: '',
    address: '',
    warehouse_type: 'FINISHED',
    is_default: false,
    sync_stock: false,
    enable_location: false,
    zero_stock_out: false,
    is_virtual: false,
    ship_regions: '',
    is_enabled: true,
    lock_type: 'NONE',
    remark: '',
  })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await warehouseApi.list({
      keyword: keyword.value || undefined,
      warehouse_type: typeFilter.value || undefined,
      is_enabled: enabledFilter.value ?? undefined,
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '仓库加载失败')
  } finally {
    loading.value = false
  }
}

function resetFilters(): void {
  keyword.value = ''
  typeFilter.value = ''
  enabledFilter.value = null
  void load()
}

function openWarehouseCreate(): void {
  resetWarehouseForm()
  warehouseVisible.value = true
}

function openWarehouseEdit(row: Warehouse): void {
  Object.assign(warehouseForm, {
    id: row.id,
    code: row.code,
    name: row.name,
    region: row.region || '',
    address: row.address || '',
    warehouse_type: row.warehouse_type,
    is_default: row.is_default,
    sync_stock: row.sync_stock,
    enable_location: row.enable_location,
    zero_stock_out: row.zero_stock_out,
    is_virtual: row.is_virtual,
    ship_regions: row.ship_regions || '',
    is_enabled: row.is_enabled,
    lock_type: row.lock_type,
    remark: row.remark || '',
  })
  warehouseVisible.value = true
}

async function submitWarehouse(): Promise<void> {
  if (!warehouseForm.code.trim() || !warehouseForm.name.trim()) {
    ElMessage.warning('请填写仓库编号和名称')
    return
  }
  saving.value = true
  try {
    const { id, ...payload } = warehouseForm
    if (id) {
      await warehouseApi.update(id, payload)
    } else {
      await warehouseApi.create(payload)
    }
    ElMessage.success(id ? '仓库已更新' : '仓库已创建')
    warehouseVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '仓库保存失败')
  } finally {
    saving.value = false
  }
}

async function runWarehouseAction(
  row: Warehouse,
  action: 'default' | 'lock' | 'unlock' | 'enable' | 'disable',
): Promise<void> {
  try {
    const actionMap = {
      default: warehouseApi.setDefault,
      lock: warehouseApi.lock,
      unlock: warehouseApi.unlock,
      enable: warehouseApi.enable,
      disable: warehouseApi.disable,
    }
    await actionMap[action](row.id)
    ElMessage.success('仓库状态已更新')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '仓库操作失败')
  }
}

async function removeWarehouse(row: Warehouse): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除仓库 ${row.code} · ${row.name} 吗？`,
      '删除仓库',
      { type: 'warning' },
    )
    await warehouseApi.remove(row.id)
    ElMessage.success('仓库已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '仓库删除失败')
    }
  }
}

async function openZoneDialog(row: Warehouse): Promise<void> {
  activeWarehouse.value = row
  zoneVisible.value = true
  await loadZones()
}

async function loadZones(): Promise<void> {
  if (!activeWarehouse.value) {
    return
  }
  zoneLoading.value = true
  try {
    zones.value = await warehouseApi.listZones(activeWarehouse.value.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库区加载失败')
  } finally {
    zoneLoading.value = false
  }
}

function openZoneCreate(): void {
  if (!activeWarehouse.value) {
    return
  }
  Object.assign(zoneForm, {
    id: undefined,
    warehouse_id: activeWarehouse.value.id,
    code: '',
    name: '',
    sort_order: 0,
  })
  zoneFormVisible.value = true
}

function openZoneEdit(row: Zone): void {
  Object.assign(zoneForm, {
    id: row.id,
    warehouse_id: row.warehouse_id,
    code: row.code,
    name: row.name || '',
    sort_order: row.sort_order,
  })
  zoneFormVisible.value = true
}

async function submitZone(): Promise<void> {
  if (!zoneForm.code.trim()) {
    ElMessage.warning('请填写库区编号')
    return
  }
  saving.value = true
  try {
    const { id, ...payload } = zoneForm
    if (id) {
      await warehouseApi.updateZone(id, payload)
    } else {
      await warehouseApi.createZone(payload)
    }
    ElMessage.success(id ? '库区已更新' : '库区已创建')
    zoneFormVisible.value = false
    await loadZones()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库区保存失败')
  } finally {
    saving.value = false
  }
}

async function removeZone(row: Zone): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除库区 ${row.code} 吗？`, '删除库区', {
      type: 'warning',
    })
    await warehouseApi.removeZone(row.id)
    ElMessage.success('库区已删除')
    await loadZones()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '库区删除失败')
    }
  }
}

onMounted(load)
</script>
