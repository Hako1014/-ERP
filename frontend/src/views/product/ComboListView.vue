<template>
  <div class="settings-page product-page">
    <SettingsPageHeader
      title="组合商品列表"
      description="维护主商品与配件清单，销售时按组成项逐项扣减库存和核算成本。"
    />

    <section class="settings-page__panel">
      <div class="settings-page__toolbar">
        <div class="settings-page__toolbar-actions">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="组合编码或名称"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="enabledFilter"
            class="product-filter"
            placeholder="全部状态"
            @change="search"
          >
            <el-option label="全部状态" value="" />
            <el-option label="启用" value="true" />
            <el-option label="停用" value="false" />
          </el-select>
          <el-button @click="search">查询</el-button>
        </div>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新增组合商品
        </el-button>
      </div>

      <DataTable
        v-model:page="page"
        v-model:page-size="pageSize"
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="total"
        :sync-query="false"
        empty-description="暂无组合商品"
        empty-action-text="重置筛选"
        :show-empty-action="Boolean(keyword || enabledFilter)"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #main="{ row }">
          <div class="product-main-cell">
            <strong>{{ row.main_product_name || `商品 #${row.main_product_id}` }}</strong>
            <span>{{ row.main_sku_spec_code || '商品级' }}</span>
          </div>
        </template>
        <template #enabled="{ row }">
          <StatusTag
            :status="row.is_enabled ? 'ACTIVE' : 'DISABLED'"
            :label="row.is_enabled ? '启用' : '停用'"
          />
        </template>
        <template #actions="{ row }">
          <div class="settings-page__actions settings-page__actions--wrap">
            <el-button link type="primary" @click="openDetail(row)">
              明细
            </el-button>
            <template v-if="canManage">
              <el-button link type="primary" @click="openEdit(row)">
                编辑
              </el-button>
              <el-button
                link
                :type="row.is_enabled ? 'warning' : 'success'"
                @click="toggleEnabled(row)"
              >
                {{ row.is_enabled ? '停用' : '启用' }}
              </el-button>
              <el-button link type="danger" @click="remove(row)">
                删除
              </el-button>
            </template>
            <span v-else class="settings-page__muted">只读</span>
          </div>
        </template>
      </DataTable>
    </section>

    <ComboFormDialog
      ref="comboFormRef"
      v-model:visible="formVisible"
      :saving="saving"
      @submit="submitCombo"
    />

    <el-drawer
      v-model="detailVisible"
      :title="detail?.name || '组合商品明细'"
      size="min(820px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading" class="product-detail">
        <el-descriptions v-if="detail" :column="2" border>
          <el-descriptions-item label="组合编码">
            {{ detail.combo_no }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <StatusTag
              :status="detail.is_enabled ? 'ACTIVE' : 'DISABLED'"
              :label="detail.is_enabled ? '启用' : '停用'"
            />
          </el-descriptions-item>
          <el-descriptions-item label="主商品">
            {{ detail.main_product_name || `商品 #${detail.main_product_id}` }}
          </el-descriptions-item>
          <el-descriptions-item label="主商品规格">
            {{ detail.main_sku_spec_code || '商品级' }}
          </el-descriptions-item>
          <el-descriptions-item class="is-full-width" label="备注">
            {{ detail.remark || '--' }}
          </el-descriptions-item>
        </el-descriptions>

        <el-table :data="detail?.items || []" row-key="id">
          <el-table-column prop="product_name" label="配件商品" min-width="170" />
          <el-table-column label="配件规格" min-width="130">
            <template #default="{ row }">
              {{ row.sku_spec_code || '商品级' }}
            </template>
          </el-table-column>
          <el-table-column prop="qty" label="数量" width="100" align="right" />
          <el-table-column
            prop="sort_order"
            label="排序"
            width="80"
            align="right"
          />
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import {
  productApi,
  type ComboPayload,
  type ComboRecord,
} from '@/api/product'
import DataTable from '@/components/DataTable.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import ComboFormDialog from '@/views/product/components/ComboFormDialog.vue'
import { useAuthStore } from '@/stores/auth'
import '@/views/settings/settings.css'
import '@/views/product/product.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('product:manage'))
const columns: DataTableColumn<ComboRecord>[] = [
  { prop: 'combo_no', label: '组合编码', minWidth: 130 },
  { prop: 'name', label: '组合名称', minWidth: 180 },
  { label: '主商品 / 规格', slot: 'main', minWidth: 220 },
  { label: '状态', slot: 'enabled', width: 90, align: 'center' },
  { prop: 'remark', label: '备注', minWidth: 180 },
  { label: '操作', slot: 'actions', width: 230, fixed: 'right' },
]

const rows = ref<ComboRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const enabledFilter = ref('')
const formVisible = ref(false)
const editingCombo = ref<ComboRecord | null>(null)
const detailVisible = ref(false)
const detail = ref<ComboRecord | null>(null)
const comboFormRef = ref<InstanceType<typeof ComboFormDialog>>()

function enabledParam(): boolean | undefined {
  if (enabledFilter.value === 'true') {
    return true
  }
  if (enabledFilter.value === 'false') {
    return false
  }
  return undefined
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await productApi.listCombos({
      keyword: keyword.value || undefined,
      is_enabled: enabledParam(),
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '组合商品加载失败')
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
  enabledFilter.value = ''
  search()
}

function openCreate(): void {
  editingCombo.value = null
  comboFormRef.value?.open()
}

function openEdit(row: ComboRecord): void {
  editingCombo.value = row
  comboFormRef.value?.open(row)
}

async function submitCombo(payload: ComboPayload): Promise<void> {
  saving.value = true
  try {
    if (editingCombo.value) {
      await productApi.updateCombo(editingCombo.value.id, payload)
      ElMessage.success('组合商品已更新')
    } else {
      await productApi.createCombo(payload)
      ElMessage.success('组合商品已创建')
    }
    formVisible.value = false
    editingCombo.value = null
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '组合商品保存失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(row: ComboRecord): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await productApi.getCombo(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '组合明细加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function toggleEnabled(row: ComboRecord): Promise<void> {
  const action = row.is_enabled ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(
      `确认${action}组合商品「${row.name}」？`,
      `${action}组合商品`,
      { type: row.is_enabled ? 'warning' : 'info' },
    )
    await productApi.setComboEnabled(row.id, !row.is_enabled)
    ElMessage.success(`组合商品已${action}`)
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '操作失败')
    }
  }
}

async function remove(row: ComboRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除组合商品「${row.name}」？`,
      '删除组合商品',
      { type: 'warning' },
    )
    await productApi.deleteCombo(row.id)
    ElMessage.success('组合商品已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

onMounted(load)
</script>

<style scoped>
.product-main-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.product-main-cell span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
