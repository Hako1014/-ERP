<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <div class="settings-page__toolbar-actions">
        <el-select
          v-model="logisticsFilter"
          class="logistics-filter"
          clearable
          placeholder="全部物流方式"
          @change="load"
        >
          <el-option
            v-for="item in logisticsOptions"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </div>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增区域规则
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="logistics_name" label="物流方式" min-width="140" />
      <el-table-column prop="province" label="省" min-width="110" />
      <el-table-column prop="city" label="市" min-width="110" />
      <el-table-column label="匹配重量(kg)" min-width="150">
        <template #default="{ row }">
          {{ weightRange(row) }}
        </template>
      </el-table-column>
      <el-table-column
        prop="freight_template_name"
        label="运费模板"
        min-width="150"
      />
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
          description="暂无区域物流规则"
          action-text="新增区域规则"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑区域物流规则' : '新增区域物流规则'"
      :width="680"
      :loading="saving"
      @submit="submit"
    >
      <div class="settings-page__form-grid">
        <el-form-item label="物流方式" required>
          <el-select
            v-model="form.logistics_id"
            filterable
            @change="handleLogisticsChange"
          >
            <el-option
              v-for="item in logisticsOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="运费模板" required>
          <el-select v-model="form.freight_template_id" clearable filterable>
            <el-option
              v-for="item in templateOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="省">
          <el-input v-model="form.province" maxlength="32" />
        </el-form-item>
        <el-form-item label="市">
          <el-input v-model="form.city" maxlength="32" />
        </el-form-item>
        <el-form-item label="匹配重量下限(kg)">
          <el-input v-model="form.match_weight_min" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="匹配重量上限(kg)">
          <el-input v-model="form.match_weight_max" inputmode="decimal" />
        </el-form-item>
      </div>
      <p class="settings-page__muted">
        仅填写市、不填写省也可匹配；重量边界按闭区间处理。留空表示对应方向不限。
      </p>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  logisticsApi,
  type AreaRule,
  type AreaRulePayload,
  type FreightTemplate,
  type Logistics,
} from '@/api/logistics'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<AreaRule[]>([])
const logisticsOptions = ref<Logistics[]>([])
const templateOptions = ref<FreightTemplate[]>([])
const loading = ref(false)
const saving = ref(false)
const logisticsFilter = ref<number | undefined>()
const dialogVisible = ref(false)
const editing = ref<AreaRule | null>(null)
const form = reactive<AreaRulePayload>({
  logistics_id: undefined,
  province: '',
  city: '',
  match_weight_min: '',
  match_weight_max: '',
  freight_template_id: null,
})

function resetForm(): void {
  Object.assign(form, {
    logistics_id: logisticsFilter.value,
    province: '',
    city: '',
    match_weight_min: '',
    match_weight_max: '',
    freight_template_id: null,
  })
  templateOptions.value = []
}

function weightRange(row: AreaRule): string {
  if (row.match_weight_min == null && row.match_weight_max == null) {
    return '不限'
  }
  return `${row.match_weight_min ?? '不限'} - ${row.match_weight_max ?? '不限'}`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await logisticsApi.listAreaRules(logisticsFilter.value)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '区域规则加载失败')
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

async function handleLogisticsChange(logisticsId?: number): Promise<void> {
  form.freight_template_id = null
  templateOptions.value = logisticsId
    ? await logisticsApi.listFreightTemplates(logisticsId)
    : []
}

async function openCreate(): Promise<void> {
  editing.value = null
  resetForm()
  if (form.logistics_id) {
    await handleLogisticsChange(form.logistics_id)
  }
  dialogVisible.value = true
}

async function openEdit(row: AreaRule): Promise<void> {
  editing.value = row
  Object.assign(form, {
    logistics_id: row.logistics_id,
    province: row.province || '',
    city: row.city || '',
    match_weight_min: row.match_weight_min ?? '',
    match_weight_max: row.match_weight_max ?? '',
    freight_template_id: row.freight_template_id || null,
  })
  await handleLogisticsChange(row.logistics_id)
  form.freight_template_id = row.freight_template_id || null
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.logistics_id || !form.freight_template_id) {
    ElMessage.warning('请选择物流方式和运费模板')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await logisticsApi.updateAreaRule(editing.value.id, { ...form })
    } else {
      await logisticsApi.createAreaRule({ ...form })
    }
    ElMessage.success(editing.value ? '区域规则已更新' : '区域规则已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '区域规则保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: AreaRule): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除“${row.province || '全部'} ${row.city || ''}”规则吗？`,
      '删除区域规则',
      { type: 'warning' },
    )
    await logisticsApi.deleteAreaRule(row.id)
    ElMessage.success('区域规则已删除')
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
.logistics-filter {
  width: 210px;
}
</style>
