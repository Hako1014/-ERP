<template>
  <section class="empty-state">
    <el-empty :description="description" :image-size="imageSize">
      <template #image>
        <el-icon class="empty-state__icon" :size="imageSize * 0.6">
          <component :is="icon" />
        </el-icon>
      </template>
      <el-button
        v-if="showAction"
        type="primary"
        @click="emit('action')"
      >
        {{ actionText }}
      </el-button>
    </el-empty>
  </section>
</template>

<script setup lang="ts">
import { Box } from '@element-plus/icons-vue'
import type { Component } from 'vue'

withDefaults(
  defineProps<{
    description?: string
    actionText?: string
    icon?: Component
    imageSize?: number
    showAction?: boolean
  }>(),
  {
    description: '暂无数据',
    actionText: '重置筛选',
    icon: Box,
    imageSize: 80,
    showAction: true,
  },
)

const emit = defineEmits<{
  action: []
}>()
</script>

<style scoped>
.empty-state__icon {
  color: var(--el-text-color-placeholder);
}
</style>
