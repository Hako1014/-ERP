<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <div class="settings-page__toolbar-actions">
        <el-select
          v-model="typeFilter"
          class="type-filter"
          clearable
          placeholder="全部模板类型"
          @change="load"
        >
          <el-option
            v-for="item in TEMPLATE_TYPES"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </div>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增打印模板
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="模板名称" min-width="160" />
      <el-table-column label="模板类型" min-width="130">
        <template #default="{ row }">
          {{ templateTypeLabel(row.template_type) }}
        </template>
      </el-table-column>
      <el-table-column label="纸张尺寸" min-width="140">
        <template #default="{ row }">
          {{ paperSize(row) }}
        </template>
      </el-table-column>
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
          description="暂无打印模板"
          action-text="新增打印模板"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑打印模板' : '新增打印模板'"
      :width="760"
      :loading="saving"
      @submit="submit"
    >
      <div class="settings-page__form-grid">
        <el-form-item label="模板名称" required>
          <el-input v-model="form.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="模板类型" required>
          <el-select v-model="form.template_type">
            <el-option
              v-for="item in TEMPLATE_TYPES"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="物流方式">
          <el-select v-model="form.logistics_id" clearable filterable>
            <el-option
              v-for="item in logisticsOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="纸张宽度(mm)">
          <el-input v-model="form.paper_width" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="纸张高度(mm)">
          <el-input v-model="form.paper_height" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="默认模板">
          <el-switch v-model="form.is_default" />
        </el-form-item>
        <el-form-item class="is-full-width" label="打印内容">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="5"
            placeholder="填写模板字段、HTML 或打印配置内容"
          />
        </el-form-item>
      </div>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  logisticsApi,
  type Logistics,
  type PrintTemplate,
  type PrintTemplatePayload,
} from '@/api/logistics'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'

defineProps<{
  canManage: boolean
}>()

const TEMPLATE_TYPES = [
  { value: 'SHIPPING', label: '快递面单' },
  { value: 'PICKING', label: '拣货单' },
  { value: 'DISTRIBUTION', label: '配货单' },
  { value: 'STOCK_IN', label: '入库单' },
] as const

const rows = ref<PrintTemplate[]>([])
const logisticsOptions = ref<Logistics[]>([])
const loading = ref(false)
const saving = ref(false)
const typeFilter = ref<string | undefined>()
const dialogVisible = ref(false)
const editing = ref<PrintTemplate | null>(null)
const form = reactive<PrintTemplatePayload>({
  name: '',
  template_type: 'SHIPPING',
  logistics_id: null,
  content: '',
  paper_width: '',
  paper_height: '',
  is_default: false,
})

function resetForm(): void {
  Object.assign(form, {
    name: '',
    template_type: 'SHIPPING',
    logistics_id: null,
    content: '',
    paper_width: '',
    paper_height: '',
    is_default: false,
  })
}

function templateTypeLabel(value: string): string {
  return (
    TEMPLATE_TYPES.find((item) => item.value === value)?.label || value
  )
}

function paperSize(row: PrintTemplate): string {
  if (row.paper_width == null || row.paper_height == null) {
    return '未设置'
  }
  return `${row.paper_width} × ${row.paper_height} mm`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await logisticsApi.listPrintTemplates(
      undefined,
      typeFilter.value,
    )
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '打印模板加载失败')
  } finally {
    loading.value = false
  }
}

async function loadOptions(): Promise<void> {
  try {
    logisticsOptions.value = await logisticsApi.list()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '物流方式加载失败')
  }
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: PrintTemplate): void {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    template_type: row.template_type,
    logistics_id: row.logistics_id || null,
    content: row.content || '',
    paper_width: row.paper_width ?? '',
    paper_height: row.paper_height ?? '',
    is_default: row.is_default,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.name || '').trim() || !form.template_type) {
    ElMessage.warning('请填写模板名称并选择模板类型')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await logisticsApi.updatePrintTemplate(editing.value.id, { ...form })
    } else {
      await logisticsApi.createPrintTemplate({ ...form })
    }
    ElMessage.success(editing.value ? '打印模板已更新' : '打印模板已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '打印模板保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: PrintTemplate): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除模板“${row.name}”吗？`, '删除打印模板', {
      type: 'warning',
    })
    await logisticsApi.deletePrintTemplate(row.id)
    ElMessage.success('打印模板已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([load(), loadOptions()])
})
</script>

<style scoped>
.type-filter {
  width: 190px;
}
</style>
