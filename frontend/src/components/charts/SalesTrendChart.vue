<template>
  <section class="sales-trend-chart">
    <header class="sales-trend-chart__header">
      <div class="sales-trend-chart__heading">
        <h3>{{ title }}</h3>
        <span v-if="dateRangeText">{{ dateRangeText }}</span>
      </div>
      <el-radio-group
        v-if="showPeriodSwitch"
        v-model="period"
        size="small"
        aria-label="销售趋势统计粒度"
      >
        <el-radio-button value="day">日</el-radio-button>
        <el-radio-button value="week">周</el-radio-button>
        <el-radio-button value="month">月</el-radio-button>
      </el-radio-group>
    </header>
    <BaseChart
      :option="option"
      :height="height"
      :empty="!items.length"
      :aria-label="`${title}，${periodLabel}粒度`"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'

import BaseChart from '@/components/charts/BaseChart.vue'
import { buildSalesTrendOption } from '@/components/charts/options'
import type {
  SalesTrendItem,
  TrendPeriod,
} from '@/components/charts/types'

const props = withDefaults(
  defineProps<{
    items: readonly SalesTrendItem[]
    title?: string
    height?: string
    showPeriodSwitch?: boolean
    startDate?: string
    endDate?: string
  }>(),
  {
    title: '销售趋势',
    height: '320px',
    showPeriodSwitch: true,
  },
)

const emit = defineEmits<{
  'period-change': [period: TrendPeriod]
}>()

const period = defineModel<TrendPeriod>('period', { default: 'day' })
const option = computed(() =>
  buildSalesTrendOption(props.items, period.value),
)
const periodLabel = computed(() => {
  const labels: Record<TrendPeriod, string> = {
    day: '日',
    week: '周',
    month: '月',
  }
  return labels[period.value]
})
const dateRangeText = computed(() => {
  if (!props.startDate || !props.endDate) {
    return ''
  }
  return `${props.startDate} 至 ${props.endDate}`
})

watch(period, (value) => {
  emit('period-change', value)
})
</script>

<style scoped>
.sales-trend-chart {
  min-width: 0;
}

.sales-trend-chart__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.sales-trend-chart__heading {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.sales-trend-chart h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 16px;
  font-weight: 600;
}

.sales-trend-chart__heading span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

@media (max-width: 640px) {
  .sales-trend-chart__header {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
