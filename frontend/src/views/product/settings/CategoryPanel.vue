<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>商品分类</h2>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增分类
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="类别名称" min-width="180" />
      <el-table-column label="所属类别" min-width="180">
        <template #default="{ row }">
          {{ parentName(row.parent_id) }}
        </template>
      </el-table-column>
      <el-table-column
        prop="sort_order"
        label="排序"
        width="90"
        align="right"
      />
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
          description="暂无商品分类"
          action-text="新增分类"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑商品分类' : '新增商品分类'"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="类别名称" required>
        <el-input v-model="form.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="所属类别">
        <el-select v-model="form.parent_id" clearable filterable>
          <el-option
            v-for="item in parentOptions"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="排序">
        <el-input-number v-model="form.sort_order" :min="0" />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import {
  productSettingsApi,
  type ProductCategory,
} from '@/api/productSettings'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

const props = defineProps<{
  canManage: boolean
}>()

const rows = ref<ProductCategory[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<ProductCategory | null>(null)
const form = reactive({
  name: '',
  parent_id: null as number | null,
  sort_order: 0,
})

const parentOptions = computed(() => {
  if (!editing.value) {
    return rows.value
  }
  const blocked = new Set<number>([editing.value.id])
  let changed = true
  while (changed) {
    changed = false
    rows.value.forEach((item) => {
      if (item.parent_id && blocked.has(item.parent_id) && !blocked.has(item.id)) {
        blocked.add(item.id)
        changed = true
      }
    })
  }
  return rows.value.filter((item) => !blocked.has(item.id))
})

function parentName(parentId?: number | null): string {
  if (!parentId) {
    return '一级分类'
  }
  return rows.value.find((item) => item.id === parentId)?.name || `#${parentId}`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await productSettingsApi.listCategories()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '分类加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.parent_id = null
  form.sort_order = 0
  dialogVisible.value = true
}

function openEdit(row: ProductCategory): void {
  editing.value = row
  form.name = row.name
  form.parent_id = row.parent_id || null
  form.sort_order = row.sort_order
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写类别名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      parent_id: form.parent_id,
      sort_order: form.sort_order,
    }
    if (editing.value) {
      await productSettingsApi.updateCategory(editing.value.id, payload)
      ElMessage.success('分类已更新')
    } else {
      await productSettingsApi.createCategory(payload)
      ElMessage.success('分类已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '分类保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: ProductCategory): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除分类「${row.name}」？`, '删除分类', {
      type: 'warning',
    })
    await productSettingsApi.deleteCategory(row.id)
    ElMessage.success('分类已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

onMounted(load)
</script>
