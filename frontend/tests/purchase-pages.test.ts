import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const purchaseMocks = vi.hoisted(() => ({
  invoiceApi: {
    approve: vi.fn(),
    costDetail: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    list: vi.fn(),
    reject: vi.fn(),
    remove: vi.fn(),
    submit: vi.fn(),
    unaudit: vi.fn(),
    update: vi.fn(),
  },
  reportApi: {
    inTransit: vi.fn(),
    inbounds: vi.fn(),
    invoices: vi.fn(),
    orders: vi.fn(),
  },
  referenceApi: {
    listStockIns: vi.fn(),
    listWarehouses: vi.fn(),
  },
}))

const partyMock = vi.hoisted(() => ({
  listSuppliers: vi.fn(),
}))

const companyMock = vi.hoisted(() => ({
  listStaff: vi.fn(),
}))

const productOptionsMock = vi.hoisted(() => ({
  listProducts: vi.fn(),
}))

vi.mock('@/api/purchase', () => ({
  purchaseInvoiceApi: purchaseMocks.invoiceApi,
  purchaseReferenceApi: purchaseMocks.referenceApi,
  purchaseReportApi: purchaseMocks.reportApi,
}))
vi.mock('@/api/party', () => ({
  partyApi: partyMock,
}))
vi.mock('@/api/company', () => ({
  companyApi: companyMock,
}))
vi.mock('@/api/productOptions', () => ({
  productOptionsApi: productOptionsMock,
}))

import PurchaseInvoiceListView from '@/views/purchase/PurchaseInvoiceListView.vue'
import PurchaseReportView from '@/views/purchase/PurchaseReportView.vue'
import { menuTree } from '@/router/menu'
import { routes } from '@/router/routes'
import { useAuthStore } from '@/stores/auth'

const wrappers: VueWrapper[] = []

const invoice = {
  id: 11,
  invoice_no: 'INV-001',
  supplier_id: 5,
  supplier_name: '上海供应商',
  invoice_type: 'SPECIAL',
  invoice_type_name: '专用发票',
  tax_rate: '13.00',
  amount: '1130.00',
  tax_amount: '130.00',
  net_amount: '1000.00',
  write_off_amount: '0.00',
  write_off_status: 'NONE',
  status: 'PENDING',
  audit_status: 'PENDING',
  stock_in_ids: [6],
  stock_in_nos: ['RK-001'],
  cost_allocation: [],
  created_at: '2026-09-24T10:00:00',
  approval: {
    doc_type: 'purchase_invoice',
    doc_id: 11,
    state: {
      status: 'PENDING',
      current_step: 1,
      total_steps: 1,
      approved_steps: [],
    },
    records: [],
  },
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
    staff_id: 1,
    roles: ['ADMIN'],
    permissions,
    modules: ['purchase', 'product', 'stock', 'finance', 'settings'],
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
  purchaseMocks.invoiceApi.list.mockResolvedValue({
    items: [invoice],
    total: 1,
    page: 1,
    page_size: 20,
  })
  purchaseMocks.invoiceApi.get.mockResolvedValue(invoice)
  purchaseMocks.referenceApi.listStockIns.mockResolvedValue({
    items: [
      {
        id: 6,
        stock_in_no: 'RK-001',
        warehouse_id: 2,
        warehouse_name: '成品仓',
        purchase_order_id: 8,
        purchase_order_no: 'PO-001',
        status: 'RECEIVED',
        audit_status: 'APPROVED',
        total_qty: '10.00',
        item_count: 1,
      },
    ],
    total: 1,
    page: 1,
    page_size: 200,
  })
  purchaseMocks.referenceApi.listWarehouses.mockResolvedValue([
    {
      id: 2,
      code: 'WH-01',
      name: '成品仓',
      warehouse_type: 'FINISHED',
      is_enabled: true,
      is_virtual: false,
      selectable: true,
    },
  ])
  purchaseMocks.reportApi.orders.mockResolvedValue({
    items: [
      {
        supplier_id: 5,
        supplier_name: '上海供应商',
        order_count: 2,
        total_qty: '20.00',
        total_amount: '2000.00',
        arrived_qty: '12.00',
        in_transit_qty: '8.00',
      },
    ],
    summary: {
      supplier_count: 1,
      order_count: 2,
      total_qty: '20.00',
      total_amount: '2000.00',
      arrived_qty: '12.00',
      in_transit_qty: '8.00',
    },
  })
  purchaseMocks.reportApi.inbounds.mockResolvedValue({
    items: [
      {
        supplier_id: 5,
        supplier_name: '上海供应商',
        purchaser_id: 3,
        purchaser_name: '采购员甲',
        product_id: 1,
        product_code: 'P0001',
        product_name: '旅行箱',
        sku_id: 2,
        spec_code: 'RED-24',
        spec_name: '红色 24 寸',
        stock_in_count: 1,
        qty: '10.00',
        amount: '1000.00',
      },
    ],
    summary: {
      stock_in_count: 1,
      qty: '10.00',
      amount: '1000.00',
    },
  })
  purchaseMocks.reportApi.invoices.mockResolvedValue({
    items: [
      {
        supplier_id: 5,
        supplier_name: '上海供应商',
        write_off_status: 'FULL',
        invoice_count: 1,
        amount: '1130.00',
        tax_amount: '130.00',
        net_amount: '1000.00',
        write_off_amount: '1130.00',
      },
    ],
    summary: {
      invoice_count: 1,
      amount: '1130.00',
      tax_amount: '130.00',
      net_amount: '1000.00',
      write_off_amount: '1130.00',
    },
  })
  purchaseMocks.reportApi.inTransit.mockResolvedValue({
    items: [
      {
        product_id: 1,
        product_code: 'P0001',
        product_name: '旅行箱',
        sku_id: 2,
        spec_code: 'RED-24',
        spec_name: '红色 24 寸',
        current_qty: '5.00',
        in_transit_qty: '8.00',
        total_qty: '13.00',
      },
    ],
    summary: {
      sku_count: 1,
      current_qty: '5.00',
      in_transit_qty: '8.00',
      total_qty: '13.00',
    },
  })
  partyMock.listSuppliers.mockResolvedValue({
    items: [{ id: 5, code: 'SUP-001', name: '上海供应商', is_active: true }],
    total: 1,
    page: 1,
    page_size: 200,
  })
  companyMock.listStaff.mockResolvedValue({
    items: [
      {
        id: 3,
        company_id: 1,
        name: '采购员甲',
        is_distribution: false,
        is_active: true,
        has_password: true,
      },
    ],
    total: 1,
    page: 1,
    page_size: 200,
  })
  productOptionsMock.listProducts.mockResolvedValue({
    items: [{ id: 1, code: 'P0001', name: '旅行箱' }],
    total: 1,
    page: 1,
    page_size: 200,
  })
})

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('purchase pages', () => {
  it('routes and menus all purchase resources to real page components', () => {
    const names = [
      'purchase-plans',
      'purchase-orders',
      'purchase-prepays',
      'purchase-returns',
      'purchase-invoices',
      'purchase-reports',
    ]
    const purchaseRoutes = routes
      .flatMap((route) => route.children || [])
      .filter((route) => names.includes(String(route.name)))

    expect(purchaseRoutes).toHaveLength(6)
    expect(purchaseRoutes.every((route) => route.component)).toBe(true)

    const purchaseMenu = menuTree.find((item) => item.name === 'purchase')
    const menuNames = purchaseMenu?.children?.map((item) => item.name) || []
    expect(menuNames).toEqual(expect.arrayContaining(names))
  })

  it('renders the invoice list and loads cost allocation details', async () => {
    const wrapper = await mountPage(PurchaseInvoiceListView, '/purchase/invoices', [
      'module:purchase',
      'module:finance',
      'module:stock',
      'finance:manage',
    ])

    expect(wrapper.text()).toContain('采购发票')
    expect(wrapper.text()).toContain('新增采购发票')
    expect(wrapper.text()).toContain('INV-001')
    expect(wrapper.text()).toContain('上海供应商')
    expect(wrapper.text()).toContain('专用发票')
    expect(wrapper.text()).toContain('¥1,130.00')

    const detailButton = wrapper
      .findAll('button')
      .filter((button) => button.text() === '详情')
      .at(-1)
    await detailButton?.trigger('click')
    await vi.waitFor(() => {
      expect(purchaseMocks.invoiceApi.get).toHaveBeenCalledWith(11)
      expect(document.body.textContent).toContain('成本分摊明细')
    })
  })

  it('loads all four purchase report tabs with summary and detail data', async () => {
    const wrapper = await mountPage(PurchaseReportView, '/purchase/reports', [
      'module:purchase',
      'module:product',
      'module:stock',
      'module:settings',
    ])

    expect(wrapper.text()).toContain('采购报表')
    expect(wrapper.text()).toContain('供应商数')
    expect(wrapper.text()).toContain('上海供应商')
    expect(purchaseMocks.reportApi.orders).toHaveBeenCalled()

    async function openTab(label: string): Promise<void> {
      const tab = wrapper
        .findAll('.el-tabs__item')
        .find((item) => item.text() === label)
      await tab?.trigger('click')
      await flushPromises()
    }

    await openTab('采购入库')
    expect(purchaseMocks.reportApi.inbounds).toHaveBeenCalled()
    expect(wrapper.text()).toContain('旅行箱')
    expect(wrapper.text()).toContain('采购员甲')

    await openTab('采购发票')
    expect(purchaseMocks.reportApi.invoices).toHaveBeenCalled()
    expect(wrapper.text()).toContain('已核销')

    await openTab('库存 / 在途')
    expect(purchaseMocks.reportApi.inTransit).toHaveBeenCalled()
    expect(wrapper.text()).toContain('当前库存')
    expect(wrapper.text()).toContain('在途数量')
  })
})
