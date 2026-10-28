import { execFileSync } from 'node:child_process'
import { join, resolve } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { expect, it, vi } from 'vitest'

import type { DashboardOverview } from '@/api/dashboard'
import BrandSalesPieChart from '@/components/charts/BrandSalesPieChart.vue'
import CategorySalesChart from '@/components/charts/CategorySalesChart.vue'
import SalesTrendChart from '@/components/charts/SalesTrendChart.vue'
import type { TrendPeriod } from '@/components/charts/types'
import DashboardView from '@/views/dashboard/DashboardView.vue'

const dashboardMock = vi.hoisted(() => ({ overview: vi.fn() }))
vi.mock('@/api/dashboard', () => ({ dashboardApi: dashboardMock }))
vi.mock('@/components/charts/echarts', () => {
  const chart = { dispose: vi.fn(), resize: vi.fn(), setOption: vi.fn() }
  return { echarts: { init: vi.fn(() => chart) } }
})

it.skipIf(process.env.ERP_MYSQL_E2E !== '1')(
  '使用本地 MySQL 的真实接口响应渲染看板并切换趋势粒度',
  async () => {
    const backendPath = resolve(process.cwd(), '..', 'backend')
    const pythonPath = join(
      backendPath,
      process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python',
    )
    const output = execFileSync(pythonPath, ['-m', 'scripts.dashboard_mysql_e2e', '--payload'], {
      cwd: backendPath,
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    const payloadLine = output.split(/\r?\n/).find((line) => line.startsWith('E2E_PAYLOAD='))
    expect(payloadLine).toBeDefined()
    const payloads = JSON.parse(payloadLine!.slice('E2E_PAYLOAD='.length)) as Record<
      TrendPeriod,
      DashboardOverview
    >
    dashboardMock.overview.mockImplementation((period: TrendPeriod) => Promise.resolve(payloads[period]))

    const wrapper = mount(DashboardView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(dashboardMock.overview).toHaveBeenCalledWith('day')
    expect(wrapper.text()).toContain(payloads.day.recent_orders[0].order_no)
    expect(wrapper.findComponent(CategorySalesChart).props('items')).toEqual(payloads.day.category_sales)
    expect(wrapper.findComponent(BrandSalesPieChart).props('items')).toEqual(payloads.day.brand_sales)
    expect(wrapper.text()).toContain(payloads.day.hot_products[0].product_name)

    for (const period of ['week', 'month'] as const) {
      wrapper.findComponent(SalesTrendChart).vm.$emit('update:period', period)
      await flushPromises()
      expect(dashboardMock.overview).toHaveBeenLastCalledWith(period)
      expect(wrapper.findComponent(SalesTrendChart).props('items')).toEqual(
        payloads[period].sales_trend.items,
      )
    }
    wrapper.unmount()
  },
)
