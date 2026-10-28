<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库位管理"
      description="按仓库与库区维护库位、容量、优先级及启停状态。"
    >
      <template #actions>
        <el-button
          v-if="canManage"
          :disabled="!warehouseId"
          @click="openGenerate"
        >
          批量生成库位
        </el-button>
        <el-button
          v-if="canManage"
          type="primary"
          :disabled="!warehouseId"
          @click="openCreate"
        >
          新建库位
        </el-button>
      </template>
    </SettingsPageHeader>

    <section class="settings-page__panel">
      <div class="stock-toolbar">
        <div class="stock-toolbar__filters">
          <el-select
            v-model="warehouseId"
            class="stock-filter--wide"
            filterable
            placeholder="选择仓库"
            @change="handleWarehouseChange"
          >
            <el-option
              v-for="item in warehouses"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="zoneId"
            class="stock-filter"
            clearable
            placeholder="全部库区"
            @change="load"
          >
            <el-option
              v-for="item in zones"
              :key="item.id"
              :label="`${item.code}${item.name ? ` · ${item.name}` : ''}`"
              :value="item.id"
            />
          </el-select>
          <el-input
            v-model="keyword"
            class="stock-filter--wide"
            clearable
            placeholder="库位编号"
            @keyup.enter="load"
            @clear="load"
          />
          <el-button @click="load">查询</el-button>
        </div>
      </div>

      <DataTable
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="rows.length"
        :sync-query="false"
        empty-description="暂无库位，请先创建或批量生成"
        empty-action-text="清空筛选"
        :show-empty-action="Boolean(zoneId || keyword)"
        @empty-action="resetFilters"
      >
        <template #zone="{ row }">
          {{ row.zone_code || '未分区' }}
        </template>
        <template #position="{ row }">
          {{ row.row_no }} 行 / {{ row.col_no }} 列 / {{ row.layer_no }} 层
        </template>
        <template #capacity="{ row }">
          <span v-if="row.capacity === 0">不限</span>
          <span v-else>{{ row.used_qty }} / {{ row.capacity }}</span>
        </template>
        <template #remaining="{ row }">
          {{ row.capacity === 0 ? '不限' : row.remaining_qty }}
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
            <el-button
              v-if="canManage"
              link
              type="primary"
              @click="openEdit(row)"
            >
              编辑
            </el-button>
            <el-button
              v-if="canManage"
              link
              :type="row.is_enabled ? 'warning' : 'success'"
              @click="toggleLocation(row)"
            >
              {{ row.is_enabled ? '停用' : '启用' }}
            </el-button>
            <el-button
              v-if="canManage"
              link
              type="danger"
              @click="removeLocation(row)"
            >
              删除
            </el-button>
          </div>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="formVisible"
      :title="form.id ? '编辑库位' : '新建库位'"
      :loading="saving"
      width="640px"
      @submit="submitForm"
    >
      <div class="stock-form-grid">
        <el-form-item label="所属仓库" required>
          <el-select v-model="form.warehouse_id" disabled>
            <el-option
              v-for="item in warehouses"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="所属库区">
          <el-select v-model="form.zone_id" clearable placeholder="未分区">
            <el-option
              v-for="item in zones"
              :key="item.id"
              :label="`${item.code}${item.name ? ` · ${item.name}` : ''}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item class="is-full-width" label="库位编号">
          <el-input
            v-model="form.code"
            maxlength="64"
            placeholder="留空时由后端按行列层自动生成"
          />
        </el-form-item>
        <el-form-item label="行号">
          <el-input-number v-model="form.row_no" :min="1" />
        </el-form-item>
        <el-form-item label="列号">
          <el-input-number v-model="form.col_no" :min="1" />
        </el-form-item>
        <el-form-item label="层号">
          <el-input-number v-model="form.layer_no" :min="1" />
        </el-form-item>
        <el-form-item label="容量">
          <el-input-number v-model="form.capacity" :min="0" />
          <div class="stock-muted">0 表示不限容量</div>
        </el-form-item>
        <el-form-item label="拣货优先级">
          <el-input-number v-model="form.priority" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" />
        </el-form-item>
        <el-form-item label="虚拟库位">
          <el-switch v-model="form.is_virtual" />
        </el-form-item>
        <el-form-item label="启用状态">
          <el-switch v-model="form.is_enabled" />
        </el-form-item>
      </div>
    </FormDialog>

    <FormDialog
      v-model:visible="generateVisible"
      title="批量生成库位"
      :loading="saving"
      width="580px"
      @submit="submitGenerate"
    >
      <p class="stock-muted">
        按库区、行数、列数、层数生成笛卡尔组合，已存在的库位会自动跳过。
      </p>
      <el-form-item label="所属仓库" required>
        <el-select v-model="generateForm.warehouse_id" disabled>
          <el-option
            v-for="item in warehouses"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="所属库区" required>
        <el-select v-model="generateForm.zone_id">
          <el-option
            v-for="item in zones"
            :key="item.id"
            :label="`${item.code}${item.name ? ` · ${item.name}` : ''}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <div class="stock-form-grid">
        <el-form-item label="行数">
          <el-input-number v-model="generateForm.row_count" :min="1" />
        </el-form-item>
        <el-form-item label="列数">
          <el-input-number v-model="generateForm.col_count" :min="1" />
        </el-form-item>
        <el-form-item label="层数">
          <el-input-number v-model="generateForm.layer_count" :min="1" />
        </el-form-item>
        <el-form-item label="容量">
          <el-input-number v-model="generateForm.capacity" :min="0" />
        </el-form-item>
        <el-form-item label="拣货优先级">
          <el-input-number v-model="generateForm.priority" />
        </el-form-item>
        <el-form-item label="启用状态">
          <el-switch v-model="generateForm.is_enabled" />
        </el-form-item>
      </div>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  warehouseApi,
  type Location,
  type LocationGeneratePayload,
  type LocationPayload,
  type Warehouse,
  type Zone,
} from '@/api/stock'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))

const columns: DataTableColumn<Location>[] = [
  { prop: 'code', label: '库位编号', minWidth: 200 },
  { prop: 'warehouse_name', label: '仓库', minWidth: 120 },
  { label: '库区', slot: 'zone', width: 110 },
  { label: '行列层', slot: 'position', minWidth: 150 },
  { label: '已用 / 容量', slot: 'capacity', width: 130, align: 'right' },
  { label: '剩余', slot: 'remaining', width: 95, align: 'right' },
  { prop: 'priority', label: '优先级', width: 85, align: 'right' },
  { label: '状态', slot: 'enabled', width: 95, align: 'center' },
  { label: '操作', slot: 'actions', width: 170, fixed: 'right' },
]

const warehouses = ref<Warehouse[]>([])
const zones = ref<Zone[]>([])
const rows = ref<Location[]>([])
const warehouseId = ref<number | null>(null)
const zoneId = ref<number | null>(null)
const keyword = ref('')
const loading = ref(false)
const saving = ref(false)
const formVisible = ref(false)
const generateVisible = ref(false)

const form = reactive<LocationPayload & { id?: number }>({
  warehouse_id: 0,
  zone_id: null,
  code: '',
  row_no: 1,
  col_no: 1,
  layer_no: 1,
  capacity: 0,
  priority: 0,
  sort_order: 0,
  is_virtual: false,
  is_enabled: true,
})
const generateForm = reactive<LocationGeneratePayload>({
  warehouse_id: 0,
  zone_id: 0,
  row_count: 1,
  col_count: 1,
  layer_count: 1,
  capacity: 0,
  priority: 0,
  sort_order: 0,
  is_virtual: false,
  is_enabled: true,
})

async function loadOptions(): Promise<void> {
  try {
    warehouses.value = await warehouseApi.list({
      selectable_only: false,
      is_enabled: true,
    })
    const preferred =
      warehouses.value.find((item) => item.is_default) || warehouses.value[0]
    if (preferred) {
      warehouseId.value = preferred.id
      await loadZones()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '仓库选项加载失败')
  }
}

async function loadZones(): Promise<void> {
  if (!warehouseId.value) {
    zones.value = []
    return
  }
  try {
    zones.value = await warehouseApi.listZones(warehouseId.value)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库区加载失败')
  }
}

async function load(): Promise<void> {
  if (!warehouseId.value) {
    rows.value = []
    return
  }
  loading.value = true
  try {
    rows.value = await warehouseApi.listLocations({
      warehouse_id: warehouseId.value,
      zone_id: zoneId.value || undefined,
      keyword: keyword.value || undefined,
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库位加载失败')
  } finally {
    loading.value = false
  }
}

async function handleWarehouseChange(): Promise<void> {
  zoneId.value = null
  await loadZones()
  await load()
}

function resetFilters(): void {
  zoneId.value = null
  keyword.value = ''
  void load()
}

function openCreate(): void {
  Object.assign(form, {
    id: undefined,
    warehouse_id: warehouseId.value || 0,
    zone_id: zoneId.value || null,
    code: '',
    row_no: 1,
    col_no: 1,
    layer_no: 1,
    capacity: 0,
    priority: 0,
    sort_order: 0,
    is_virtual: false,
    is_enabled: true,
  })
  formVisible.value = true
}

function openEdit(row: Location): void {
  Object.assign(form, {
    id: row.id,
    warehouse_id: row.warehouse_id,
    zone_id: row.zone_id || null,
    code: row.code,
    row_no: row.row_no,
    col_no: row.col_no,
    layer_no: row.layer_no,
    capacity: row.capacity,
    priority: row.priority,
    sort_order: row.sort_order,
    is_virtual: row.is_virtual,
    is_enabled: row.is_enabled,
  })
  formVisible.value = true
}

async function submitForm(): Promise<void> {
  saving.value = true
  try {
    const { id, ...payload } = form
    if (id) {
      const { warehouse_id: _warehouseId, ...updatePayload } = payload
      await warehouseApi.updateLocation(id, updatePayload)
    } else {
      await warehouseApi.createLocation(payload)
    }
    ElMessage.success(id ? '库位已更新' : '库位已创建')
    formVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库位保存失败')
  } finally {
    saving.value = false
  }
}

function openGenerate(): void {
  Object.assign(generateForm, {
    warehouse_id: warehouseId.value || 0,
    zone_id: zoneId.value || zones.value[0]?.id || 0,
    row_count: 1,
    col_count: 1,
    layer_count: 1,
    capacity: 0,
    priority: 0,
    sort_order: 0,
    is_virtual: false,
    is_enabled: true,
  })
  generateVisible.value = true
}

async function submitGenerate(): Promise<void> {
  if (!generateForm.zone_id) {
    ElMessage.warning('请选择库区；未开启库位时需先维护库区')
    return
  }
  saving.value = true
  try {
    const result = await warehouseApi.generateLocations(generateForm)
    ElMessage.success(`已生成 ${result.generated_count} 个库位`)
    generateVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库位生成失败')
  } finally {
    saving.value = false
  }
}

async function toggleLocation(row: Location): Promise<void> {
  try {
    if (row.is_enabled) {
      await warehouseApi.disableLocation(row.id)
    } else {
      await warehouseApi.enableLocation(row.id)
    }
    ElMessage.success(row.is_enabled ? '库位已停用' : '库位已启用')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库位状态更新失败')
  }
}

async function removeLocation(row: Location): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除库位 ${row.code} 吗？`, '删除库位', {
      type: 'warning',
    })
    await warehouseApi.removeLocation(row.id)
    ElMessage.success('库位已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '库位删除失败')
    }
  }
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>
