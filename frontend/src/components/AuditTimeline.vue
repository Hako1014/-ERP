<template>
  <el-timeline v-if="items.length" class="audit-timeline">
    <el-timeline-item
      v-for="(item, index) in items"
      :key="item.id ?? `${item.title}-${index}`"
      :timestamp="formatDateTime(item.timestamp)"
      :type="timelineTone(item)"
      placement="top"
    >
      <div class="audit-timeline__header">
        <strong>{{ item.title }}</strong>
        <StatusTag
          v-if="item.status"
          :status="item.status"
          :label="item.statusLabel"
          :tone="item.statusTone"
        />
      </div>
      <div v-if="item.operator" class="audit-timeline__operator">
        {{ operatorLabel }}：{{ item.operator }}
      </div>
      <p v-if="item.description" class="audit-timeline__description">
        {{ item.description }}
      </p>
      <slot name="item" :item="item" :index="index" />
    </el-timeline-item>
  </el-timeline>

  <EmptyState
    v-else
    :description="emptyDescription"
    :action-text="emptyActionText"
    @action="emit('refresh')"
  />
</template>

<script setup lang="ts">
import { resolveStatusMeta } from '@/components/status'
import EmptyState from '@/components/EmptyState.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { AuditTimelineItem, StatusTone } from '@/components/types'
import { formatDateTime } from '@/utils/datetime'

withDefaults(
  defineProps<{
    items: AuditTimelineItem[]
    emptyDescription?: string
    emptyActionText?: string
    operatorLabel?: string
  }>(),
  {
    emptyDescription: '暂无操作记录',
    emptyActionText: '刷新记录',
    operatorLabel: '操作人',
  },
)

const emit = defineEmits<{
  refresh: []
}>()

function timelineTone(item: AuditTimelineItem): StatusTone {
  return item.statusTone || resolveStatusMeta(item.status).tone
}
</script>

<style scoped>
.audit-timeline {
  padding: 8px 4px 0;
}

.audit-timeline__header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
}

.audit-timeline__operator {
  margin-top: 5px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.audit-timeline__description {
  margin: 6px 0 0;
  color: var(--el-text-color-regular);
  line-height: 1.6;
  word-break: break-word;
}
</style>
