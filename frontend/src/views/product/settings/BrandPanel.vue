<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>品牌管理</h2>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增品牌
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column label="品牌图片" width="100" align="center">
        <template #default="{ row }">
          <el-image class="product-thumb" :src="row.image || ''" fit="cover">
            <template #error>
              <div class="product-thumb__placeholder">
                <el-icon><Picture /></el-icon>
              </div>
            </template>
          </el-image>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="品牌名称" min-width="180" />
      <el-table-column prop="image" label="图片地址" min-width="260" />
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
          description="暂无品牌"
          action-text="新增品牌"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑品牌' : '新增品牌'"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="品牌名称" required>
        <el-input v-model="form.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="品牌图片">
        <el-input
          v-model="form.image"
          maxlength="255"
          placeholder="填写图片 URL"
        />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { Picture } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  productSettingsApi,
  type ProductBrand,
} from '@/api/productSettings'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<ProductBrand[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<ProductBrand | null>(null)
const form = reactive({
  name: '',
  image: '',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await productSettingsApi.listBrands()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '品牌加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.image = ''
  dialogVisible.value = true
}

function openEdit(row: ProductBrand): void {
  editing.value = row
  form.name = row.name
  form.image = row.image || ''
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写品牌名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      image: form.image.trim() || null,
    }
    if (editing.value) {
      await productSettingsApi.updateBrand(editing.value.id, payload)
      ElMessage.success('品牌已更新')
    } else {
      await productSettingsApi.createBrand(payload)
      ElMessage.success('品牌已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '品牌保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: ProductBrand): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除品牌「${row.name}」？`, '删除品牌', {
      type: 'warning',
    })
    await productSettingsApi.deleteBrand(row.id)
    ElMessage.success('品牌已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

onMounted(load)
</script>
