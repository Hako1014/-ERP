<template>
  <div class="settings-page approval-page">
    <SettingsPageHeader title="我的审批" description="处理待审核单据并查看审批留痕" />
    <section class="settings-page__panel">
      <div class="settings-page__toolbar">
        <div class="approval-tabs"><el-radio-group v-model="scope" @change="search"><el-radio-button value="pending">待我审批</el-radio-button><el-radio-button value="submitted">我提交的</el-radio-button><el-radio-button value="processed">我处理的</el-radio-button></el-radio-group></div>
        <div class="settings-page__toolbar-actions"><el-input v-model="keyword" clearable placeholder="单据号、审批流或提交人" @keyup.enter="search" /><el-select v-model="docType" clearable placeholder="全部单据类型" @change="search"><el-option v-for="item in docTypes" :key="item.code" :label="item.name" :value="item.code" /></el-select><el-button @click="search">查询</el-button></div>
      </div>
      <DataTable v-model:page="page" v-model:page-size="pageSize" :rows="items" :columns="columns" :loading="loading" :total="total" :sync-query="false" empty-description="暂无审批记录" @query-change="load">
        <template #docNo="{ row }"><span>{{ row.doc_no || `#${row.doc_id}` }}</span></template>
        <template #status="{ row }"><StatusTag :status="row.approval_status" :label="statusLabel(row.approval_status)" /></template>
        <template #submittedAt="{ row }">{{ formatDateTime(row.submitted_at) }}</template>
        <template #actions="{ row }"><el-button link type="primary" @click="openDetail(row)">{{ scope === 'pending' ? '处理' : '详情' }}</el-button></template>
      </DataTable>
    </section>

    <el-drawer v-model="detailVisible" title="审批详情" size="680px">
      <el-skeleton v-if="detailLoading" :rows="8" animated />
      <template v-else-if="detail">
        <el-descriptions :column="2" border><el-descriptions-item label="单据类型">{{ detail.doc_type_label }}</el-descriptions-item><el-descriptions-item label="单据编号">{{ detail.doc_no || `#${detail.doc_id}` }}</el-descriptions-item><el-descriptions-item label="当前状态"><StatusTag :status="detail.approval.status" :label="statusLabel(detail.approval.status)" /></el-descriptions-item><el-descriptions-item label="审批流">{{ detail.flow?.name || '--' }}</el-descriptions-item></el-descriptions>
        <el-divider content-position="left">单据摘要</el-divider>
        <div class="approval-detail-grid"><template v-for="(value, key) in summaryFields" :key="key"><div v-if="value !== undefined && value !== null && value !== ''"><span>{{ key }}</span><strong>{{ String(value) }}</strong></div></template></div>
        <el-divider content-position="left">审批流程与留痕</el-divider>
        <ApprovalFlow :flow-name="detail.flow?.name" :state="detail.approval" :records="detail.records" :auto-load="false" />
        <div v-if="detail.can_approve || detail.can_reject || detail.can_unaudit" class="approval-actions"><el-button v-if="detail.can_approve" type="success" @click="runAction('approve')">审核通过</el-button><el-button v-if="detail.can_reject" type="danger" plain @click="runAction('reject')">审核不通过</el-button><el-button v-if="detail.can_unaudit" type="warning" plain @click="runAction('unaudit')">弃审</el-button></div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { approvalApi, type ApprovalDetail, type ApprovalDocType, type ApprovalListItem } from '@/api/approval'
import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import DataTable from '@/components/DataTable.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import '@/views/settings/settings.css'

const columns: DataTableColumn<ApprovalListItem>[] = [{ label: '单据编号', slot: 'docNo', minWidth: 170 }, { prop: 'doc_type_label', label: '单据类型', minWidth: 150 }, { prop: 'flow_name', label: '审批流', minWidth: 150 }, { prop: 'current_step', label: '当前环节', width: 100, formatter: (row) => row.current_step ? `第 ${row.current_step}/${row.total_steps} 环节` : '--' }, { prop: 'submitted_by_name', label: '提交人', width: 110 }, { label: '审批状态', slot: 'status', width: 110 }, { label: '提交时间', slot: 'submittedAt', width: 170 }, { label: '操作', slot: 'actions', width: 90, fixed: 'right' }]
const authStore = useAuthStore(); const route = useRoute(); const router = useRouter()
let detailRequestSequence = 0
const scope = ref('pending'); const keyword = ref(''); const docType = ref(''); const page = ref(1); const pageSize = ref(20); const total = ref(0); const items = ref<ApprovalListItem[]>([]); const docTypes = ref<ApprovalDocType[]>([]); const loading = ref(false); const detailVisible = ref(false); const detailLoading = ref(false); const detail = ref<ApprovalDetail | null>(null)
const summaryFields = computed(() => { const source = detail.value?.detail || {}; const labels: Record<string, string> = { order_no: '订单号', plan_no: '计划号', invoice_no: '发票号', prepay_no: '申请单号', supplier_name: '供应商', customer_name: '客户', status: '业务状态', audit_status: '审核状态', total_amount: '金额', description: '说明' }; return Object.fromEntries(Object.entries(labels).map(([key, label]) => [label, source[key]])) })
function statusLabel(status: string): string { return ({ NOT_SUBMITTED: '未提交', PENDING: '审批中', APPROVED: '已通过', REJECTED: '已驳回', UNKNOWN: '未知' } as Record<string, string>)[status] || status }
async function load(): Promise<void> { loading.value = true; try { const result = await approvalApi.listMine({ scope: scope.value, keyword: keyword.value || undefined, doc_type: docType.value || undefined, page: page.value, page_size: pageSize.value }); items.value = result.items; total.value = result.total } catch (error) { ElMessage.error(error instanceof Error ? error.message : '审批列表加载失败') } finally { loading.value = false } }
function search(): void { page.value = 1; void load() }
function currentIdentity(): string { return `${authStore.profile?.staff_id || ''}:${authStore.token || ''}` }
async function openDetail(row: ApprovalListItem): Promise<void> {
  const sequence = ++detailRequestSequence
  const identity = currentIdentity()
  detailVisible.value = true
  detailLoading.value = true
  try {
    const current = await approvalApi.getDetail(row.doc_type, row.doc_id)
    if (identity === currentIdentity() && sequence === detailRequestSequence) detail.value = current
  } catch (error) {
    if (identity === currentIdentity() && sequence === detailRequestSequence) {
      ElMessage.error(
        error instanceof ApiError && (error.status === 403 || error.status === 404)
          ? '审批单据已失效或当前账号无权查看。'
          : error instanceof Error
            ? error.message
            : '审批详情加载失败',
      )
    }
  } finally {
    if (identity === currentIdentity() && sequence === detailRequestSequence) detailLoading.value = false
  }
}

async function openNotificationTarget(
  docType: unknown,
  docId: unknown,
): Promise<void> {
  const type = Array.isArray(docType) ? docType[0] : docType
  const rawId = Array.isArray(docId) ? docId[0] : docId
  const clearRouteQuery = async () => {
    const query = { ...route.query }
    delete query.notificationDocType
    delete query.notificationDocId
    if (route.query.notificationDocType || route.query.notificationDocId) {
      await router.replace({ path: route.path, query })
    }
  }

  if (typeof type !== 'string' || typeof rawId !== 'string' || !/^\d{1,15}$/.test(rawId)) {
    await clearRouteQuery()
    return
  }
  const id = Number(rawId)
  if (!Number.isSafeInteger(id) || id < 1) {
    await clearRouteQuery()
    return
  }

  const identity = currentIdentity()
  const sequence = ++detailRequestSequence
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  try {
    const current = await approvalApi.getDetail(type, id)
    if (identity !== currentIdentity() || sequence !== detailRequestSequence) return
    detail.value = current
  } catch (error) {
    if (identity === currentIdentity() && sequence === detailRequestSequence) {
      detailVisible.value = false
      ElMessage.error(
        error instanceof ApiError && (error.status === 403 || error.status === 404)
          ? '目标单据已失效或当前账号无权查看。'
          : error instanceof Error
            ? error.message
            : '审批单据无法打开。',
      )
    }
  } finally {
    if (identity === currentIdentity() && sequence === detailRequestSequence) {
      detailLoading.value = false
      await clearRouteQuery()
    }
  }
}
async function runAction(action: 'approve' | 'reject' | 'unaudit'): Promise<void> { if (!detail.value) return; try { if (action === 'approve') { const { value } = await ElMessageBox.prompt('可填写审批意见', '审核通过', { inputPlaceholder: '可留空' }); detail.value = await approvalApi.approve(detail.value.doc_type, detail.value.doc_id, value || undefined) } else if (action === 'reject') { const { value } = await ElMessageBox.prompt('请填写不通过原因', '审核不通过', { inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见' }); detail.value = await approvalApi.reject(detail.value.doc_type, detail.value.doc_id, value) } else { const { value } = await ElMessageBox.prompt('请填写弃审原因', '弃审', { inputValidator: (value) => Boolean(value?.trim()) || '请填写弃审原因' }); detail.value = await approvalApi.unaudit(detail.value.doc_type, detail.value.doc_id, value) }; ElMessage.success('审批操作已完成'); await load() } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '审批操作失败') } }
onMounted(async () => { try { docTypes.value = await approvalApi.listDocTypes(); await load() } catch (error) { ElMessage.error(error instanceof Error ? error.message : '审批页面初始化失败') } })
watch(() => [route.query.notificationDocType, route.query.notificationDocId], ([docType, docId]) => { if (docType || docId) void openNotificationTarget(docType, docId) }, { immediate: true })
</script>

<style scoped>
.approval-tabs { min-width: 360px; }
.approval-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.approval-detail-grid div { padding: 10px 12px; background: var(--el-fill-color-light); border-radius: 4px; }
.approval-detail-grid span { display: block; color: var(--el-text-color-secondary); font-size: 12px; margin-bottom: 4px; }
.approval-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 24px; }
@media (max-width: 760px) { .approval-tabs { min-width: 0; } .approval-detail-grid { grid-template-columns: 1fr; } }
</style>
