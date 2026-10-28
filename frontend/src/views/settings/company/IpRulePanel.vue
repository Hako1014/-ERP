<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>IP 访问规则</h2>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增规则
      </el-button>
    </div>
    <el-alert
      class="ip-rule-tip"
      type="info"
      :closable="false"
      show-icon
      title="当前仅维护允许/禁止规则，登录来源拦截将在安全任务中启用。"
    />

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column label="规则类型" width="120">
        <template #default="{ row }">
          <StatusTag
            :status="row.rule_type"
            :label="row.rule_type === 'ALLOW' ? '允许' : '禁止'"
            :tone="row.rule_type === 'ALLOW' ? 'success' : 'danger'"
          />
        </template>
      </el-table-column>
      <el-table-column prop="ip_address" label="IP / CIDR" min-width="180" />
      <el-table-column prop="remark" label="备注" min-width="220" />
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
          description="暂无 IP 规则"
          action-text="新增规则"
          :show-action="canManage"
          @action="openCreate"
        />
      </template>
    </el-table>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑 IP 规则' : '新增 IP 规则'"
      :width="520"
      :loading="saving"
      @submit="submit"
    >
      <el-form-item label="规则类型" required>
        <el-radio-group v-model="form.rule_type">
          <el-radio value="ALLOW">允许访问</el-radio>
          <el-radio value="DENY">禁止访问</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="IP / CIDR" required>
        <el-input
          v-model="form.ip_address"
          placeholder="例如 192.168.1.10 或 192.168.1.0/24"
        />
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="form.remark" maxlength="255" />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { companyApi, type IpRule, type IpRulePayload } from '@/api/company'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'

defineProps<{
  canManage: boolean
}>()

const rows = ref<IpRule[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<IpRule | null>(null)
const form = reactive<IpRulePayload>({
  rule_type: 'ALLOW',
  ip_address: '',
  remark: '',
})

function resetForm(): void {
  Object.assign(form, {
    rule_type: 'ALLOW',
    ip_address: '',
    remark: '',
  })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await companyApi.listIpRules()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'IP 规则加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: IpRule): void {
  editing.value = row
  Object.assign(form, {
    rule_type: row.rule_type,
    ip_address: row.ip_address,
    remark: row.remark || '',
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.ip_address || '').trim()) {
    ElMessage.warning('请填写 IP 或 CIDR')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await companyApi.updateIpRule(editing.value.id, { ...form })
    } else {
      await companyApi.createIpRule({ ...form })
    }
    ElMessage.success(editing.value ? 'IP 规则已更新' : 'IP 规则已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'IP 规则保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: IpRule): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除 IP 规则“${row.ip_address}”吗？`, '删除规则', {
      type: 'warning',
    })
    await companyApi.deleteIpRule(row.id)
    ElMessage.success('IP 规则已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : 'IP 规则删除失败')
    }
  }
}

onMounted(load)
</script>

<style scoped>
.ip-rule-tip {
  margin-bottom: 14px;
}
</style>
