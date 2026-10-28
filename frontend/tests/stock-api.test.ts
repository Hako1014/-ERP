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

import {
  locationAdjustApi,
  shopStockApi,
  stockCheckApi,
  stockInApi,
  stockOutApi,
  stockQueryApi,
  stockSettlementApi,
  transferApi,
  warehouseApi,
} from '@/api/stock'

beforeEach(() => {
  apiMock.delete.mockReset()
  apiMock.get.mockReset()
  apiMock.post.mockReset()
  apiMock.put.mockReset()
  apiMock.get.mockResolvedValue({ items: [], total: 0 })
  apiMock.post.mockResolvedValue({})
  apiMock.put.mockResolvedValue({})
  apiMock.delete.mockResolvedValue(undefined)
})

describe('stock API contracts', () => {
  it('uses warehouse and location endpoints for stock master data', async () => {
    await warehouseApi.list({ keyword: '主仓', is_enabled: true })
    await warehouseApi.createZone({
      warehouse_id: 1,
      code: 'A',
      name: 'A 区',
    })
    await warehouseApi.generateLocations({
      warehouse_id: 1,
      zone_id: 2,
      row_count: 2,
      col_count: 3,
      layer_count: 1,
      capacity: 100,
    })
    await warehouseApi.disableLocation(8)

    expect(apiMock.get).toHaveBeenCalledWith('/warehouses', {
      params: { keyword: '主仓', is_enabled: true },
    })
    expect(apiMock.post).toHaveBeenCalledWith('/zones', {
      warehouse_id: 1,
      code: 'A',
      name: 'A 区',
    })
    expect(apiMock.post).toHaveBeenCalledWith('/locations/generate', {
      warehouse_id: 1,
      zone_id: 2,
      row_count: 2,
      col_count: 3,
      layer_count: 1,
      capacity: 100,
    })
    expect(apiMock.post).toHaveBeenCalledWith('/locations/8/disable', undefined)
  })

  it('uses inbound, outbound, transfer, check, and adjustment endpoints', async () => {
    await stockInApi.pendingPutaways(1, 'RK')
    await stockInApi.putaway(9, [
      { stock_in_item_id: 21, location_id: 1, qty: '5.00' },
    ])
    await stockOutApi.offShelf(3, [
      { stock_out_item_id: 31, location_id: 2, qty: '1.00' },
    ])
    await transferApi.offShelfOut(4, [
      { transfer_out_item_id: 41, location_id: 2, qty: '2.00' },
    ])
    await transferApi.receiveIn(5, [
      { transfer_in_item_id: 51, location_id: 3, qty: '2.00' },
    ])
    await stockCheckApi.create({
      warehouse_id: 1,
      lock_warehouse: false,
      items: [],
    })
    await locationAdjustApi.approve(7, '库位修正')

    expect(apiMock.get).toHaveBeenCalledWith('/stock-ins/putaway/pending', {
      params: { warehouse_id: 1, keyword: 'RK' },
    })
    expect(apiMock.post).toHaveBeenCalledWith('/stock-ins/9/putaway', {
      items: [{ stock_in_item_id: 21, location_id: 1, qty: '5.00' }],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/stock-outs/3/off-shelf', {
      items: [{ stock_out_item_id: 31, location_id: 2, qty: '1.00' }],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/transfer-outs/4/off-shelf', {
      items: [{ transfer_out_item_id: 41, location_id: 2, qty: '2.00' }],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/transfer-ins/5/receive', {
      items: [{ transfer_in_item_id: 51, location_id: 3, qty: '2.00' }],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/location-adjusts/7/approve', {
      comment: '库位修正',
    })
  })

  it('uses query, shop stock, and settlement endpoints', async () => {
    await stockQueryApi.summary({
      warehouse_id: 1,
      keyword: '演示',
      below_warning: true,
    })
    await stockQueryApi.locations({ warehouse_id: 1, sku_id: 1 })
    await stockQueryApi.setLocationStock({
      warehouse_id: 1,
      location_id: 2,
      product_id: 1,
      sku_id: 1,
      target_qty: '12.00',
    })
    await shopStockApi.sync({ ids: [6, 7] })
    await stockQueryApi.refreshCosts()
    await stockSettlementApi.rewriteCost({
      settlement_date: '2026-09-30',
    })
    await stockSettlementApi.rewriteLogs({
      product_id: 1,
      page: 1,
      page_size: 20,
    })

    expect(apiMock.get).toHaveBeenCalledWith('/stock-summary', {
      params: {
        warehouse_id: 1,
        keyword: '演示',
        below_warning: true,
      },
    })
    expect(apiMock.get).toHaveBeenCalledWith('/stock-locations', {
      params: { warehouse_id: 1, sku_id: 1 },
    })
    expect(apiMock.put).toHaveBeenCalledWith('/stock/location-stock', {
      warehouse_id: 1,
      location_id: 2,
      product_id: 1,
      sku_id: 1,
      target_qty: '12.00',
    })
    expect(apiMock.post).toHaveBeenCalledWith('/shop-stocks/sync', {
      ids: [6, 7],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/stock-costs/refresh', undefined)
    expect(apiMock.post).toHaveBeenCalledWith(
      '/stock-settlements/rewrite-outbound-cost',
      { settlement_date: '2026-09-30' },
    )
    expect(apiMock.get).toHaveBeenCalledWith(
      '/stock-settlements/cost-rewrite-logs',
      {
        params: { product_id: 1, page: 1, page_size: 20 },
      },
    )
  })
})
