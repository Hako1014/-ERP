<template>
  <div v-loading="loading" class="approval-flow">
    <div class="approval-flow__header">
      <div>
        <strong>{{ displayFlowName || '审批流' }}</strong>
        <span v-if="displayState.total_steps" class="approval-flow__progress">
          {{ progressText }}
        </span>
      </div>
      <StatusTag
        :status="String(displayState.status)"
        :label="approvalStatusLabel"
        :tone="approvalStatusTone"
      />
    </div>

    <el-steps
      v-if="displayState.total_steps > 0"
      class="approval-flow__steps"
      :active="activeStep"
      finish-status="success"
      align-center
    >
      <el-step
        v-for="step in steps"
        :key="step"
        :title="stepName(step)"
        :status="stepStatus(step)"
      />
    </el-steps>

    <AuditTimeline
      :items="timelineItems"
      empty-description="暂无审批记录"
      empty-action-text="刷新审批流"
      operator-label="审批人"
      @refresh="reload"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from '@/api/client'
import AuditTimeline from '@/components/AuditTimeline.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { AuditTimelineItem, StatusTone } from '@/components/types'
import type {
  ApprovalRecord,
  ApprovalState,
  ApprovalStateResponse,
} from '@/components/business/types'

const props = withDefaults(
  defineProps<{
    docType?: string
    docId?: number | string
    flowName?: string
    state?: ApprovalState
    records?: ApprovalRecord[]
    load?: () => Promise<ApprovalStateResponse>
    autoLoad?: boolean
  }>(),
  {
    records: () => [],
    autoLoad: true,
  },
)

const emit = defineEmits<{
  loaded: [data: ApprovalStateResponse]
  error: [error: unknown]
}>()

const loadedData = ref<ApprovalStateResponse | null>(null)
const loading = ref(false)

const emptyState: ApprovalState = {
  status: 'UNKNOWN',
  current_step: null,
  total_steps: 0,
  approved_steps: [],
}

const displayState = computed(
  () => props.state || loadedData.value?.state || emptyState,
)
const displayRecords = computed(
  () =>
    props.records?.length
      ? props.records
      : loadedData.value?.records || [],
)
const displayFlowName = computed(
  () => props.flowName || loadedData.value?.flow_name || '',
)
const steps = computed(() =>
  Array.from({ length: displayState.value.total_steps }, (_, index) => index + 1),
)
const approvalStatusLabel = computed(() => {
  const labels: Record<string, string> = {
    NOT_SUBMITTED: '未提交',
    PENDING: '审批中',
    APPROVED: '已通过',
    REJECTED: '已驳回',
    UNKNOWN: '未知',
  }
  return labels[String(displayState.value.status)] || String(displayState.value.status)
})
const approvalStatusTone = computed<StatusTone>(() => {
  const tones: Record<string, StatusTone> = {
    NOT_SUBMITTED: 'info',
    PENDING: 'warning',
    APPROVED: 'success',
    REJECTED: 'danger',
    UNKNOWN: 'info',
  }
  return tones[String(displayState.value.status)] || 'info'
})
const activeStep = computed(() => {
  const status = String(displayState.value.status)
  if (status === 'APPROVED') {
    return displayState.value.total_steps
  }
  if (status === 'NOT_SUBMITTED' || status === 'UNKNOWN') {
    return 0
  }
  return displayState.value.current_step
    ? displayState.value.current_step - 1
    : displayState.value.approved_steps.length
})
const progressText = computed(() => {
  const approved = displayState.value.approved_steps.length
  const current = displayState.value.current_step
  if (String(displayState.value.status) === 'APPROVED') {
    return `共 ${displayState.value.total_steps} 个环节，已全部通过`
  }
  if (current) {
    return `共 ${displayState.value.total_steps} 个环节，当前第 ${current} 个，已通过 ${approved} 个`
  }
  return `共 ${displayState.value.total_steps} 个环节，已通过 ${approved} 个`
})
const rejectedStep = computed(() => {
  const rejected = displayRecords.value
    .filter((record) => record.action === 'REJECT')
    .sort((left, right) => right.step - left.step)
  return rejected[0]?.step || null
})
const timelineItems = computed<AuditTimelineItem[]>(() =>
  displayRecords.value.map((record) => ({
    id: record.id,
    title: recordTitle(record),
    description: recordDescription(record),
    timestamp: record.audit_time || record.created_at,
    operator:
      record.approver_name ||
      (record.approver_id ? `员工 #${record.approver_id}` : undefined),
    status: recordStatus(record),
    statusLabel: recordStatusLabel(record),
    statusTone: recordStatusTone(record),
  })),
)

function recordActionLabel(action: string, status: string): string {
  if (action === 'SUBMIT') {
    return '提交审批'
  }
  if (action === 'APPROVE') {
    return status === 'PENDING' ? '待审批' : '审批通过'
  }
  if (action === 'REJECT') {
    return '审批不通过'
  }
  if (action === 'UNAUDIT') {
    return '弃审'
  }
  return action || '审批操作'
}

function recordTitle(record: ApprovalRecord): string {
  const actionLabel = recordActionLabel(record.action, record.status)
  if (record.action === 'SUBMIT' && record.step === 0) {
    return actionLabel
  }
  return `${record.step_name || `第 ${record.step} 环节`} · ${actionLabel}`
}

function recordDescription(record: ApprovalRecord): string {
  const parts: string[] = []
  if (record.comment) {
    parts.push(`审批意见：${record.comment}`)
  }
  if (record.reason) {
    parts.push(`原因：${record.reason}`)
  }
  if (record.status === 'PENDING') {
    parts.push('等待处理')
  }
  if (record.status === 'INVALID') {
    parts.push('该环节已因弃审失效')
  }
  return parts.join('；')
}

function recordStatus(record: ApprovalRecord): string {
  if (record.status === 'PENDING') {
    return 'PENDING'
  }
  if (record.status === 'REJECTED') {
    return 'REJECTED'
  }
  if (record.status === 'APPROVED' || record.status === 'DONE') {
    return 'APPROVED'
  }
  return record.status
}

function recordStatusLabel(record: ApprovalRecord): string {
  if (record.action === 'UNAUDIT') {
    return '已弃审'
  }
  if (record.action === 'REJECT') {
    return '不通过'
  }
  if (record.status === 'PENDING') {
    return '待处理'
  }
  if (record.status === 'REJECTED') {
    return '不通过'
  }
  if (record.status === 'APPROVED') {
    return '已通过'
  }
  if (record.status === 'DONE') {
    return record.action === 'SUBMIT' ? '已提交' : '已完成'
  }
  if (record.status === 'INVALID') {
    return '已失效'
  }
  return record.status
}

function recordStatusTone(record: ApprovalRecord): StatusTone {
  if (record.status === 'PENDING') {
    return 'warning'
  }
  if (record.status === 'REJECTED') {
    return 'danger'
  }
  if (record.status === 'INVALID') {
    return 'info'
  }
  if (record.action === 'UNAUDIT') {
    return 'warning'
  }
  return 'success'
}

function stepName(step: number): string {
  return (
    displayRecords.value.find(
      (record) => record.step === step && record.step_name,
    )?.step_name || `第 ${step} 环节`
  )
}

function stepStatus(
  step: number,
): 'wait' | 'process' | 'success' | 'error' {
  if (displayState.value.approved_steps.includes(step)) {
    return 'success'
  }
  if (String(displayState.value.status) === 'REJECTED' && rejectedStep.value === step) {
    return 'error'
  }
  if (
    String(displayState.value.status) === 'PENDING' &&
    displayState.value.current_step === step
  ) {
    return 'process'
  }
  return 'wait'
}

async function loadData(): Promise<void> {
  if (loading.value) {
    return
  }
  if (!props.load && (!props.docType || props.docId === undefined)) {
    return
  }

  loading.value = true
  try {
    const data = props.load
      ? await props.load()
      : await api.get<ApprovalStateResponse>(
          `/approval/records/${encodeURIComponent(String(props.docType))}/${encodeURIComponent(String(props.docId))}`,
        )
    loadedData.value = data
    emit('loaded', data)
  } catch (error) {
    emit('error', error)
    ElMessage.error(error instanceof Error ? error.message : '审批流加载失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  void loadData()
}

watch(
  () => [props.docType, props.docId, props.load],
  () => {
    if (props.autoLoad && !props.state && !props.records?.length) {
      void loadData()
    }
  },
  { immediate: true },
)

defineExpose({
  reload,
})
</script>

<style scoped>
.approval-flow {
  min-height: 180px;
}

.approval-flow__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.approval-flow__header > div {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.approval-flow__progress {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.approval-flow__steps {
  margin-bottom: 24px;
}

@media (max-width: 640px) {
  .approval-flow__header,
  .approval-flow__header > div {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
