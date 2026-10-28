import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const stockMocks = vi.hoisted(() => ({
  stockInApi: {
    approve: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    list: vi.fn(),
    listPutaways: vi.fn(),
    pendingPutaways: vi.fn(),
    putaway: vi.fn(),
    reject: vi.fn(),
    remove: vi.fn(),
    submit: vi.fn(),
    update: vi.fn(),
  },
  stockQueryApi: {
    costs: vi.fn(),
    flows: vi.fn(),
    locations: vi.fn(),
    refreshCosts: vi.fn(),
    refreshWarnings: vi.fn(),
    summary: vi.fn(),
    warnings: vi.fn(),
  },
  warehouseApi: {
    create: vi.fn(),
    createLocation: vi.fn(),
    createZone: vi.fn(),
    disable: vi.fn(),
    disableLocation: vi.fn(),
    enable: vi.fn(),
    enableLocation: vi.fn(),
    generateLocations: vi.fn(),
    list: vi.fn(),
    listLocations: vi.fn(),
    listZones: vi.fn(),
    lock: vi.fn(),
    remove: vi.fn(),
    removeLocation: vi.fn(),
    removeZone: vi.fn(),
    setDefault: vi.fn(),
    unlock: vi.fn(),
    update: vi.fn(),
    updateLocation: vi.fn(),
    updateZone: vi.fn(),
  },
}))

const purchaseMocks = vi.hoisted(() => ({
  purchaseOrderApi: {
    list: vi.fn(),
  },
}))

const productOptionsMock = vi.hoisted(() => ({
  listProducts: vi.fn(),
  listSkus: vi.fn(),
}))

vi.mock('@/api/stock', () => ({
  stockInApi: stockMocks.stockInApi,
  stockQueryApi: stockMocks.stockQueryApi,
  warehouseApi: stockMocks.warehouseApi,
}))
vi.mock('@/api/purchase', () => ({
  purchaseOrderApi: purchaseMocks.purchaseOrderApi,
}))
vi.mock('@/api/productOptions', () => ({
  productOptionsApi: productOptionsMock,
}))

import StockInboundView from '@/views/stock/StockInboundView.vue'
import StockQueryView from '@/views/stock/StockQueryView.vue'
import WarehouseManagementView from '@/views/stock/WarehouseManagementView.vue'
import { menuTree } from '@/router/menu'
import { routes } from '@/router/routes'
import { useAuthStore } from '@/stores/auth'

const wrappers: VueWrapper[] = []

const warehouse = {
  id: 1,
  code: 'DEMO-WH-01',
  name: '演示主仓',
  warehouse_type: 'FINISHED',
  is_default: true,
  sync_stock: false,
  enable_location: true,
  zero_stock_out: false,
  is_virtual: false,
  is_enabled: true,
  lock_type: 'NONE',
  selectable: true,
}

const stockIn = {
  id: 9,
  stock_in_no: 'RK-001',
  warehouse_id: 1,
  warehouse_name: '演示主仓',
  purchase_order_id: null,
  purchase_order_no: null,
  stock_in_type: 'PURCHASE',
  status: 'APPROVED',
  audit_status: 'APPROVED',
  total_qty: '5.000',
  putaway_qty: '0.000',
  pending_putaway_qty: '5.000',
  created_at: '2026-09-24T10:00:00',
}

const stockInDetail = {
  ...stockIn,
  items: [
    {
      id: 21,
      product_id: 1,
      product_code: 'DEMO-P-001',
      product_name: '演示商品A',
      sku_id: 1,
      spec_code: 'DEMO-P-001-RED',
      spec_name: '红色',
      qty: '5.000',
      putaway_qty: '0.000',
      pending_putaway_qty: '5.000',
      cost_price: '9.50',
    },
  ],
  approval: {
    doc_type: 'stock_in',
    doc_id: 9,
    state: {
      status: 'APPROVED',
      current_step: null,
      total_steps: 1,
      approved_steps: [1],
    },
    records: [],
  },
}

const location = {
  id: 1,
  warehouse_id: 1,
  warehouse_name: '演示主仓',
  code: 'DEMO-A-01',
  row_no: 1,
  col_no: 1,
  layer_no: 1,
  capacity: 100,
  used_qty: '0.000',
  remaining_qty: '100.000',
  priority: 0,
  sort_order: 0,
  is_virtual: false,
  is_enabled: true,
}

async function mountPage(
  component: Component,
  path: string,
  permissions: string[],
) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.setPermissionSnapshot({
    staff_id: 10,
    roles: ['ADMIN'],
    permissions,
    modules: ['stock', 'product', 'purchase'],
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes,
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, {
    attachTo: document.body,
    global: {
      plugins: [pinia, router, ElementPlus],
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

function buttonByText(text: string): HTMLButtonElement | undefined {
  return Array.from(document.body.querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === text,
  ) as HTMLButtonElement | undefined
}

beforeEach(() => {
  stockMocks.warehouseApi.list.mockResolvedValue([warehouse])
  stockMocks.warehouseApi.listLocations.mockResolvedValue([location])
  stockMocks.stockInApi.list.mockResolvedValue({
    items: [stockIn],
    total: 1,
    page: 1,
    page_size: 20,
  })
  stockMocks.stockInApi.get.mockResolvedValue(stockInDetail)
  stockMocks.stockInApi.pendingPutaways.mockResolvedValue([])
  stockMocks.stockInApi.listPutaways.mockResolvedValue([])
  stockMocks.stockInApi.putaway.mockResolvedValue([
    {
      id: 1,
      stock_in_item_id: 21,
      location_id: 1,
      qty: '5.000',
    },
  ])
  stockMocks.stockQueryApi.summary.mockResolvedValue({
    items: [
      {
        warehouse_id: 1,
        warehouse_name: '演示主仓',
        product_id: 1,
        product_code: 'DEMO-P-001',
        product_name: '演示商品A',
        sku_id: 1,
        spec_code: 'DEMO-P-001-RED',
        spec_name: '红色',
        normal_qty: '3.000',
        defective_qty: '0',
        total_qty: '3.000',
        occupied_qty: '0',
        available_qty: '3.000',
        shortage_qty: '0.000',
        warn_qty: '0',
        is_below_warning: false,
        cost_price: '8.50',
        stock_cost: '25.50',
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  })
  stockMocks.stockQueryApi.locations.mockResolvedValue([])
  purchaseMocks.purchaseOrderApi.list.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 200,
  })
  productOptionsMock.listProducts.mockResolvedValue({
    items: [{ id: 1, code: 'DEMO-P-001', name: '演示商品A' }],
    total: 1,
    page: 1,
    page_size: 200,
  })
  productOptionsMock.listSkus.mockResolvedValue([
    {
      id: 1,
      product_id: 1,
      spec_code: 'DEMO-P-001-RED',
      spec_name: '红色',
    },
  ])
})

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('stock pages', () => {
  it('routes and menus all stock resources to real page components', () => {
    const names = [
      'stock-warehouses',
      'stock-locations',
      'stock-purchase-out',
      'stock-transfers',
      'stock-flows',
      'stock-query',
      'stock-adjustments',
      'stock-inbound',
      'stock-check',
      'stock-warnings',
      'stock-shop',
      'stock-settlement',
      'stock-options',
      'stock-costs',
    ]
    const stockRoutes = routes
      .flatMap((route) => route.children || [])
      .filter((route) => names.includes(String(route.name)))

    expect(stockRoutes).toHaveLength(14)
    expect(stockRoutes.every((route) => route.component)).toBe(true)

    const stockMenu = menuTree.find((item) => item.name === 'stock')
    const menuNames = stockMenu?.children?.map((item) => item.name) || []
    expect(menuNames).toEqual(expect.arrayContaining(names))
  })

  it('renders warehouse, inbound, and stock query data', async () => {
    const warehouseView = await mountPage(
      WarehouseManagementView,
      '/stock/warehouses',
      ['module:stock', 'stock:manage'],
    )
    expect(warehouseView.text()).toContain('仓库管理')
    expect(warehouseView.text()).toContain('DEMO-WH-01')

    const inboundView = await mountPage(StockInboundView, '/stock/inbound', [
      'module:stock',
      'stock:manage',
    ])
    expect(inboundView.text()).toContain('采购入库')
    expect(inboundView.text()).toContain('RK-001')

    const queryView = await mountPage(StockQueryView, '/stock/query', [
      'module:stock',
      'stock:manage',
    ])
    expect(queryView.text()).toContain('库存查询')
    expect(queryView.text()).toContain('演示商品A')
    expect(queryView.text()).toContain('25.50')
  })

  it('loads inbound details and submits putaway from the selected location', async () => {
    const wrapper = await mountPage(StockInboundView, '/stock/inbound', [
      'module:stock',
      'stock:manage',
    ])

    const detailButton = wrapper
      .findAll('button')
      .filter((button) => button.text() === '详情')
      .at(-1)
    expect(detailButton).toBeTruthy()
    await detailButton?.trigger('click')
    await vi.waitFor(() => {
      expect(stockMocks.stockInApi.get).toHaveBeenCalledWith(9)
      expect(document.body.textContent).toContain('入库单详情')
    })

    const putawayButton = buttonByText('上架')
    expect(putawayButton).toBeTruthy()
    putawayButton?.click()
    await vi.waitFor(() => {
      expect(stockMocks.warehouseApi.listLocations).toHaveBeenCalledWith({
        warehouse_id: 1,
        is_enabled: true,
      })
      expect(document.body.textContent).toContain('商品上架')
    })

    const submitButton = buttonByText('保存')
    expect(submitButton).toBeTruthy()
    submitButton?.click()
    await vi.waitFor(() => {
      expect(stockMocks.stockInApi.putaway).toHaveBeenCalledWith(9, [
        { stock_in_item_id: 21, location_id: 1, qty: '5.000' },
      ])
    })
  })
})
