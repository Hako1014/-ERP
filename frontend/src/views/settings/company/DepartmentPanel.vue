<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>部门设置</h2>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增部门
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="部门名称" min-width="180" />
      <el-table-column label="上级部门" min-width="180">
        <template #default="{ row }">
          {{ departmentName(row.parent_id) }}
        </template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="90" />
      <el-table-column
        v-if="canManage"
        label="操作"
        width="150"
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
          description="暂无部门"
          action-text="新增部门"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑部门' : '新增部门'"
      :width="520"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="部门名称" required>
        <el-input v-model="form.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="上级部门">
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
        <el-input-number v-model="form.sort_order" :min="0" :max="99999" />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import {
  companyApi,
  type Department,
  type DepartmentPayload,
} from '@/api/company'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<Department[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<Department | null>(null)
const form = reactive<DepartmentPayload>({
  name: '',
  parent_id: null,
  sort_order: 0,
})
const parentOptions = computed(() =>
  rows.value.filter((item) => item.id !== editing.value?.id),
)

function resetForm(): void {
  Object.assign(form, {
    name: '',
    parent_id: null,
    sort_order: 0,
  })
}

function departmentName(id?: number | null): string {
  if (!id) {
    return '顶级部门'
  }
  return rows.value.find((item) => item.id === id)?.name || `#${id}`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await companyApi.listDepartments()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '部门加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Department): void {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    parent_id: row.parent_id || null,
    sort_order: row.sort_order,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.name || '').trim()) {
    ElMessage.warning('请填写部门名称')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await companyApi.updateDepartment(editing.value.id, { ...form })
    } else {
      await companyApi.createDepartment({ ...form })
    }
    ElMessage.success(editing.value ? '部门已更新' : '部门已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '部门保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: Department): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除部门“${row.name}”吗？`, '删除部门', {
      type: 'warning',
    })
    await companyApi.deleteDepartment(row.id)
    ElMessage.success('部门已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '部门删除失败')
    }
  }
}

onMounted(load)
</script>
