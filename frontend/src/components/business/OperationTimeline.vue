<template>
  <div v-loading="loading" class="operation-timeline">
    <AuditTimeline
      :items="timelineItems"
      empty-description="暂无操作留痕"
      empty-action-text="刷新留痕"
      @refresh="reload"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import AuditTimeline from '@/components/AuditTimeline.vue'
import type { AuditTimelineItem, StatusTone } from '@/components/types'
import type { OperationLogItem } from '@/components/business/types'

const props = withDefaults(
  defineProps<{
    items?: OperationLogItem[]
    load?: () => Promise<OperationLogItem[]>
    autoLoad?: boolean
  }>(),
  {
    items: () => [],
    autoLoad: true,
  },
)

const emit = defineEmits<{
  loaded: [items: OperationLogItem[]]
  error: [error: unknown]
  refresh: []
}>()

const loadedItems = ref<OperationLogItem[]>([])
const loading = ref(false)
const sourceItems = computed(() => props.items.length ? props.items : loadedItems.value)
const timelineItems = computed<AuditTimelineItem[]>(() =>
  sourceItems.value.map((item, index) => ({
    id: item.id ?? `${item.action}-${item.created_at ?? index}`,
    title: operationLabel(item.action),
    description: operationDescription(item),
    timestamp: item.created_at,
    operator: item.actor_name || '系统',
    status: item.action,
    statusLabel: operationLabel(item.action),
    statusTone: operationTone(item.action),
  })),
)

const ACTION_LABELS: Record<string, string> = {
  CREATE: '新增',
  UPDATE: '修改',
  DELETE: '删除',
  SUBMIT: '提交',
  APPROVE: '审核通过',
  REJECT: '审核不通过',
  UNAUDIT: '弃审',
  AUDIT: '审核',
  IMPORT: '导入',
  EXPORT: '导出',
  PRINT: '打印',
  SYNC: '同步',
}

function operationLabel(action: string): string {
  return ACTION_LABELS[action] || action || '操作'
}

function operationTone(action: string): StatusTone {
  if (action === 'DELETE' || action === 'REJECT') {
    return 'danger'
  }
  if (action === 'UNAUDIT') {
    return 'warning'
  }
  if (action === 'CREATE' || action === 'APPROVE') {
    return 'success'
  }
  return 'primary'
}

function operationDescription(item: OperationLogItem): string {
  const parts: string[] = []
  if (item.summary) {
    parts.push(item.summary)
  }
  if (item.reason) {
    parts.push(`原因：${item.reason}`)
  }
  if (item.target_type || item.target_id) {
    parts.push(
      `对象：${item.target_type || '单据'}${item.target_id ? ` #${item.target_id}` : ''}`,
    )
  }
  return parts.join('；')
}

async function loadData(): Promise<void> {
  if (!props.load) {
    emit('refresh')
    return
  }
  if (loading.value) {
    return
  }

  loading.value = true
  try {
    const items = await props.load()
    loadedItems.value = items
    emit('loaded', items)
  } catch (error) {
    emit('error', error)
    ElMessage.error(error instanceof Error ? error.message : '操作留痕加载失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  void loadData()
}

watch(
  () => props.load,
  () => {
    if (props.autoLoad && props.load && !props.items.length) {
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
.operation-timeline {
  min-height: 120px;
}
</style>
