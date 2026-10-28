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
        新增运费模板
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="模板名称" min-width="150" />
      <el-table-column prop="logistics_name" label="物流方式" min-width="130" />
      <el-table-column label="计费方式" width="110">
        <template #default="{ row }">
          {{ chargeTypeLabel(row.charge_type) }}
        </template>
      </el-table-column>
      <el-table-column label="首件/首重" min-width="120">
        <template #default="{ row }">
          {{ firstUnit(row) }}
        </template>
      </el-table-column>
      <el-table-column label="续件/续重" min-width="120">
        <template #default="{ row }">
          {{ continueUnit(row) }}
        </template>
      </el-table-column>
      <el-table-column label="基础运费" width="120" align="right">
        <template #default="{ row }">
          <MoneyText :value="row.base_fee" />
        </template>
      </el-table-column>
      <el-table-column label="续费" width="120" align="right">
        <template #default="{ row }">
          <MoneyText :value="row.continue_fee" />
        </template>
      </el-table-column>
      <el-table-column
        v-if="canManage"
        label="操作"
        width="250"
        fixed="right"
      >
        <template #default="{ row }">
          <div class="settings-page__actions">
            <el-button link type="primary" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button link type="primary" @click="openCalculator(row)">
              试算
            </el-button>
            <el-button link type="danger" @click="remove(row)">
              删除
            </el-button>
          </div>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState
          description="暂无运费模板"
          action-text="新增运费模板"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑运费模板' : '新增运费模板'"
      :width="820"
      :loading="saving"
      @submit="submit"
    >
      <div class="settings-page__form-grid">
        <el-form-item label="模板名称" required>
          <el-input v-model="form.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="物流方式" required>
          <el-select v-model="form.logistics_id" filterable>
            <el-option
              v-for="item in logisticsOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="计费方式">
          <el-radio-group v-model="form.charge_type">
            <el-radio value="BY_COUNT">按件数</el-radio>
            <el-radio value="BY_WEIGHT">按重量</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="form.charge_type === 'BY_COUNT'">
          <el-form-item label="首件数量">
            <el-input-number v-model="form.first_count" :min="1" />
          </el-form-item>
          <el-form-item label="续件数量">
            <el-input-number v-model="form.continue_count" :min="1" />
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="首重(kg)">
            <el-input v-model="form.first_weight" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="续重(kg)">
            <el-input v-model="form.continue_weight" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="扣减重量(kg)">
            <el-input v-model="form.deduct_weight" inputmode="decimal" />
          </el-form-item>
        </template>
        <el-form-item label="基础运费">
          <el-input v-model="form.base_fee" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="续件/续重运费">
          <el-input v-model="form.continue_fee" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="操作费">
          <el-input v-model="form.operation_fee" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="耗材费">
          <el-input v-model="form.consumable_fee" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="电子面单费">
          <el-input v-model="form.ewaybill_fee" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="固定税费">
          <el-input v-model="form.tax_fee" inputmode="decimal" />
        </el-form-item>
        <el-form-item label="税点(%)">
          <el-input v-model="form.tax_point" inputmode="decimal" />
        </el-form-item>
      </div>
      <p class="settings-page__muted">
        税点按“运费 + 操作费 + 耗材费 + 电子面单费”计算，再叠加固定税费。
      </p>
    </FormDialog>

    <FormDialog
      v-model:visible="calculatorVisible"
      title="运费试算"
      :width="620"
      :loading="calculatorLoading"
      submit-text="计算"
      @submit="calculate"
    >
      <el-form-item label="模板">
        <span>{{ calculatorTarget?.name }}</span>
      </el-form-item>
      <el-form-item
        v-if="calculatorTarget?.charge_type === 'BY_COUNT'"
        label="件数"
        required
      >
        <el-input-number v-model="calculatorCount" :min="1" />
      </el-form-item>
      <el-form-item v-else label="重量(kg)" required>
        <el-input v-model="calculatorWeight" inputmode="decimal" />
      </el-form-item>

      <el-descriptions v-if="calculation" :column="2" border>
        <el-descriptions-item label="计费单位">
          {{ calculation.quantity_label }}
        </el-descriptions-item>
        <el-descriptions-item label="续费单位数">
          {{ calculation.additional_units }}
        </el-descriptions-item>
        <el-descriptions-item label="基础运费">
          <MoneyText :value="calculation.base_fee" />
        </el-descriptions-item>
        <el-descriptions-item label="续费">
          <MoneyText :value="calculation.additional_fee" />
        </el-descriptions-item>
        <el-descriptions-item label="税费">
          <MoneyText :value="calculation.tax_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="合计">
          <MoneyText :value="calculation.total_fee" />
        </el-descriptions-item>
      </el-descriptions>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import {
  logisticsApi,
  type FreightCalculation,
  type FreightTemplate,
  type FreightTemplatePayload,
  type Logistics,
} from '@/api/logistics'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<FreightTemplate[]>([])
const logisticsOptions = ref<Logistics[]>([])
const loading = ref(false)
const saving = ref(false)
const logisticsFilter = ref<number | undefined>()
const dialogVisible = ref(false)
const editing = ref<FreightTemplate | null>(null)
const form = reactive<FreightTemplatePayload>({
  name: '',
  logistics_id: undefined,
  charge_type: 'BY_COUNT',
  first_count: 1,
  continue_count: 1,
  first_weight: '',
  continue_weight: '',
  base_fee: '0',
  continue_fee: '0',
  deduct_weight: '',
  operation_fee: '0',
  consumable_fee: '0',
  ewaybill_fee: '0',
  tax_fee: '0',
  tax_point: '',
})
const calculatorVisible = ref(false)
const calculatorLoading = ref(false)
const calculatorTarget = ref<FreightTemplate | null>(null)
const calculatorCount = ref(1)
const calculatorWeight = ref('')
const calculation = ref<FreightCalculation | null>(null)

const chargeType = computed(() => form.charge_type || 'BY_COUNT')

function resetForm(): void {
  Object.assign(form, {
    name: '',
    logistics_id: undefined,
    charge_type: 'BY_COUNT',
    first_count: 1,
    continue_count: 1,
    first_weight: '',
    continue_weight: '',
    base_fee: '0',
    continue_fee: '0',
    deduct_weight: '',
    operation_fee: '0',
    consumable_fee: '0',
    ewaybill_fee: '0',
    tax_fee: '0',
    tax_point: '',
  })
}

function chargeTypeLabel(value: string): string {
  return value === 'BY_WEIGHT' ? '按重量' : '按件数'
}

function firstUnit(row: FreightTemplate): string {
  return row.charge_type === 'BY_WEIGHT'
    ? `${row.first_weight ?? '--'} kg`
    : `${row.first_count} 件`
}

function continueUnit(row: FreightTemplate): string {
  return row.charge_type === 'BY_WEIGHT'
    ? `${row.continue_weight ?? '--'} kg`
    : `${row.continue_count} 件`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await logisticsApi.listFreightTemplates(
      logisticsFilter.value,
    )
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '运费模板加载失败')
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

function normalizedPayload(): FreightTemplatePayload {
  const payload: FreightTemplatePayload = {
    ...form,
    charge_type: chargeType.value,
  }
  if (chargeType.value === 'BY_COUNT') {
    delete payload.first_weight
    delete payload.continue_weight
    delete payload.deduct_weight
  } else {
    delete payload.first_count
    delete payload.continue_count
  }
  return payload
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: FreightTemplate): void {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    logistics_id: row.logistics_id,
    charge_type: row.charge_type,
    first_count: row.first_count,
    continue_count: row.continue_count,
    first_weight: row.first_weight ?? '',
    continue_weight: row.continue_weight ?? '',
    base_fee: row.base_fee,
    continue_fee: row.continue_fee,
    deduct_weight: row.deduct_weight ?? '',
    operation_fee: row.operation_fee,
    consumable_fee: row.consumable_fee,
    ewaybill_fee: row.ewaybill_fee,
    tax_fee: row.tax_fee,
    tax_point: row.tax_point ?? '',
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.name || '').trim() || !form.logistics_id) {
    ElMessage.warning('请填写模板名称并选择物流方式')
    return
  }
  if (
    form.charge_type === 'BY_WEIGHT' &&
    (!form.first_weight || !form.continue_weight)
  ) {
    ElMessage.warning('请填写首重和续重')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await logisticsApi.updateFreightTemplate(
        editing.value.id,
        normalizedPayload(),
      )
    } else {
      await logisticsApi.createFreightTemplate(normalizedPayload())
    }
    ElMessage.success(editing.value ? '运费模板已更新' : '运费模板已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '运费模板保存失败')
  } finally {
    saving.value = false
  }
}

function openCalculator(row: FreightTemplate): void {
  calculatorTarget.value = row
  calculatorCount.value = 1
  calculatorWeight.value = ''
  calculation.value = null
  calculatorVisible.value = true
}

async function calculate(): Promise<void> {
  if (!calculatorTarget.value) {
    return
  }
  if (
    calculatorTarget.value.charge_type !== 'BY_COUNT' &&
    !calculatorWeight.value
  ) {
    ElMessage.warning('请填写重量')
    return
  }
  calculatorLoading.value = true
  try {
    calculation.value = await logisticsApi.calculateFreight(
      calculatorTarget.value.id,
      calculatorTarget.value.charge_type === 'BY_COUNT'
        ? { count: calculatorCount.value }
        : { weight: calculatorWeight.value },
    )
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '运费试算失败')
  } finally {
    calculatorLoading.value = false
  }
}

async function remove(row: FreightTemplate): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除模板“${row.name}”吗？`, '删除运费模板', {
      type: 'warning',
    })
    await logisticsApi.deleteFreightTemplate(row.id)
    ElMessage.success('运费模板已删除')
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
