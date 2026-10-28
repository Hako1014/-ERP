<template>
  <div
    class="base-chart"
    :style="{ height }"
    role="img"
    :aria-label="ariaLabel"
  >
    <div ref="chartElement" class="base-chart__canvas" />
    <div v-if="empty" class="base-chart__empty">
      <span>{{ emptyText }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { echarts } from '@/components/charts/echarts'
import type {
  EChartsCoreOption,
  EChartsType,
} from '@/components/charts/echarts'

const props = withDefaults(
  defineProps<{
    option: EChartsCoreOption
    height?: string
    ariaLabel?: string
    empty?: boolean
    emptyText?: string
    autoResize?: boolean
  }>(),
  {
    height: '320px',
    ariaLabel: '数据图表',
    empty: false,
    emptyText: '暂无图表数据',
    autoResize: true,
  },
)

const chartElement = ref<HTMLDivElement | null>(null)
let chart: EChartsType | null = null
let resizeObserver: ResizeObserver | null = null

function initializeChart(): void {
  if (props.empty || chart || !chartElement.value) {
    return
  }
  chart = echarts.init(chartElement.value)
  chart.setOption(props.option, { notMerge: true })
}

function renderChart(): void {
  if (props.empty) {
    disposeChart()
    return
  }
  initializeChart()
  chart?.setOption(props.option, { notMerge: true })
}

function disposeChart(): void {
  chart?.dispose()
  chart = null
}

function resize(): void {
  chart?.resize()
}

onMounted(() => {
  renderChart()
  if (props.autoResize && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(resize)
    if (chartElement.value) {
      resizeObserver.observe(chartElement.value)
    }
  }
})

watch(
  () => props.option,
  () => {
    renderChart()
  },
  { deep: true },
)

watch(
  () => props.empty,
  () => {
    renderChart()
  },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  disposeChart()
})

defineExpose({
  resize,
  getInstance: () => chart,
})
</script>

<style scoped>
.base-chart {
  position: relative;
  width: 100%;
  min-width: 0;
}

.base-chart__canvas {
  width: 100%;
  height: 100%;
}

.base-chart__empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--el-text-color-placeholder);
  font-size: 14px;
  background:
    linear-gradient(
      180deg,
      rgb(248 250 252 / 36%),
      rgb(248 250 252 / 72%)
    );
}
</style>
