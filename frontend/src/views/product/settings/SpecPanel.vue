<template>
  <div class="product-settings-split">
    <section class="product-settings-split__panel">
      <div class="settings-page__toolbar">
        <h3>规格类型</h3>
        <el-button v-if="canManage" type="primary" @click="openCreateType">
          新增规格类型
        </el-button>
      </div>
      <el-table
        v-loading="loading"
        class="product-settings-list"
        :data="specTypes"
        row-key="id"
        highlight-current-row
        @current-change="selectSpecType"
      >
        <el-table-column prop="name" label="规格类型" min-width="130" />
        <el-table-column label="关联分类" min-width="120">
          <template #default="{ row }">
            {{ categoryName(row.category_id) }}
          </template>
        </el-table-column>
        <el-table-column
          v-if="canManage"
          label="操作"
          width="120"
          fixed="right"
        >
          <template #default="{ row }">
            <div class="settings-page__actions">
              <el-button link type="primary" @click="openEditType(row)">
                编辑
              </el-button>
              <el-button link type="danger" @click="removeType(row)">
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            description="暂无规格类型"
            action-text="新增规格类型"
            :show-action="canManage"
            @action="openCreateType"
          />
        </template>
      </el-table>
    </section>

    <section class="product-settings-split__panel">
      <div class="settings-page__toolbar">
        <h3>
          规格明细
          <span v-if="selectedType" class="settings-page__muted">
            · {{ selectedType.name }}
          </span>
        </h3>
        <el-button
          v-if="canManage"
          type="primary"
          :disabled="!selectedType"
          @click="openCreateValue"
        >
          新增规格明细
        </el-button>
      </div>
      <el-table
        v-loading="valueLoading"
        class="product-settings-list"
        :data="specValues"
        row-key="id"
      >
        <el-table-column prop="name" label="规格明细名称" min-width="150" />
        <el-table-column prop="code" label="规格明细编码" min-width="150" />
        <el-table-column
          prop="sort_order"
          label="排序"
          width="80"
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
              <el-button link type="primary" @click="openEditValue(row)">
                编辑
              </el-button>
              <el-button link type="danger" @click="removeValue(row)">
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            :description="selectedType ? '暂无规格明细' : '请先选择规格类型'"
            action-text="新增规格明细"
            :show-action="canManage && Boolean(selectedType)"
            @action="openCreateValue"
          />
        </template>
      </el-table>
    </section>

    <FormDialog
      v-model:visible="typeDialogVisible"
      :title="editingType ? '编辑规格类型' : '新增规格类型'"
      :loading="saving"
      @submit="submitType"
    >
      <el-form-item label="规格类型名称" required>
        <el-input v-model="typeForm.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="关联分类">
        <el-select v-model="typeForm.category_id" clearable filterable>
          <el-option
            v-for="item in categories"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
    </FormDialog>

    <FormDialog
      v-model:visible="valueDialogVisible"
      :title="editingValue ? '编辑规格明细' : '新增规格明细'"
      :loading="saving"
      @submit="submitValue"
    >
      <el-form-item label="规格明细名称" required>
        <el-input v-model="valueForm.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="规格明细编码">
        <el-input v-model="valueForm.code" maxlength="64" />
      </el-form-item>
      <el-form-item label="排序">
        <el-input-number v-model="valueForm.sort_order" :min="0" />
      </el-form-item>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  productSettingsApi,
  type ProductCategory,
  type ProductSpecType,
  type ProductSpecValue,
} from '@/api/productSettings'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

defineProps<{
  canManage: boolean
}>()

const specTypes = ref<ProductSpecType[]>([])
const specValues = ref<ProductSpecValue[]>([])
const categories = ref<ProductCategory[]>([])
const selectedType = ref<ProductSpecType | null>(null)
const loading = ref(false)
const valueLoading = ref(false)
const saving = ref(false)
const typeDialogVisible = ref(false)
const valueDialogVisible = ref(false)
const editingType = ref<ProductSpecType | null>(null)
const editingValue = ref<ProductSpecValue | null>(null)
const typeForm = reactive({
  name: '',
  category_id: null as number | null,
})
const valueForm = reactive({
  name: '',
  code: '',
  sort_order: 0,
})

function categoryName(categoryId?: number | null): string {
  if (!categoryId) {
    return '--'
  }
  return categories.value.find((item) => item.id === categoryId)?.name || `#${categoryId}`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [types, categoryRows] = await Promise.all([
      productSettingsApi.listSpecTypes(),
      productSettingsApi.listCategories(),
    ])
    specTypes.value = types
    categories.value = categoryRows
    if (
      selectedType.value &&
      !types.some((item) => item.id === selectedType.value?.id)
    ) {
      selectedType.value = null
      specValues.value = []
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格类型加载失败')
  } finally {
    loading.value = false
  }
}

async function selectSpecType(row: ProductSpecType | null): Promise<void> {
  selectedType.value = row
  specValues.value = []
  if (!row) {
    return
  }
  valueLoading.value = true
  try {
    specValues.value = await productSettingsApi.listSpecValues(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格明细加载失败')
  } finally {
    valueLoading.value = false
  }
}

function openCreateType(): void {
  editingType.value = null
  typeForm.name = ''
  typeForm.category_id = null
  typeDialogVisible.value = true
}

function openEditType(row: ProductSpecType): void {
  editingType.value = row
  typeForm.name = row.name
  typeForm.category_id = row.category_id || null
  typeDialogVisible.value = true
}

async function submitType(): Promise<void> {
  if (!typeForm.name.trim()) {
    ElMessage.warning('请填写规格类型名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: typeForm.name.trim(),
      category_id: typeForm.category_id,
    }
    if (editingType.value) {
      await productSettingsApi.updateSpecType(editingType.value.id, payload)
      ElMessage.success('规格类型已更新')
    } else {
      await productSettingsApi.createSpecType(payload)
      ElMessage.success('规格类型已创建')
    }
    typeDialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格类型保存失败')
  } finally {
    saving.value = false
  }
}

async function removeType(row: ProductSpecType): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除规格类型「${row.name}」及其规格明细？`,
      '删除规格类型',
      { type: 'warning' },
    )
    await productSettingsApi.deleteSpecType(row.id)
    if (selectedType.value?.id === row.id) {
      selectedType.value = null
      specValues.value = []
    }
    ElMessage.success('规格类型已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

function openCreateValue(): void {
  if (!selectedType.value) {
    return
  }
  editingValue.value = null
  valueForm.name = ''
  valueForm.code = ''
  valueForm.sort_order = specValues.value.length
  valueDialogVisible.value = true
}

function openEditValue(row: ProductSpecValue): void {
  editingValue.value = row
  valueForm.name = row.name
  valueForm.code = row.code || ''
  valueForm.sort_order = row.sort_order
  valueDialogVisible.value = true
}

async function submitValue(): Promise<void> {
  if (!selectedType.value || !valueForm.name.trim()) {
    ElMessage.warning('请填写规格明细名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: valueForm.name.trim(),
      code: valueForm.code.trim() || null,
      sort_order: valueForm.sort_order,
    }
    if (editingValue.value) {
      await productSettingsApi.updateSpecValue(editingValue.value.id, payload)
      ElMessage.success('规格明细已更新')
    } else {
      await productSettingsApi.createSpecValue(selectedType.value.id, payload)
      ElMessage.success('规格明细已创建')
    }
    valueDialogVisible.value = false
    await selectSpecType(selectedType.value)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格明细保存失败')
  } finally {
    saving.value = false
  }
}

async function removeValue(row: ProductSpecValue): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除规格明细「${row.name}」？`,
      '删除规格明细',
      { type: 'warning' },
    )
    await productSettingsApi.deleteSpecValue(row.id)
    ElMessage.success('规格明细已删除')
    await selectSpecType(selectedType.value)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

onMounted(load)
</script>
