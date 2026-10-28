<template>
  <section class="category-sales-chart">
    <header class="category-sales-chart__header">
      <h3>{{ title }}</h3>
      <span v-if="periodText">{{ periodText }}</span>
    </header>
    <BaseChart
      :option="option"
      :height="height"
      :empty="!items.length"
      :aria-label="title"
    />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import BaseChart from '@/components/charts/BaseChart.vue'
import { buildCategorySalesOption } from '@/components/charts/options'
import type { DimensionSalesItem } from '@/components/charts/types'

const props = withDefaults(
  defineProps<{
    items: readonly DimensionSalesItem[]
    title?: string
    height?: string
    periodText?: string
  }>(),
  {
    title: '分类销售',
    height: '320px',
  },
)

const option = computed(() => buildCategorySalesOption(props.items))
</script>

<style scoped>
.category-sales-chart {
  min-width: 0;
}

.category-sales-chart__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.category-sales-chart h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 16px;
  font-weight: 600;
}

.category-sales-chart__header span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
