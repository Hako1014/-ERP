<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>计量单位组</h2>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增单位组
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="单位组名称" min-width="180" />
      <el-table-column label="主计量单位" min-width="140">
        <template #default="{ row }">
          {{ unitName(row.main_unit_id) }}
        </template>
      </el-table-column>
      <el-table-column label="单位明细" min-width="260">
        <template #default="{ row }">
          {{ unitGroupSummary(row) }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="canManage"
        label="操作"
        width="140"
        fixed="right"
      >
        <template #default="{ row }">
          <div class="settings-page__actions">
            <el-button link type="primary" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button link type="danger" @click="remove(row)">
              删除
            </el-button>
          </div>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState
          description="暂无计量单位组"
          action-text="新增单位组"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑计量单位组' : '新增计量单位组'"
      :width="760"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="单位组名称" required>
        <el-input v-model="form.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="主计量单位" required>
        <el-select v-model="form.main_unit_id" filterable>
          <el-option
            v-for="item in unitOptions"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <div class="unit-group-items">
        <div
          v-for="(item, index) in form.items"
          :key="item.key"
          class="unit-group-item"
        >
          <el-select v-model="item.unit_id" filterable placeholder="计量单位">
            <el-option
              v-for="unit in unitOptions"
              :key="unit.id"
              :label="unit.name"
              :value="unit.id"
            />
          </el-select>
          <el-input
            v-model="item.conversion_rate"
            inputmode="decimal"
            placeholder="换算率"
          />
          <el-button link type="danger" @click="removeItem(index)">
            删除
          </el-button>
        </div>
        <el-button @click="appendItem">添加单位</el-button>
      </div>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  productSettingsApi,
  type ProductUnit,
  type ProductUnitGroup,
} from '@/api/productSettings'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

interface UnitGroupItemForm {
  key: number
  unit_id: number | null
  conversion_rate: string
}

defineProps<{
  canManage: boolean
}>()

const rows = ref<ProductUnitGroup[]>([])
const unitOptions = ref<ProductUnit[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<ProductUnitGroup | null>(null)
let itemKey = 0

function emptyItem(): UnitGroupItemForm {
  itemKey += 1
  return {
    key: itemKey,
    unit_id: null,
    conversion_rate: '1',
  }
}

const form = reactive({
  name: '',
  main_unit_id: null as number | null,
  items: [] as UnitGroupItemForm[],
})

function unitName(unitId: number): string {
  return unitOptions.value.find((item) => item.id === unitId)?.name || `#${unitId}`
}

function unitGroupSummary(group: ProductUnitGroup): string {
  if (!group.items.length) {
    return '--'
  }
  return group.items
    .map((item) => `${unitName(item.unit_id)} × ${item.conversion_rate}`)
    .join('，')
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [groups, units] = await Promise.all([
      productSettingsApi.listUnitGroups(),
      productSettingsApi.listUnits(),
    ])
    rows.value = groups
    unitOptions.value = units
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '计量单位组加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.main_unit_id = null
  form.items = [emptyItem()]
  dialogVisible.value = true
}

function openEdit(row: ProductUnitGroup): void {
  editing.value = row
  form.name = row.name
  form.main_unit_id = row.main_unit_id
  form.items = row.items.map((item) => {
    itemKey += 1
    return {
      key: itemKey,
      unit_id: item.unit_id,
      conversion_rate: String(item.conversion_rate),
    }
  })
  dialogVisible.value = true
}

function appendItem(): void {
  form.items.push(emptyItem())
}

function removeItem(index: number): void {
  form.items.splice(index, 1)
}

async function submit(): Promise<void> {
  if (!form.name.trim() || !form.main_unit_id) {
    ElMessage.warning('请填写单位组名称并选择主计量单位')
    return
  }
  const items = form.items.map((item) => ({
    unit_id: Number(item.unit_id),
    conversion_rate: item.conversion_rate,
  }))
  if (
    items.some(
      (item) =>
        !item.unit_id ||
        !item.conversion_rate ||
        Number(item.conversion_rate) <= 0,
    )
  ) {
    ElMessage.warning('请完整填写单位明细和大于 0 的换算率')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      main_unit_id: form.main_unit_id,
      items,
    }
    if (editing.value) {
      await productSettingsApi.updateUnitGroup(editing.value.id, payload)
      ElMessage.success('计量单位组已更新')
    } else {
      await productSettingsApi.createUnitGroup(payload)
      ElMessage.success('计量单位组已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '计量单位组保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: ProductUnitGroup): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除计量单位组「${row.name}」？`,
      '删除计量单位组',
      { type: 'warning' },
    )
    await productSettingsApi.deleteUnitGroup(row.id)
    ElMessage.success('计量单位组已删除')
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
.unit-group-items {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.unit-group-item {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(140px, 1fr) 54px;
  gap: 8px;
  align-items: center;
}
</style>
