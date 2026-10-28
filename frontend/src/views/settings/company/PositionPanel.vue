<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>职务设置</h2>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增职务
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="id" label="ID" width="90" />
      <el-table-column prop="name" label="职务名称" min-width="220" />
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
          description="暂无职务"
          action-text="新增职务"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑职务' : '新增职务'"
      :width="480"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="职务名称" required>
        <el-input v-model="form.name" maxlength="64" />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { companyApi, type Position, type PositionPayload } from '@/api/company'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<Position[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<Position | null>(null)
const form = reactive<PositionPayload>({ name: '' })

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await companyApi.listPositions()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '职务加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  dialogVisible.value = true
}

function openEdit(row: Position): void {
  editing.value = row
  form.name = row.name
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.name || '').trim()) {
    ElMessage.warning('请填写职务名称')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await companyApi.updatePosition(editing.value.id, { ...form })
    } else {
      await companyApi.createPosition({ ...form })
    }
    ElMessage.success(editing.value ? '职务已更新' : '职务已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '职务保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: Position): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除职务“${row.name}”吗？`, '删除职务', {
      type: 'warning',
    })
    await companyApi.deletePosition(row.id)
    ElMessage.success('职务已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '职务删除失败')
    }
  }
}

onMounted(load)
</script>
