import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const orderMocks = vi.hoisted(() => ({
  orderApi: {
    approve: vi.fn(),
    create: vi.fn(),
    export: vi.fn(),
    flags: vi.fn(),
    get: vi.fn(),
    list: vi.fn(),
    logs: vi.fn(),
    reject: vi.fn(),
    setFlags: vi.fn(),
    setLogistics: vi.fn(),
    setWarehouse: vi.fn(),
    submit: vi.fn(),
    unaudit: vi.fn(),
    update: vi.fn(),
  },
  waveApi: {
    createStrategy: vi.fn(),
    get: vi.fn(),
    list: vi.fn(),
    listStrategies: vi.fn(),
    pendingCount: vi.fn(),
    remove: vi.fn(),
    removeStrategy: vi.fn(),
    run: vi.fn(),
    updateStrategy: vi.fn(),
  },
  orderPrintApi: {
    acquireWaybills: vi.fn(),
    createBatch: vi.fn(),
    downloadPdf: vi.fn(),
    getBatch: vi.fn(),
    recycleWaybills: vi.fn(),
  },
  shipmentApi: {
    get: vi.fn(),
    list: vi.fn(),
    listByOrder: vi.fn(),
    pickup: vi.fn(),
    scan: vi.fn(),
    ship: vi.fn(),
    syncPlatform: vi.fn(),
  },
  afterSaleApi: {
    attachExchangeRegister: vi.fn(),
    attachReturnRegister: vi.fn(),
    cancelExchange: vi.fn(),
    cancelReturn: vi.fn(),
    confirmReturn: vi.fn(),
    createExchange: vi.fn(),
    createRegister: vi.fn(),
    createReturn: vi.fn(),
    detachExchangeRegister: vi.fn(),
    detachReturnRegister: vi.fn(),
    exportReturns: vi.fn(),
    getExchange: vi.fn(),
    getReturn: vi.fn(),
    listExchanges: vi.fn(),
    listRegisters: vi.fn(),
    listReturns: vi.fn(),
    removeExchange: vi.fn(),
    removeRegister: vi.fn(),
    removeReturn: vi.fn(),
    submitExchange: vi.fn(),
    submitReturn: vi.fn(),
    updateExchange: vi.fn(),
    updateRegister: vi.fn(),
    updateReturn: vi.fn(),
    updateReturnLogistics: vi.fn(),
  },
}))

const shopMocks = vi.hoisted(() => ({
  listShops: vi.fn(),
}))

const stockMocks = vi.hoisted(() => ({
  warehouseApi: {
    list: vi.fn(),
  },
}))

const logisticsMocks = vi.hoisted(() => ({
  logisticsApi: {
    list: vi.fn(),
  },
}))

const productOptionMocks = vi.hoisted(() => ({
  listProducts: vi.fn(),
  listSkus: vi.fn(),
}))

vi.mock('@/api/order', () => ({
  ...orderMocks,
  ORDER_EXPORT_URL: '/orders/export',
  ORDER_TEMPLATE_URL: '/orders/template',
  AFTER_SALE_RETURN_EXPORT_URL: '/after-sales/returns/export',
}))
vi.mock('@/api/shop', () => ({
  shopApi: shopMocks,
}))
vi.mock('@/api/stock', () => ({
  warehouseApi: stockMocks.warehouseApi,
}))
vi.mock('@/api/logistics', () => ({
  logisticsApi: logisticsMocks.logisticsApi,
}))
vi.mock('@/api/productOptions', () => ({
  productOptionsApi: productOptionMocks,
}))

import OrderListView from '@/views/order/OrderListView.vue'
import ScanShipmentView from '@/views/order/ScanShipmentView.vue'
import { menuTree } from '@/router/menu'
import { routes } from '@/router/routes'
import { useAuthStore } from '@/stores/auth'

const wrappers: VueWrapper[] = []

const order = {
  id: 10,
  order_no: 'SO-20260924-001',
  shop_id: 1,
  shop_name: '演示旗舰店',
  platform_id: 1,
  platform_name: '淘宝',
  warehouse_id: 1,
  warehouse_name: '演示主仓',
  logistics_id: 1,
  logistics_name: '顺丰',
  logistics_company: '顺丰速运',
  tracking_no: 'SF10001',
  buyer_nick: '测试买家',
  receiver_name: '张三',
  receiver_phone: '13800000000',
  receiver_region: '浙江省 杭州市',
  receiver_address: '西湖区 1 号',
  status: 'PENDING',
  status_label: '待处理',
  audit_status: 'APPROVED',
  pay_status: 'PAID',
  flags: [],
  flags_label: '',
  order_amount: '99.00',
  paid_amount: '99.00',
  freight: '0.00',
  platform_discount: '0.00',
  seller_discount: '0.00',
  total_qty: '2.000',
  order_time: '2026-09-24T10:00:00',
  created_at: '2026-09-24T10:00:00',
  items: [],
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
    modules: ['order', 'settings', 'stock'],
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

beforeEach(() => {
  orderMocks.orderApi.list.mockResolvedValue({
    items: [order],
    total: 1,
    page: 1,
    page_size: 20,
  })
  orderMocks.orderApi.get.mockResolvedValue({
    ...order,
    items: [
      {
        id: 21,
        product_id: 3,
        product_code: 'P-001',
        product_name: '演示商品',
        sku_id: 4,
        spec_code: 'P-001-RED',
        sku_name: '红色',
        qty: '2.000',
        price: '49.50',
        amount: '99.00',
        is_gift: false,
      },
    ],
    approval: {
      doc_type: 'order',
      doc_id: 10,
      state: {
        status: 'APPROVED',
        current_step: null,
        total_steps: 1,
        approved_steps: [1],
      },
      records: [],
    },
  })
  orderMocks.orderApi.flags.mockResolvedValue([
    { code: 'MERGED', label: '合并订单' },
    { code: 'REFUND', label: '退款订单' },
  ])
  orderMocks.orderApi.logs.mockResolvedValue([])
  orderMocks.shipmentApi.listByOrder.mockResolvedValue([])
  orderMocks.shipmentApi.list.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 20,
  })
  orderMocks.shipmentApi.scan.mockResolvedValue({
    scan_code: 'SO-20260924-001',
    scan_source: 'ORDER',
    barcode: null,
    completed: true,
    package_count: 1,
    remaining: {},
    shipment: {
      id: 5,
      shipment_no: 'SHIP-001',
      order_id: 10,
      order_no: 'SO-20260924-001',
      warehouse_id: 1,
      package_no: 1,
      tracking_no: 'SF10001',
      shipping_type: 'EXPRESS',
      source: 'SCAN',
      status: 'SHIPPED',
      weight_out_of_range: false,
      weight_overridden: false,
      platform_sync_status: 'PENDING',
      shipped_by_name: '仓库管理员',
      items: [],
    },
  })
  shopMocks.listShops.mockResolvedValue({
    items: [
      {
        id: 1,
        platform_id: 1,
        name: '演示旗舰店',
        is_locked: false,
        auto_sync_order: true,
        auto_sync_stock: false,
        enable_logistics_params: false,
        api_configured: true,
      },
    ],
    total: 1,
    page: 1,
    page_size: 200,
  })
  stockMocks.warehouseApi.list.mockResolvedValue([
    {
      id: 1,
      code: 'WH-01',
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
    },
  ])
  logisticsMocks.logisticsApi.list.mockResolvedValue([
    { id: 1, name: '顺丰', is_default: true, is_enabled: true },
  ])
  productOptionMocks.listProducts.mockResolvedValue({
    items: [{ id: 3, code: 'P-001', name: '演示商品' }],
    total: 1,
    page: 1,
    page_size: 200,
  })
  productOptionMocks.listSkus.mockResolvedValue([
    {
      id: 4,
      product_id: 3,
      spec_code: 'P-001-RED',
      spec_name: '红色',
    },
  ])
})

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('order pages', () => {
  it('maps all order menus and the print/scan workbenches to real components', () => {
    const menuNames = [
      'order-new',
      'order-pending',
      'order-print',
      'order-ship',
      'order-shipped',
      'order-done',
      'order-returns',
      'order-waves',
      'order-process',
      'order-settings',
    ]
    const orderMenu = menuTree.find((item) => item.name === 'orders')
    expect(orderMenu?.children?.map((item) => item.name)).toEqual(menuNames)

    const allRoutes = routes.flatMap((route) => route.children || [])
    const mapped = allRoutes.filter((route) =>
      [...menuNames, 'order-print-workbench', 'order-scan-shipment'].includes(
        String(route.name),
      ),
    )
    expect(mapped).toHaveLength(12)
    expect(mapped.every((route) => route.component)).toBe(true)
  })

  it('renders the approved pending-order list from the real order API', async () => {
    const wrapper = await mountPage(OrderListView, '/orders/pending', [
      'module:order',
      'order:audit',
      'order:ship',
    ])

    expect(wrapper.text()).toContain('待处理订单')
    expect(wrapper.text()).toContain('SO-20260924-001')
    expect(wrapper.text()).toContain('演示旗舰店')
    expect(orderMocks.orderApi.list).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'PENDING',
        audit_status: 'APPROVED',
        page: 1,
        page_size: 20,
      }),
    )
  })

  it('submits a scanned order and renders the completed shipment result', async () => {
    const wrapper = await mountPage(ScanShipmentView, '/orders/scan-shipment', [
      'module:order',
      'order:ship',
    ])

    const scanInput = wrapper.find(
      'input[placeholder="扫描后按回车连续操作"]',
    )
    await scanInput.setValue('SO-20260924-001')
    await scanInput.trigger('keyup.enter')
    await flushPromises()

    expect(orderMocks.shipmentApi.scan).toHaveBeenCalledWith(
      expect.objectContaining({
        scan_code: 'SO-20260924-001',
        qty: '1.000',
        package_no: 1,
      }),
    )
    expect(wrapper.text()).toContain('订单已发完')
    expect(wrapper.text()).toContain('SHIP-001')
  })
})
