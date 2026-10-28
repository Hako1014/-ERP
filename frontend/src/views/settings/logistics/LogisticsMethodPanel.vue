<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <div class="settings-page__toolbar-actions">
        <el-input
          v-model="keyword"
          class="settings-page__search"
          clearable
          placeholder="物流方式或物流公司"
          @keyup.enter="load"
          @clear="load"
        />
        <el-button @click="load">查询</el-button>
      </div>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增物流方式
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="物流方式" min-width="150" />
      <el-table-column prop="company" label="物流公司" min-width="150" />
      <el-table-column prop="contact" label="联系人" min-width="100" />
      <el-table-column prop="phone" label="联系方式" min-width="130" />
      <el-table-column label="默认" width="80" align="center">
        <template #default="{ row }">
          <StatusTag
            v-if="row.is_default"
            status="ACTIVE"
            label="默认"
            tone="success"
          />
          <span v-else>--</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{ row }">
          <StatusTag
            :status="row.is_enabled ? 'ACTIVE' : 'DISABLED'"
            :label="row.is_enabled ? '启用' : '停用'"
          />
        </template>
      </el-table-column>
      <el-table-column
        v-if="canManage"
        label="操作"
        width="240"
        fixed="right"
      >
        <template #default="{ row }">
          <div class="settings-page__actions">
            <el-button link type="primary" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button
              v-if="!row.is_default"
              link
              type="primary"
              @click="setDefault(row)"
            >
              设默认
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
          </div>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState
          description="暂无物流方式"
          action-text="新增物流方式"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑物流方式' : '新增物流方式'"
      :width="640"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="物流方式名称" required>
        <el-input v-model="form.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="物流公司">
        <el-input v-model="form.company" maxlength="64" />
      </el-form-item>
      <el-form-item label="联系人">
        <el-input v-model="form.contact" maxlength="64" />
      </el-form-item>
      <el-form-item label="联系方式">
        <el-input v-model="form.phone" maxlength="32" />
      </el-form-item>
      <el-form-item label="联系地址">
        <el-input v-model="form.address" maxlength="255" />
      </el-form-item>
      <el-form-item label="默认物流">
        <el-switch v-model="form.is_default" />
      </el-form-item>
      <el-form-item label="启用状态">
        <el-switch v-model="form.is_enabled" />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  logisticsApi,
  type Logistics,
  type LogisticsPayload,
} from '@/api/logistics'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<Logistics[]>([])
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const dialogVisible = ref(false)
const editing = ref<Logistics | null>(null)
const form = reactive<LogisticsPayload>({
  name: '',
  company: '',
  contact: '',
  phone: '',
  address: '',
  is_default: false,
  is_enabled: true,
})

function resetForm(): void {
  Object.assign(form, {
    name: '',
    company: '',
    contact: '',
    phone: '',
    address: '',
    is_default: false,
    is_enabled: true,
  })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await logisticsApi.list(keyword.value || undefined)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '物流方式加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Logistics): void {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    company: row.company || '',
    contact: row.contact || '',
    phone: row.phone || '',
    address: row.address || '',
    is_default: row.is_default,
    is_enabled: row.is_enabled,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.name || '').trim()) {
    ElMessage.warning('请填写物流方式名称')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await logisticsApi.update(editing.value.id, { ...form })
    } else {
      await logisticsApi.create({ ...form })
    }
    ElMessage.success(editing.value ? '物流方式已更新' : '物流方式已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '物流方式保存失败')
  } finally {
    saving.value = false
  }
}

async function setDefault(row: Logistics): Promise<void> {
  try {
    await logisticsApi.setDefault(row.id)
    ElMessage.success('已设为默认物流')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '设置失败')
  }
}

async function toggleEnabled(row: Logistics): Promise<void> {
  try {
    await (row.is_enabled
      ? logisticsApi.disable(row.id)
      : logisticsApi.enable(row.id))
    ElMessage.success(row.is_enabled ? '物流方式已停用' : '物流方式已启用')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '状态更新失败')
  }
}

async function remove(row: Logistics): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除物流方式“${row.name}”吗？`, '删除物流方式', {
      type: 'warning',
    })
    await logisticsApi.delete(row.id)
    ElMessage.success('物流方式已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

onMounted(load)
</script>
