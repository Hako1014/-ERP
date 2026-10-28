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

import { companyApi } from '@/api/company'
import { logisticsApi } from '@/api/logistics'
import { partyApi } from '@/api/party'
import { productOptionsApi } from '@/api/productOptions'
import { shopApi } from '@/api/shop'

beforeEach(() => {
  apiMock.delete.mockReset()
  apiMock.get.mockReset()
  apiMock.post.mockReset()
  apiMock.put.mockReset()
})

describe('basic settings API contracts', () => {
  it('uses the company, staff, and IP rule endpoints', async () => {
    apiMock.get.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.post.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await companyApi.getCompany()
    await companyApi.updateCompany({ full_name: '测试公司' })
    await companyApi.updateStaff(9, { name: '张三' })
    await companyApi.resetPassword(9, 'new-password')
    await companyApi.getStaffDataScope(9)
    await companyApi.setStaffDataScope(9, 'DEPARTMENT')
    await companyApi.deleteIpRule(3)

    expect(apiMock.get).toHaveBeenCalledWith('/company')
    expect(apiMock.put).toHaveBeenCalledWith('/company', {
      full_name: '测试公司',
    })
    expect(apiMock.put).toHaveBeenCalledWith('/staff/9', { name: '张三' })
    expect(apiMock.post).toHaveBeenCalledWith('/staff/9/reset-password', {
      new_password: 'new-password',
    })
    expect(apiMock.get).toHaveBeenCalledWith('/staff/9/data-scope')
    expect(apiMock.put).toHaveBeenCalledWith('/staff/9/data-scope', {
      scope_type: 'DEPARTMENT',
    })
    expect(apiMock.delete).toHaveBeenCalledWith('/ip-rules/3')
  })

  it('uses supplier accounts, customer accounts, and price endpoints', async () => {
    apiMock.get.mockResolvedValue({ items: [], total: 0 })
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})

    await partyApi.listSuppliers({ keyword: '上海', page: 2, page_size: 50 })
    await partyApi.createSupplierAccount(7, {
      bank: '工商银行',
      is_default: true,
    })
    await partyApi.listCustomerAccounts(8)
    await partyApi.updateSupplierPrice(11, { price: '12.50' })
    await productOptionsApi.listSkus(21)

    expect(apiMock.get).toHaveBeenCalledWith('/suppliers', {
      params: { keyword: '上海', page: 2, page_size: 50 },
    })
    expect(apiMock.post).toHaveBeenCalledWith('/suppliers/7/accounts', {
      bank: '工商银行',
      is_default: true,
    })
    expect(apiMock.get).toHaveBeenCalledWith('/customers/8/accounts')
    expect(apiMock.put).toHaveBeenCalledWith('/supplier-prices/11', {
      price: '12.50',
    })
    expect(apiMock.get).toHaveBeenCalledWith('/products/21/skus')
  })

  it('uses shop API and logistics calculation endpoints', async () => {
    apiMock.get.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})

    await shopApi.saveShopApi(3, {
      api_code: 'API-3',
      app_secret: 'secret',
    })
    await logisticsApi.calculateFreight(5, { weight: '1.250' })

    expect(apiMock.put).toHaveBeenCalledWith('/shops/3/api', {
      api_code: 'API-3',
      app_secret: 'secret',
    })
    expect(apiMock.get).toHaveBeenCalledWith(
      '/freight-templates/5/calculate',
      { params: { weight: '1.250' } },
    )
  })
})
