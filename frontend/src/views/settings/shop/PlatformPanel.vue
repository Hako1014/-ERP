<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <div class="settings-page__toolbar-actions">
        <el-input
          v-model="keyword"
          class="settings-page__search"
          clearable
          placeholder="平台名称或编码"
          @keyup.enter="load"
          @clear="load"
        />
        <el-button @click="load">查询</el-button>
      </div>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增平台
      </el-button>
    </div>

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="name" label="平台名称" min-width="180" />
      <el-table-column prop="code" label="平台编码" min-width="160" />
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
          description="暂无平台"
          action-text="新增平台"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑平台' : '新增平台'"
      :width="520"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="平台名称" required>
        <el-input v-model="form.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="平台编码">
        <el-input v-model="form.code" maxlength="32" />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { shopApi, type Platform, type PlatformPayload } from '@/api/shop'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<Platform[]>([])
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const dialogVisible = ref(false)
const editing = ref<Platform | null>(null)
const form = reactive<PlatformPayload>({ name: '', code: '' })

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await shopApi.listPlatforms(keyword.value || undefined)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '平台加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  Object.assign(form, { name: '', code: '' })
  dialogVisible.value = true
}

function openEdit(row: Platform): void {
  editing.value = row
  Object.assign(form, { name: row.name, code: row.code || '' })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.name || '').trim()) {
    ElMessage.warning('请填写平台名称')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await shopApi.updatePlatform(editing.value.id, { ...form })
    } else {
      await shopApi.createPlatform({ ...form })
    }
    ElMessage.success(editing.value ? '平台已更新' : '平台已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '平台保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: Platform): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除平台“${row.name}”吗？`, '删除平台', {
      type: 'warning',
    })
    await shopApi.deletePlatform(row.id)
    ElMessage.success('平台已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '平台删除失败')
    }
  }
}

onMounted(load)
</script>
