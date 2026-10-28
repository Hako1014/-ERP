<template>
  <div class="settings-page approval-page">
    <SettingsPageHeader title="审批管理" description="配置各类业务单据的审批环节与审批人" />
    <section class="settings-page__panel">
      <div class="settings-page__toolbar">
        <h2>审批流配置</h2>
        <el-button v-if="canConfigure" type="primary" @click="openCreate">新增审批流</el-button>
      </div>
      <DataTable :rows="flows" :columns="columns" :loading="loading" :total="flows.length" empty-description="暂无审批流">
        <template #steps="{ row }">
          <span class="approval-flow-display">{{ row.flow_display || '--' }}</span>
        </template>
        <template #createdAt="{ row }">{{ formatDateTime(row.created_at) }}</template>
        <template #actions="{ row }">
          <el-button v-if="canConfigure" link type="primary" @click="openEdit(row)">编辑</el-button>
          <span v-else class="settings-page__muted">只读</span>
        </template>
      </DataTable>
    </section>

    <FormDialog v-model:visible="dialogVisible" :title="editing ? '编辑审批流' : '新增审批流'" :loading="saving" @submit="submit">
      <el-form-item label="审批名称" required><el-input v-model="form.name" maxlength="64" /></el-form-item>
      <el-form-item label="单据类型" required>
        <el-select v-model="form.doc_type" filterable :disabled="Boolean(editing)">
          <el-option v-for="item in docTypes" :key="item.code" :label="item.name" :value="item.code" />
        </el-select>
      </el-form-item>
      <el-form-item label="审批人" required>
        <el-select v-model="form.approvers" multiple filterable collapse-tags placeholder="可选择多个审批人">
          <el-option v-for="staff in staffs" :key="staff.id" :label="`${staff.name}${staff.staff_no ? `（${staff.staff_no}）` : ''}`" :value="staff.id" />
        </el-select>
      </el-form-item>
      <el-alert title="多个审批人按同一审批环节处理；如需多环节，可在后端配置后查看流程。" type="info" :closable="false" />
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { approvalApi, type ApprovalDocType, type ApprovalFlowRecord } from '@/api/approval'
import { companyApi, type Staff } from '@/api/company'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'

const auth = useAuthStore()
const canConfigure = computed(() => auth.hasPermission('approval:configure'))
const columns: DataTableColumn<ApprovalFlowRecord>[] = [
  { prop: 'name', label: '审批名称', minWidth: 180 },
  { prop: 'doc_type_label', label: '单据类型', minWidth: 150 },
  { label: '审批流程', slot: 'steps', minWidth: 300 },
  { prop: 'created_by_name', label: '创建人', width: 110 },
  { label: '创建时间', slot: 'createdAt', width: 170 },
  { label: '操作', slot: 'actions', width: 100, fixed: 'right' },
]
const flows = ref<ApprovalFlowRecord[]>([])
const docTypes = ref<ApprovalDocType[]>([])
const staffs = ref<Staff[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<ApprovalFlowRecord | null>(null)
const form = reactive({ name: '', doc_type: '', approvers: [] as number[] })

async function load(): Promise<void> {
  loading.value = true
  try { flows.value = await approvalApi.listFlows() } catch (error) { ElMessage.error(error instanceof Error ? error.message : '审批流加载失败') } finally { loading.value = false }
}
function resetForm(): void { form.name = ''; form.doc_type = ''; form.approvers = [] }
function openCreate(): void { editing.value = null; resetForm(); dialogVisible.value = true }
function openEdit(row: ApprovalFlowRecord): void {
  editing.value = row; form.name = row.name; form.doc_type = row.doc_type; form.approvers = row.steps.flatMap((step) => step.approver_ids); dialogVisible.value = true
}
async function submit(): Promise<void> {
  if (!form.name.trim() || !form.doc_type || !form.approvers.length) { ElMessage.warning('请完整填写审批名称、单据类型和审批人'); return }
  saving.value = true
  try {
    const payload = { name: form.name.trim(), doc_type: form.doc_type, approvers: form.approvers }
    if (editing.value) await approvalApi.updateFlow(editing.value.id, payload)
    else await approvalApi.createFlow(payload)
    ElMessage.success(editing.value ? '审批流已更新' : '审批流已创建'); dialogVisible.value = false; await load()
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '审批流保存失败') } finally { saving.value = false }
}
onMounted(async () => {
  try { [docTypes.value, staffs.value] = await Promise.all([approvalApi.listDocTypes(), companyApi.listStaff({ is_active: true, page_size: 200 }).then((result) => result.items)]); await load() }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '审批配置选项加载失败') }
})
</script>

<style scoped>
.approval-flow-display { color: var(--el-text-color-regular); }
</style>
