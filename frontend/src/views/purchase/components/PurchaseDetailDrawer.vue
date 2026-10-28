<template>
  <el-drawer
    v-model="visible"
    :title="title"
    size="min(980px, 96vw)"
    destroy-on-close
  >
    <div v-loading="loading" class="purchase-detail">
      <template v-if="document">
        <div class="purchase-detail__header">
          <div>
            <h2>{{ documentNo }}</h2>
            <p class="purchase-muted">
              制单时间：{{ formatDateTime(document.created_at) }}
            </p>
          </div>
          <div class="purchase-detail__status">
            <StatusTag
              :status="document.status"
              :label="statusLabel(document.status)"
              :tone="statusTone(document.status)"
            />
            <StatusTag
              :status="document.audit_status"
              :label="auditLabel(document.audit_status)"
              :tone="statusTone(document.audit_status)"
            />
          </div>
        </div>

        <el-descriptions :column="2" border>
          <slot name="summary" />
        </el-descriptions>

        <slot name="items" />

        <section class="purchase-detail-section">
          <h3 class="purchase-detail-section__title">审批流程</h3>
          <ApprovalFlow
            v-if="document.approval"
            :state="document.approval.state"
            :records="document.approval.records"
            :flow-name="document.approval.flow_name || ''"
          />
          <el-empty v-else description="暂无审批流程" :image-size="72" />
        </section>

        <slot name="footer" />
      </template>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import StatusTag from '@/components/StatusTag.vue'
import { formatDateTime } from '@/utils/datetime'
import {
  AUDIT_STATUS_LABELS,
  PURCHASE_STATUS_LABELS,
  labelOf,
  statusTone,
} from '@/views/purchase/purchaseHelpers'

interface PurchaseDocument {
  status: string
  audit_status: string
  created_at?: string | null
  approval?: {
    flow_name?: string | null
    state: {
      status: string
      current_step: number | null
      total_steps: number
      approved_steps: number[]
    }
    records: Array<{
      id: number
      step: number
      step_name?: string | null
      action: string
      status: string
      approver_id?: number | null
      approver_name?: string | null
      comment?: string | null
      reason?: string | null
      audit_time?: string | null
      created_at?: string | null
    }>
  }
  [key: string]: unknown
}

const visible = defineModel<boolean>('visible', { default: false })

const props = withDefaults(
  defineProps<{
    title: string
    document?: PurchaseDocument | null
    documentNo?: string
    loading?: boolean
  }>(),
  {
    document: null,
    documentNo: '--',
    loading: false,
  },
)

const document = computed(() => props.document)
const documentNo = computed(
  () => props.documentNo || String(props.document?.id || '--'),
)

function statusLabel(code?: string | null): string {
  return labelOf(PURCHASE_STATUS_LABELS, code)
}

function auditLabel(code?: string | null): string {
  return labelOf(AUDIT_STATUS_LABELS, code)
}
</script>

<style scoped>
.purchase-detail {
  min-height: 260px;
}

.purchase-detail__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.purchase-detail__header h2 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 20px;
}

.purchase-detail__header p {
  margin: 6px 0 0;
}

.purchase-detail__status {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

@media (max-width: 640px) {
  .purchase-detail__header {
    flex-direction: column;
  }

  .purchase-detail__status {
    justify-content: flex-start;
  }
}
</style>
