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
  afterSaleApi,
  orderApi,
  orderPrintApi,
  shipmentApi,
  waveApi,
} from '@/api/order'

beforeEach(() => {
  apiMock.delete.mockReset()
  apiMock.get.mockReset()
  apiMock.post.mockReset()
  apiMock.put.mockReset()
})

describe('order API contracts', () => {
  it('covers order lifecycle, fulfillment and export endpoints', async () => {
    apiMock.get.mockResolvedValue({ items: [], total: 0 })
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await orderApi.list({
      status: 'PENDING',
      audit_status: 'APPROVED',
      page: 1,
      page_size: 20,
    })
    await orderApi.create({
      shop_id: 1,
      receiver_name: '张三',
      paid_amount: '99.00',
      items: [{ product_id: 3, sku_id: 4, qty: '2.000', price: '49.50' }],
    })
    await orderApi.approve(10, '财务同意')
    await orderApi.reject(10, '地址异常')
    await orderApi.unaudit(10, '撤回复核')
    await orderApi.setWarehouse(10, 2)
    await orderApi.setLogistics(10, {
      logistics_id: 3,
      tracking_no: 'SF10001',
    })
    await orderApi.setFlags(10, ['MERGED', 'REFUND'], 'SET')
    await orderApi.logs(10)
    await orderApi.export({ status: 'PENDING' })

    expect(apiMock.get).toHaveBeenCalledWith('/orders', {
      params: {
        status: 'PENDING',
        audit_status: 'APPROVED',
        page: 1,
        page_size: 20,
      },
    })
    expect(apiMock.post).toHaveBeenCalledWith('/orders/10/approve', {
      comment: '财务同意',
    })
    expect(apiMock.post).toHaveBeenCalledWith('/orders/10/warehouse', {
      warehouse_id: 2,
    })
    expect(apiMock.post).toHaveBeenCalledWith('/orders/10/logistics', {
      logistics_id: 3,
      tracking_no: 'SF10001',
    })
    expect(apiMock.get).toHaveBeenCalledWith('/orders/10/logs')
    expect(apiMock.get).toHaveBeenCalledWith('/orders/export', {
      params: { status: 'PENDING' },
      responseType: 'blob',
    })
  })

  it('covers wave, print and scan shipment endpoints', async () => {
    apiMock.get.mockResolvedValue({ items: [], total: 0 })
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await waveApi.createStrategy({
      name: '华东仓波次',
      warehouse_id: 2,
      wave_order_count: 30,
      sort_type: 'LOCATION',
    })
    await waveApi.run({ strategy_ids: [7], order_ids: [10, 11] })
    await waveApi.pendingCount(7, [10, 11])
    await waveApi.get(3)

    await orderPrintApi.acquireWaybills([10], 'THERMAL', {
      'SO-10': 'SF10001',
    })
    await orderPrintApi.createBatch({
      order_ids: [10],
      print_type: 'THERMAL',
      include_shipping_slip: true,
      sort_type: 'LOCATION',
    })
    await orderPrintApi.downloadPdf('PB-1', 'PICK_LIST')

    await shipmentApi.scan({
      scan_code: 'SO-10',
      barcode: '690000000001',
      qty: '1.000',
      actual_weight_g: '520.000',
      weight_tolerance_g: '50.000',
    })
    await shipmentApi.list({ status: 'SHIPPED', page: 1, page_size: 20 })
    await shipmentApi.syncPlatform(5, 'SYNCED', '人工回传')

    expect(apiMock.post).toHaveBeenCalledWith('/waves/run', {
      strategy_ids: [7],
      order_ids: [10, 11],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/print/batches', {
      order_ids: [10],
      print_type: 'THERMAL',
      include_shipping_slip: true,
      sort_type: 'LOCATION',
    })
    expect(apiMock.get).toHaveBeenCalledWith('/print/batches/PB-1/pdf', {
      params: { document_type: 'PICK_LIST' },
      responseType: 'blob',
    })
    expect(apiMock.post).toHaveBeenCalledWith('/shipments/scan', {
      scan_code: 'SO-10',
      barcode: '690000000001',
      qty: '1.000',
      actual_weight_g: '520.000',
      weight_tolerance_g: '50.000',
    })
    expect(apiMock.post).toHaveBeenCalledWith(
      '/shipments/5/platform-sync',
      {
        status: 'SYNCED',
        message: '人工回传',
      },
    )
  })

  it('covers return, exchange, register and confirmation endpoints', async () => {
    apiMock.get.mockResolvedValue({ items: [], total: 0 })
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await afterSaleApi.createReturn({
      order_id: 10,
      warehouse_id: 2,
      reason: '尺码不合适',
      items: [
        { product_id: 3, sku_id: 4, qty: '1.000', refund_amount: '49.50' },
      ],
    })
    await afterSaleApi.submitReturn(6)
    await afterSaleApi.confirmReturn(6, {
      location_id: 8,
      comment: '已核验入库',
    })
    await afterSaleApi.updateReturnLogistics(6, {
      logistics_company: '顺丰',
      tracking_no: 'SF-RETURN-1',
    })
    await afterSaleApi.createExchange({
      order_id: 10,
      reason: '换色',
      items: [{ product_id: 3, sku_id: 5, exchange_qty: '1.000' }],
    })
    await afterSaleApi.submitExchange(7)
    await afterSaleApi.createRegister({
      logistics_company: '圆通',
      tracking_no: 'YT-1',
      sender: '张三',
      product_info: '商品A 1件',
    })
    await afterSaleApi.attachReturnRegister(6, 9)
    await afterSaleApi.exportReturns({ status: 'SUBMITTED' })

    expect(apiMock.post).toHaveBeenCalledWith('/after-sales/returns', {
      order_id: 10,
      warehouse_id: 2,
      reason: '尺码不合适',
      items: [
        { product_id: 3, sku_id: 4, qty: '1.000', refund_amount: '49.50' },
      ],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/after-sales/returns/6/confirm', {
      location_id: 8,
      comment: '已核验入库',
    })
    expect(apiMock.post).toHaveBeenCalledWith('/after-sales/exchanges', {
      order_id: 10,
      reason: '换色',
      items: [{ product_id: 3, sku_id: 5, exchange_qty: '1.000' }],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/after-sales/returns/6/register', {
      register_id: 9,
    })
    expect(apiMock.get).toHaveBeenCalledWith('/after-sales/returns/export', {
      params: { status: 'SUBMITTED' },
      responseType: 'blob',
    })
  })
})
