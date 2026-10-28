<template>
  <el-tag
    class="status-tag"
    :type="meta.tone"
    :aria-label="`${text}，状态编码 ${normalizedStatus || '未知'}`"
  >
    <el-icon v-if="showIcon" class="status-tag__icon">
      <component :is="meta.icon" />
    </el-icon>
    <span>{{ text }}</span>
  </el-tag>
</template>

<script setup lang="ts">
import { computed, type Component } from 'vue'

import { resolveStatusMeta, statusLabel } from '@/components/status'
import type { StatusTone } from '@/components/types'

const props = withDefaults(
  defineProps<{
    status?: string
    label?: string
    tone?: StatusTone
    icon?: Component
    showIcon?: boolean
  }>(),
  {
    status: '',
    showIcon: true,
  },
)

const normalizedStatus = computed(() =>
  String(props.status || '').trim().toUpperCase(),
)
const meta = computed(() =>
  resolveStatusMeta(props.status, props.tone, props.icon),
)
const text = computed(() =>
  statusLabel(props.status, props.label, props.tone, props.icon),
)
</script>

<style scoped>
.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  vertical-align: middle;
}

.status-tag__icon {
  flex: 0 0 auto;
}
</style>
