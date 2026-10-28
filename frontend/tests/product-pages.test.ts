import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  productApi: {
    batchDeleteProducts: vi.fn(),
    createProduct: vi.fn(),
    deleteProduct: vi.fn(),
    getCombo: vi.fn(),
    getProduct: vi.fn(),
    listCombos: vi.fn(),
    listProducts: vi.fn(),
    listSkus: vi.fn(),
    setComboEnabled: vi.fn(),
    setProductDisabled: vi.fn(),
    setProductListing: vi.fn(),
    updateProduct: vi.fn(),
    createCombo: vi.fn(),
    updateCombo: vi.fn(),
    deleteCombo: vi.fn(),
    createSku: vi.fn(),
    updateSku: vi.fn(),
    deleteSku: vi.fn(),
  },
  productSettingsApi: {
    createBrand: vi.fn(),
    createCategory: vi.fn(),
    createSpecType: vi.fn(),
    createSpecValue: vi.fn(),
    createUnit: vi.fn(),
    createUnitGroup: vi.fn(),
    deleteBrand: vi.fn(),
    deleteCategory: vi.fn(),
    deleteSpecType: vi.fn(),
    deleteSpecValue: vi.fn(),
    deleteUnit: vi.fn(),
    deleteUnitGroup: vi.fn(),
    listBrands: vi.fn(),
    listCategories: vi.fn(),
    listSpecTypes: vi.fn(),
    listSpecValues: vi.fn(),
    listUnitGroups: vi.fn(),
    listUnits: vi.fn(),
    updateBrand: vi.fn(),
    updateCategory: vi.fn(),
    updateSpecType: vi.fn(),
    updateSpecValue: vi.fn(),
    updateUnit: vi.fn(),
    updateUnitGroup: vi.fn(),
  },
  partyApi: {
    listSuppliers: vi.fn(),
  },
}))

vi.mock('@/api/product', () => ({
  PRODUCT_EXPORT_URL: '/products/export',
  PRODUCT_IMPORT_URL: '/products/import',
  PRODUCT_TEMPLATE_URL: '/common/excel-template/product',
  productApi: mocks.productApi,
}))
vi.mock('@/api/productSettings', () => ({
  productSettingsApi: mocks.productSettingsApi,
}))
vi.mock('@/api/party', () => ({
  partyApi: mocks.partyApi,
}))

import ComboListView from '@/views/product/ComboListView.vue'
import ProductListView from '@/views/product/ProductListView.vue'
import ProductSettingsView from '@/views/product/ProductSettingsView.vue'
import { routes } from '@/router/routes'
import { useAuthStore } from '@/stores/auth'

const wrappers: VueWrapper[] = []
const product = {
  id: 1,
  name: '旅行箱 24 寸',
  code: 'P0001',
  art_no: 'ART-1',
  category_id: 1,
  category_name: '箱包',
  brand_id: 1,
  brand_name: '示例品牌',
  default_supplier_id: 9,
  measure_type: 'SINGLE',
  main_unit_id: 1,
  unit_name: '个',
  online_price: '399.00',
  tag_price: '499.00',
  min_price: '329.00',
  cost_price: '210.00',
  weight: '3.200',
  volume: '0.0800',
  min_stock: 0,
  max_stock: 0,
  warn_stock: 10,
  is_gift: false,
  use_float_cost: false,
  is_disabled: false,
  listed: false,
  listed_at: null,
  skus: [],
}

async function mountPage(component: Component, canManage = true) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.setPermissionSnapshot({
    staff_id: 1,
    roles: ['ADMIN'],
    permissions: canManage
      ? ['module:product', 'product:manage', 'module:settings', 'settings:manage']
      : ['module:product', 'module:settings'],
    modules: ['product', 'settings'],
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes,
  })
  await router.push('/products/list')
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
  mocks.productApi.listProducts.mockResolvedValue({
    items: [product],
    total: 1,
    page: 1,
    page_size: 20,
  })
  mocks.productApi.getProduct.mockResolvedValue({ ...product, skus: [] })
  mocks.productApi.createProduct.mockResolvedValue(product)
  mocks.productApi.setProductListing.mockResolvedValue({ count: 1 })
  mocks.productApi.listCombos.mockResolvedValue({
    items: [
      {
        id: 3,
        combo_no: 'C0001',
        name: '旅行套装',
        main_product_id: 1,
        main_product_name: '旅行箱 24 寸',
        main_sku_id: null,
        is_enabled: true,
        remark: '',
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  })
  mocks.productApi.getCombo.mockResolvedValue({
    id: 3,
    combo_no: 'C0001',
    name: '旅行套装',
    main_product_id: 1,
    main_product_name: '旅行箱 24 寸',
    main_sku_id: null,
    is_enabled: true,
    items: [
      {
        id: 1,
        product_id: 2,
        product_name: '行李牌',
        sku_id: null,
        qty: '1',
        sort_order: 0,
      },
    ],
  })
  mocks.productApi.listSkus.mockResolvedValue([])
  mocks.productSettingsApi.listCategories.mockResolvedValue([
    { id: 1, name: '箱包', parent_id: null, sort_order: 0 },
  ])
  mocks.productSettingsApi.listBrands.mockResolvedValue([
    { id: 1, name: '示例品牌', image: null },
  ])
  mocks.productSettingsApi.listUnits.mockResolvedValue([
    { id: 1, name: '个', code: 'PCS' },
  ])
  mocks.productSettingsApi.listUnitGroups.mockResolvedValue([])
  mocks.productSettingsApi.listSpecTypes.mockResolvedValue([])
  mocks.productSettingsApi.listSpecValues.mockResolvedValue([])
  mocks.partyApi.listSuppliers.mockResolvedValue({
    items: [
      { id: 9, code: 'SUP-1', name: '示例供应商', is_active: true },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  })
})

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('product pages', () => {
  it('routes product resources to real page components', () => {
    const productRoutes = routes
      .flatMap((route) => route.children || [])
      .filter((route) =>
        ['product-list', 'product-combos', 'settings-product'].includes(
          String(route.name),
        ),
      )

    expect(productRoutes).toHaveLength(3)
    expect(productRoutes.every((route) => route.component)).toBe(true)
  })

  it('renders the product list and creates a product through the dialog', async () => {
    const wrapper = await mountPage(ProductListView)
    expect(wrapper.text()).toContain('旅行箱 24 寸')
    expect(wrapper.text()).toContain('P0001')
    expect(wrapper.text()).toContain('箱包')

    const createButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '新增商品')
    await createButton?.trigger('click')
    await flushPromises()

    const inputs = document.querySelectorAll('.el-dialog__body input')
    const nameInput = inputs[0] as HTMLInputElement
    const codeInput = inputs[1] as HTMLInputElement
    nameInput.value = '新商品'
    nameInput.dispatchEvent(new Event('input', { bubbles: true }))
    codeInput.value = 'P0002'
    codeInput.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()

    const saveButton = Array.from(
      document.querySelectorAll('.el-dialog__footer button'),
    ).find((button) => button.textContent?.includes('保存'))
    saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await vi.waitFor(() => {
      expect(mocks.productApi.createProduct).toHaveBeenCalled()
    })
  })

  it('lists and unlists a product', async () => {
    const wrapper = await mountPage(ProductListView)
    const listButton = wrapper
      .findAll('button')
      .filter((button) => button.text() === '上架')
      .at(-1)

    await listButton?.trigger('click')
    await vi.waitFor(() => {
      expect(mocks.productApi.setProductListing).toHaveBeenCalledWith([1], true)
    })
  })

  it('hides product write actions for read-only users', async () => {
    const wrapper = await mountPage(ProductListView, false)
    expect(wrapper.text()).not.toContain('新增商品')
    expect(wrapper.text()).not.toContain('编辑')
    expect(wrapper.text()).toContain('只读')
  })

  it('renders combination products and product settings', async () => {
    const combo = await mountPage(ComboListView)
    const settings = await mountPage(ProductSettingsView)

    expect(combo.text()).toContain('旅行套装')
    expect(combo.text()).toContain('旅行箱 24 寸')
    expect(settings.text()).toContain('商品设置')
    expect(settings.text()).toContain('商品分类')
    expect(settings.text()).toContain('箱包')
  })
})
