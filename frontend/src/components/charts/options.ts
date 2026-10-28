import { formatMoneyText } from '@/components/money'
import type { EChartsCoreOption } from '@/components/charts/echarts'
import type {
  DimensionSalesItem,
  SalesTrendItem,
  TrendPeriod,
} from '@/components/charts/types'

const CHART_PALETTE = [
  '#2563eb',
  '#0f9f76',
  '#d97706',
  '#dc4c64',
  '#7c3aed',
  '#0891b2',
  '#65a30d',
  '#64748b',
]

interface TooltipItem {
  axisValue?: string
  seriesName?: string
  value?: unknown
  data?: {
    amount?: unknown
    name?: string
    qty?: unknown
    ratio?: unknown
  }
}

export function buildSalesTrendOption(
  items: readonly SalesTrendItem[],
  period: TrendPeriod,
): EChartsCoreOption {
  return {
    animationDuration: 320,
    color: [CHART_PALETTE[0], CHART_PALETTE[1]],
    grid: {
      top: 28,
      right: 58,
      bottom: 42,
      left: 68,
      containLabel: false,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow',
      },
      formatter: (params: unknown) => {
        const entries = tooltipItems(params)
        if (!entries.length) {
          return ''
        }
        const lines = [
          `<strong>${escapeHtml(entries[0].axisValue || '')}</strong>`,
        ]
        entries.forEach((item) => {
          const value =
            item.seriesName === '销售金额'
              ? formatTooltipMoney(item.value)
              : `${formatInteger(item.value)} 单`
          lines.push(
            `${item.seriesName || ''}：${escapeHtml(value)}`,
          )
        })
        return lines.join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      boundaryGap: true,
      data: items.map((item) => item.period),
      axisLabel: {
        color: '#64748b',
        formatter: (value: unknown) =>
          formatPeriodLabel(period, String(value)),
      },
      axisLine: {
        lineStyle: {
          color: '#d7dee8',
        },
      },
    },
    yAxis: [
      {
        type: 'value',
        name: '金额',
        min: 0,
        axisLabel: {
          color: '#64748b',
          formatter: (value: unknown) => formatAxisMoney(value),
        },
        splitLine: {
          lineStyle: {
            color: '#edf1f5',
          },
        },
      },
      {
        type: 'value',
        name: '订单数',
        min: 0,
        minInterval: 1,
        axisLabel: {
          color: '#64748b',
        },
        splitLine: {
          show: false,
        },
      },
    ],
    series: [
      {
        name: '销售金额',
        type: 'bar',
        barMaxWidth: 24,
        data: items.map((item) => chartNumber(item.amount)),
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
        },
        emphasis: {
          focus: 'series',
        },
      },
      {
        name: '订单数',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        showSymbol: false,
        symbolSize: 6,
        data: items.map((item) => Math.max(0, Math.trunc(item.order_count))),
        lineStyle: {
          width: 2,
        },
        emphasis: {
          focus: 'series',
        },
      },
    ],
  }
}

export function buildCategorySalesOption(
  items: readonly DimensionSalesItem[],
): EChartsCoreOption {
  const displayItems = items.slice().reverse()
  return {
    animationDuration: 320,
    color: CHART_PALETTE,
    grid: {
      top: 18,
      right: 22,
      bottom: 26,
      left: 24,
      containLabel: true,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow',
      },
      formatter: (params: unknown) => {
        const item = tooltipItems(params)[0]
        if (!item) {
          return ''
        }
        const source = item.data
        return [
          `<strong>${escapeHtml(source?.name || '')}</strong>`,
          `销售金额：${escapeHtml(formatTooltipMoney(source?.amount))}`,
          `销售数量：${escapeHtml(formatQuantity(source?.qty))}`,
          `销售占比：${escapeHtml(formatPercentage(source?.ratio))}`,
        ].join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      min: 0,
      axisLabel: {
        color: '#64748b',
        formatter: (value: unknown) => formatAxisMoney(value),
      },
      splitLine: {
        lineStyle: {
          color: '#edf1f5',
        },
      },
    },
    yAxis: {
      type: 'category',
      data: displayItems.map((item) => item.name),
      axisLabel: {
        color: '#334155',
        width: 96,
        overflow: 'truncate',
      },
      axisLine: {
        show: false,
      },
      axisTick: {
        show: false,
      },
    },
    series: [
      {
        name: '分类销售',
        type: 'bar',
        barMaxWidth: 22,
        colorBy: 'data',
        data: displayItems.map((item) => ({
          value: chartNumber(item.amount),
          amount: item.amount,
          qty: item.qty,
          ratio: item.ratio,
          name: item.name,
        })),
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
        },
        emphasis: {
          focus: 'series',
        },
      },
    ],
  }
}

export function buildBrandSalesPieOption(
  items: readonly DimensionSalesItem[],
): EChartsCoreOption {
  return {
    animationDuration: 320,
    color: CHART_PALETTE,
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const item = tooltipItems(params)[0]
        if (!item) {
          return ''
        }
        const source = item.data
        return [
          `<strong>${escapeHtml(source?.name || '')}</strong>`,
          `销售金额：${escapeHtml(formatTooltipMoney(source?.amount))}`,
          `销售数量：${escapeHtml(formatQuantity(source?.qty))}`,
          `销售占比：${escapeHtml(formatPercentage(source?.ratio))}`,
        ].join('<br/>')
      },
    },
    legend: {
      type: 'scroll',
      bottom: 0,
      icon: 'circle',
      itemWidth: 9,
      itemHeight: 9,
      textStyle: {
        color: '#475569',
      },
    },
    series: [
      {
        name: '品牌销售',
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderColor: '#ffffff',
          borderWidth: 2,
          borderRadius: 3,
        },
        label: {
          color: '#334155',
          formatter: '{b}\n{d}%',
        },
        labelLine: {
          length: 10,
          length2: 8,
        },
        data: items.map((item) => ({
          value: chartNumber(item.amount),
          amount: item.amount,
          qty: item.qty,
          ratio: item.ratio,
          name: item.name,
        })),
      },
    ],
  }
}

function chartNumber(value: unknown): number {
  if (value === null || value === undefined || value === '') {
    return 0
  }
  const parsed = Number(String(value).replace(/,/g, ''))
  return Number.isFinite(parsed) ? parsed : 0
}

function tooltipItems(params: unknown): TooltipItem[] {
  if (Array.isArray(params)) {
    return params as TooltipItem[]
  }
  if (params && typeof params === 'object') {
    return [params as TooltipItem]
  }
  return []
}

function formatPeriodLabel(period: TrendPeriod, value: string): string {
  if (period === 'day' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value.slice(5)
  }
  return value
}

function formatAxisMoney(value: unknown): string {
  const parsed = chartNumber(value)
  if (Math.abs(parsed) >= 10_000) {
    return `${(parsed / 10_000).toFixed(1)}万`
  }
  return String(parsed)
}

function formatTooltipMoney(value: unknown): string {
  if (
    value === null ||
    value === undefined ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'bigint'
  ) {
    return formatMoneyText(value)
  }
  return '--'
}

function formatInteger(value: unknown): string {
  const parsed = chartNumber(value)
  return String(Math.max(0, Math.trunc(parsed)))
}

function formatQuantity(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '--'
  }
  const text = String(value).trim()
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) {
    return text
  }
  return text.includes('.')
    ? text.replace(/0+$/, '').replace(/\.$/, '')
    : text
}

function formatPercentage(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '--'
  }
  const text = String(value).trim()
  return text.endsWith('%') ? text : `${text}%`
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
