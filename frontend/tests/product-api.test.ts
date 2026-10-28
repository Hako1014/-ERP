import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: apiMock,
}))

import { productApi } from '@/api/product'
import { productSettingsApi } from '@/api/productSettings'

beforeEach(() => {
  apiMock.delete.mockReset()
  apiMock.get.mockReset()
  apiMock.post.mockReset()
  apiMock.put.mockReset()
})

describe('product API contracts', () => {
  it('uses product, SKU, listing, and combo endpoints', async () => {
    apiMock.get.mockResolvedValue({ items: [], total: 0 })
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await productApi.listProducts({ keyword: '旅行', page: 2, page_size: 50 })
    await productApi.getProduct(7)
    await productApi.listSkus(7)
    await productApi.createSku(7, { spec_code: 'SKU-7' })
    await productApi.updateSku(11, { sale_price: '12.50' })
    await productApi.deleteSku(11)
    await productApi.setProductListing([7], true)
    await productApi.batchDeleteProducts([7, 8])
    await productApi.listCombos({ is_enabled: true, page: 1, page_size: 20 })
    await productApi.setComboEnabled(3, false)

    expect(apiMock.get).toHaveBeenCalledWith('/products', {
      params: { keyword: '旅行', page: 2, page_size: 50 },
    })
    expect(apiMock.get).toHaveBeenCalledWith('/products/7')
    expect(apiMock.get).toHaveBeenCalledWith('/products/7/skus')
    expect(apiMock.post).toHaveBeenCalledWith('/products/7/skus', {
      spec_code: 'SKU-7',
    })
    expect(apiMock.put).toHaveBeenCalledWith('/product-skus/11', {
      sale_price: '12.50',
    })
    expect(apiMock.delete).toHaveBeenCalledWith('/product-skus/11')
    expect(apiMock.post).toHaveBeenCalledWith('/products/listing', {
      ids: [7],
      listed: true,
    })
    expect(apiMock.post).toHaveBeenCalledWith('/products/batch-delete', {
      ids: [7, 8],
    })
    expect(apiMock.get).toHaveBeenCalledWith('/combo-products', {
      params: { is_enabled: true, page: 1, page_size: 20 },
    })
    expect(apiMock.post).toHaveBeenCalledWith('/combo-products/3/disable')
  })

  it('uses product settings endpoints for all basic data', async () => {
    apiMock.get.mockResolvedValue([])
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await productSettingsApi.listCategories()
    await productSettingsApi.createCategory({ name: '女装' })
    await productSettingsApi.updateBrand(2, { name: '新品牌' })
    await productSettingsApi.listUnits()
    await productSettingsApi.createUnitGroup({
      name: '箱规',
      main_unit_id: 1,
      items: [{ unit_id: 2, conversion_rate: '12' }],
    })
    await productSettingsApi.listSpecTypes()
    await productSettingsApi.createSpecValue(3, {
      name: '红色',
      code: 'RED',
    })

    expect(apiMock.get).toHaveBeenCalledWith('/product-settings/categories')
    expect(apiMock.post).toHaveBeenCalledWith('/product-settings/categories', {
      name: '女装',
    })
    expect(apiMock.put).toHaveBeenCalledWith('/product-settings/brands/2', {
      name: '新品牌',
    })
    expect(apiMock.get).toHaveBeenCalledWith('/product-settings/units')
    expect(apiMock.post).toHaveBeenCalledWith(
      '/product-settings/unit-groups',
      {
        name: '箱规',
        main_unit_id: 1,
        items: [{ unit_id: 2, conversion_rate: '12' }],
      },
    )
    expect(apiMock.get).toHaveBeenCalledWith('/product-settings/spec-types')
    expect(apiMock.post).toHaveBeenCalledWith(
      '/product-settings/spec-types/3/values',
      { name: '红色', code: 'RED' },
    )
  })
})
