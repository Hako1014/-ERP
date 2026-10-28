<template>
  <article class="metric-card">
    <div class="metric-card__header">
      <span class="metric-card__label">{{ metric.label }}</span>
    </div>

    <div class="metric-card__value">
      <MoneyText
        v-if="isMoneyMetric"
        :value="metric.value"
        :precision="2"
      />
      <template v-else>
        <span class="metric-card__number">{{ valueText }}</span>
        <span class="metric-card__unit">{{ unitText }}</span>
      </template>
    </div>

    <div class="metric-card__comparison">
      <span class="metric-card__change" :class="`is-${changeTone}`">
        <el-icon v-if="changeIcon">
          <component :is="changeIcon" />
        </el-icon>
        {{ changeText }}
      </span>
      <span>{{ comparisonLabel }}</span>
    </div>

    <p class="metric-card__definition">
      <span>口径：</span>{{ definition }}
    </p>
  </article>
</template>

<script setup lang="ts">
import { Bottom, Minus, Top } from '@element-plus/icons-vue'
import { computed, type Component } from 'vue'

import MoneyText from '@/components/MoneyText.vue'
import type { DashboardMetric } from '@/components/charts/types'

const props = defineProps<{
  metric: DashboardMetric
}>()

const METRIC_DEFINITIONS: Record<string, string> = {
  today_order_amount:
    '已付款且未取消的订单金额，按付款时间统计；缺失付款时间时按下单时间。',
  today_order_count:
    '已付款且未取消的订单数量，按付款时间统计；缺失付款时间时按下单时间。',
  stock_warning_count: '当前低于库存预警阈值的 SKU 数量。',
  customer_growth: '当天新增客户数量。',
}

const UNIT_LABELS: Record<string, string> = {
  CNY: '元',
  ORDER: '单',
  SKU: 'SKU',
  CUSTOMER: '人',
}

const isMoneyMetric = computed(() => props.metric.unit === 'CNY')
const valueText = computed(() =>
  props.metric.value === null || props.metric.value === undefined
    ? '--'
    : String(props.metric.value),
)
const unitText = computed(
  () => UNIT_LABELS[String(props.metric.unit)] || String(props.metric.unit),
)
const definition = computed(
  () =>
    props.metric.description ||
    METRIC_DEFINITIONS[props.metric.key] ||
    '当前指标口径待补充。',
)
const comparisonLabel = computed(
  () =>
    props.metric.comparison_label ||
    (props.metric.key.startsWith('today_') ? '较昨日' : '较上一周期'),
)
const changeTone = computed<'up' | 'down' | 'flat' | 'empty'>(() => {
  const value = props.metric.change_rate
  if (value === null || value === undefined || value === '') {
    return 'empty'
  }
  const text = String(value).trim()
  if (text.startsWith('-')) {
    return 'down'
  }
  if (/^0*(?:\.0+)?$/.test(text)) {
    return 'flat'
  }
  return 'up'
})
const changeIcon = computed<Component | null>(() => {
  if (changeTone.value === 'up') {
    return Top
  }
  if (changeTone.value === 'down') {
    return Bottom
  }
  if (changeTone.value === 'flat') {
    return Minus
  }
  return null
})
const changeText = computed(() => {
  const value = props.metric.change_rate
  if (value === null || value === undefined || value === '') {
    return '暂无环比数据'
  }
  const text = String(value).trim()
  const percentage = text.endsWith('%') ? text : `${text}%`
  return changeTone.value === 'up' && !percentage.startsWith('+')
    ? `+${percentage}`
    : percentage
})
</script>

<style scoped>
.metric-card {
  min-width: 0;
  padding: 18px 18px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}

.metric-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.metric-card__label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.metric-card__value {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-height: 42px;
  margin-top: 10px;
  color: var(--el-text-color-primary);
}

.metric-card__value :deep(.money-text) {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.2;
}

.metric-card__number {
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.metric-card__unit {
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.metric-card__comparison {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 22px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.metric-card__change {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-variant-numeric: tabular-nums;
}

.metric-card__change.is-up {
  color: var(--el-color-danger);
}

.metric-card__change.is-down {
  color: var(--el-color-success);
}

.metric-card__change.is-flat,
.metric-card__change.is-empty {
  color: var(--el-text-color-secondary);
}

.metric-card__definition {
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px solid var(--el-border-color-extra-light);
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.55;
}

.metric-card__definition span {
  color: var(--el-text-color-regular);
  font-weight: 600;
}
</style>
