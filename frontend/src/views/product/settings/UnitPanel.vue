<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>计量单位</h2>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增单位
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="单位名称" min-width="180" />
      <el-table-column prop="code" label="单位编码" min-width="160" />
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
          description="暂无计量单位"
          action-text="新增单位"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑计量单位' : '新增计量单位'"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="单位名称" required>
        <el-input v-model="form.name" maxlength="32" />
      </el-form-item>
      <el-form-item label="单位编码">
        <el-input v-model="form.code" maxlength="32" />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  productSettingsApi,
  type ProductUnit,
} from '@/api/productSettings'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<ProductUnit[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<ProductUnit | null>(null)
const form = reactive({
  name: '',
  code: '',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await productSettingsApi.listUnits()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '计量单位加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.code = ''
  dialogVisible.value = true
}

function openEdit(row: ProductUnit): void {
  editing.value = row
  form.name = row.name
  form.code = row.code || ''
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写单位名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      code: form.code.trim() || null,
    }
    if (editing.value) {
      await productSettingsApi.updateUnit(editing.value.id, payload)
      ElMessage.success('计量单位已更新')
    } else {
      await productSettingsApi.createUnit(payload)
      ElMessage.success('计量单位已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '计量单位保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: ProductUnit): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除计量单位「${row.name}」？`,
      '删除计量单位',
      { type: 'warning' },
    )
    await productSettingsApi.deleteUnit(row.id)
    ElMessage.success('计量单位已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

onMounted(load)
</script>
