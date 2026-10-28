<template>
  <section class="brand-sales-pie-chart">
    <header class="brand-sales-pie-chart__header">
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
import { buildBrandSalesPieOption } from '@/components/charts/options'
import type { DimensionSalesItem } from '@/components/charts/types'

const props = withDefaults(
  defineProps<{
    items: readonly DimensionSalesItem[]
    title?: string
    height?: string
    periodText?: string
  }>(),
  {
    title: '品牌销售占比',
    height: '320px',
  },
)

const option = computed(() => buildBrandSalesPieOption(props.items))
</script>

<style scoped>
.brand-sales-pie-chart {
  min-width: 0;
}

.brand-sales-pie-chart__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.brand-sales-pie-chart h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 16px;
  font-weight: 600;
}

.brand-sales-pie-chart__header span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
