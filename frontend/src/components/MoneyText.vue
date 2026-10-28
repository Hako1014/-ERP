<template>
  <span
    class="money-text"
    :class="{ 'is-negative': negative, 'is-empty': display === fallback }"
    :title="rawTitle"
  >
    {{ display }}
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { formatMoneyText } from '@/components/money'

const props = withDefaults(
  defineProps<{
    value: string | number | bigint | null | undefined
    precision?: number
    symbol?: string
    showSymbol?: boolean
    fallback?: string
  }>(),
  {
    precision: 2,
    symbol: '¥',
    showSymbol: true,
    fallback: '--',
  },
)

const display = computed(() =>
  formatMoneyText(props.value, {
    precision: props.precision,
    symbol: props.symbol,
    showSymbol: props.showSymbol,
    fallback: props.fallback,
  }),
)
const negative = computed(() => String(props.value ?? '').trim().startsWith('-'))
const rawTitle = computed(() =>
  props.value === null || props.value === undefined ? '' : String(props.value),
)
</script>

<style scoped>
.money-text {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.money-text.is-negative {
  color: var(--el-color-danger);
}

.money-text.is-empty {
  color: var(--el-text-color-placeholder);
}
</style>
