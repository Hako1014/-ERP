import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BaseChart from '@/components/charts/BaseChart.vue'
import BrandSalesPieChart from '@/components/charts/BrandSalesPieChart.vue'
import CategorySalesChart from '@/components/charts/CategorySalesChart.vue'
import SalesTrendChart from '@/components/charts/SalesTrendChart.vue'
import {
  buildBrandSalesPieOption,
  buildCategorySalesOption,
  buildSalesTrendOption,
} from '@/components/charts/options'
import MetricCard from '@/components/MetricCard.vue'

const echartsMock = vi.hoisted(() => {
  const chart = {
    dispose: vi.fn(),
    resize: vi.fn(),
    setOption: vi.fn(),
  }
  return {
    chart,
    init: vi.fn(() => chart),
  }
})

vi.mock('@/components/charts/echarts', () => ({
  echarts: {
    init: echartsMock.init,
  },
}))

beforeEach(() => {
  echartsMock.init.mockClear()
  echartsMock.chart.dispose.mockClear()
  echartsMock.chart.resize.mockClear()
  echartsMock.chart.setOption.mockClear()
})

describe('dashboard metric card', () => {
  it('renders the value, comparison, and visible metric definition', () => {
    const wrapper = mount(MetricCard, {
      props: {
        metric: {
          key: 'today_order_amount',
          label: '今日订单金额',
          value: '123456.78',
          previous_value: '100000.00',
          change_rate: '23.46',
          unit: 'CNY',
        },
      },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('今日订单金额')
    expect(wrapper.text()).toContain('¥123,456.78')
    expect(wrapper.text()).toContain('+23.46%')
    expect(wrapper.text()).toContain('较昨日')
    expect(wrapper.text()).toContain('口径：')
    expect(wrapper.text()).toContain('已付款且未取消的订单金额')
  })

  it('shows an explicit empty comparison when no previous period exists', () => {
    const wrapper = mount(MetricCard, {
      props: {
        metric: {
          key: 'stock_warning_count',
          label: '库存预警',
          value: 8,
          previous_value: null,
          change_rate: null,
          unit: 'SKU',
        },
      },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('8')
    expect(wrapper.text()).toContain('SKU')
    expect(wrapper.text()).toContain('暂无环比数据')
    expect(wrapper.text()).toContain('低于库存预警阈值')
  })
})

describe('chart option builders', () => {
  it('builds a daily, weekly, or monthly sales trend option', () => {
    const option = buildSalesTrendOption(
      [
        {
          period: '2026-W36',
          start_date: '2026-08-31',
          end_date: '2026-09-06',
          amount: '1234.50',
          order_count: 12,
        },
        {
          period: '2026-W37',
          start_date: '2026-09-07',
          end_date: '2026-09-13',
          amount: '0',
          order_count: 0,
        },
      ],
      'week',
    )
    const series = option.series as Array<{
      type: string
      data: unknown[]
    }>

    expect(series.map((item) => item.type)).toEqual(['bar', 'line'])
    expect(series[0].data).toEqual([1234.5, 0])
    expect(series[1].data).toEqual([12, 0])
    expect((option.xAxis as { data: string[] }).data).toEqual([
      '2026-W36',
      '2026-W37',
    ])
  })

  it('builds category bars and brand share data from decimal strings', () => {
    const items = [
      {
        name: '手机',
        amount: '120.50',
        qty: '2.000',
        ratio: '60.25',
      },
      {
        name: '配件',
        amount: '80.00',
        qty: '4.000',
        ratio: '39.75',
      },
    ]
    const categoryOption = buildCategorySalesOption(items)
    const categorySeries = categoryOption.series as Array<{
      type: string
      data: Array<{ value: number; name: string }>
    }>
    const pieOption = buildBrandSalesPieOption(items)
    const pieSeries = pieOption.series as Array<{
      type: string
      data: Array<{ value: number; name: string }>
    }>

    expect(categorySeries[0].type).toBe('bar')
    expect(categorySeries[0].data.map((item) => item.value)).toEqual([
      80, 120.5,
    ])
    expect(pieSeries[0].type).toBe('pie')
    expect(pieSeries[0].data.map((item) => item.name)).toEqual([
      '手机',
      '配件',
    ])
  })
})

describe('chart components', () => {
  it('initializes and disposes an ECharts instance with the supplied option', async () => {
    const wrapper = mount(BaseChart, {
      props: {
        option: { series: [] },
        ariaLabel: '测试图表',
      },
    })

    expect(echartsMock.init).toHaveBeenCalledTimes(1)
    expect(echartsMock.chart.setOption).toHaveBeenCalledWith(
      { series: [] },
      { notMerge: true },
    )

    await wrapper.setProps({ option: { series: [{ type: 'bar' }] } })
    expect(echartsMock.chart.setOption).toHaveBeenLastCalledWith(
      { series: [{ type: 'bar' }] },
      { notMerge: true },
    )

    ;(wrapper.vm as unknown as { resize: () => void }).resize()
    expect(echartsMock.chart.resize).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    expect(echartsMock.chart.dispose).toHaveBeenCalledTimes(1)
  })

  it('renders the three dashboard chart widgets and period controls', () => {
    const trendItems = [
      {
        period: '2026-09-24',
        start_date: '2026-09-24',
        end_date: '2026-09-24',
        amount: '100.00',
        order_count: 2,
      },
    ]
    const dimensionItems = [
      {
        name: '手机',
        amount: '100.00',
        qty: '2.000',
        ratio: '100.00',
      },
    ]

    const trend = mount(SalesTrendChart, {
      props: {
        items: trendItems,
        period: 'day',
      },
      global: { plugins: [ElementPlus] },
    })
    const category = mount(CategorySalesChart, {
      props: { items: dimensionItems },
      global: { plugins: [ElementPlus] },
    })
    const brand = mount(BrandSalesPieChart, {
      props: { items: dimensionItems },
      global: { plugins: [ElementPlus] },
    })

    expect(trend.text()).toContain('销售趋势')
    expect(trend.text()).toContain('日')
    expect(trend.text()).toContain('周')
    expect(trend.text()).toContain('月')
    expect(category.text()).toContain('分类销售')
    expect(brand.text()).toContain('品牌销售占比')
    expect(echartsMock.init).toHaveBeenCalledTimes(3)
  })

  it('shows the chart empty state without initializing ECharts', () => {
    const wrapper = mount(CategorySalesChart, {
      props: { items: [] },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('暂无图表数据')
    expect(echartsMock.init).not.toHaveBeenCalled()
  })
})
