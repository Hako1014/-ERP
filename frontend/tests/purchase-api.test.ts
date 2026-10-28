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
  purchaseInvoiceApi,
  purchaseOrderApi,
  purchasePlanApi,
  purchasePrepayApi,
  purchaseReferenceApi,
  purchaseReportApi,
  purchaseReturnApi,
} from '@/api/purchase'

beforeEach(() => {
  apiMock.delete.mockReset()
  apiMock.get.mockReset()
  apiMock.post.mockReset()
  apiMock.put.mockReset()
})

describe('purchase API contracts', () => {
  it('uses purchase plan and order endpoints across the approval chain', async () => {
    apiMock.get.mockResolvedValue({ items: [], total: 0 })
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await purchasePlanApi.needs(5, '旅行箱', 1, 100)
    await purchasePlanApi.create({
      supplier_id: 5,
      plan_date: '2026-09-24T10:00:00',
      items: [{ product_id: 1, sku_id: 2, planned_qty: '10.00' }],
    })
    await purchasePlanApi.batchUpdateItems(7, {
      item_ids: [31],
      planned_qty: '12.00',
    })
    await purchasePlanApi.submit(7)
    await purchasePlanApi.approve(7, '同意')
    await purchasePlanApi.reject(7, '数量待确认')
    await purchasePlanApi.unaudit(7, '调整计划')
    await purchasePlanApi.abort(7)
    await purchasePlanApi.finish(7)

    await purchaseOrderApi.list({ status: 'PENDING', page: 1, page_size: 20 })
    await purchaseOrderApi.fromPlan({
      plan_id: 7,
      warehouse_id: 2,
      items: [{ plan_item_id: 31, qty: '8.00' }],
    })
    await purchaseOrderApi.submit(8)
    await purchaseOrderApi.approve(8, '同意')
    await purchaseOrderApi.unaudit(8, '撤回修改')
    await purchaseOrderApi.terminate(8, '供应商无法供货')
    await purchaseOrderApi.downloadPdf(8)

    expect(apiMock.get).toHaveBeenCalledWith('/purchase-plans/needs', {
      params: {
        supplier_id: 5,
        keyword: '旅行箱',
        page: 1,
        page_size: 100,
      },
    })
    expect(apiMock.post).toHaveBeenCalledWith('/purchase-orders/from-plan', {
      plan_id: 7,
      warehouse_id: 2,
      items: [{ plan_item_id: 31, qty: '8.00' }],
    })
    expect(apiMock.post).toHaveBeenCalledWith('/purchase-plans/7/approve', {
      comment: '同意',
    })
    expect(apiMock.post).toHaveBeenCalledWith('/purchase-orders/8/unaudit', {
      reason: '撤回修改',
    })
    expect(apiMock.get).toHaveBeenCalledWith('/purchase-orders/8/pdf', {
      responseType: 'blob',
    })
  })

  it('sends assistant creation idempotency headers and queries uncertain results', async () => {
    apiMock.post.mockResolvedValue({})
    apiMock.get.mockResolvedValue({ status: 'SUCCEEDED', result: {} })
    const payload = {
      supplier_id: 5,
      items: [{ product_id: 1, sku_id: 2, planned_qty: '10.00' }],
    }
    await purchasePlanApi.create(payload, {
      idempotencyKey: '7ab09bdf-9e59-421e-bd91-ed9d95a6f178',
      assistantDraftId: '20261010-0000000000860001',
    })
    await purchasePlanApi.getCreationRequest(
      '7ab09bdf-9e59-421e-bd91-ed9d95a6f178',
    )

    expect(apiMock.post).toHaveBeenCalledWith(
      '/purchase-plans',
      payload,
      {
        headers: {
          'Idempotency-Key': '7ab09bdf-9e59-421e-bd91-ed9d95a6f178',
          'X-Assistant-Draft-Id': '20261010-0000000000860001',
        },
      },
    )
    expect(apiMock.get).toHaveBeenCalledWith(
      '/purchase-plans/creation-requests/7ab09bdf-9e59-421e-bd91-ed9d95a6f178',
    )
  })

  it('uses prepay, return, invoice, reference, and report endpoints', async () => {
    apiMock.get.mockResolvedValue({ items: [], total: 0 })
    apiMock.post.mockResolvedValue({})
    apiMock.put.mockResolvedValue({})
    apiMock.delete.mockResolvedValue(undefined)

    await purchasePrepayApi.create({
      supplier_id: 5,
      order_id: 8,
      prepay_amount: '1000.00',
    })
    await purchasePrepayApi.submit(9)
    await purchasePrepayApi.voucher(9)

    await purchaseReturnApi.create({
      supplier_id: 5,
      order_id: 8,
      warehouse_id: 2,
      items: [
        {
          product_id: 1,
          sku_id: 2,
          unit_price: '20.00',
          return_qty: '2.00',
        },
      ],
    })
    await purchaseReturnApi.approve(10, '同意退货')
    await purchaseReturnApi.stockOut(10)
    await purchaseReturnApi.refund(10)

    await purchaseInvoiceApi.create({
      supplier_id: 5,
      invoice_type: 'SPECIAL',
      tax_rate: '13',
      amount: '1130.00',
      stock_in_ids: [6],
    })
    await purchaseInvoiceApi.approve(11, '财务审核通过')
    await purchaseInvoiceApi.unaudit(11, '作废重开')
    await purchaseInvoiceApi.costDetail(11)

    await purchaseReferenceApi.listStockIns('RK', 8)
    await purchaseReportApi.orders({ supplier_id: 5 })
    await purchaseReportApi.inbounds({ purchaser_id: 3, product_id: 1 })
    await purchaseReportApi.invoices({ write_off_status: 'FULL' })
    await purchaseReportApi.inTransit({ warehouse_id: 2, keyword: '旅行' })

    expect(apiMock.post).toHaveBeenCalledWith('/purchase-prepays/9/submit', {
      comment: undefined,
    })
    expect(apiMock.get).toHaveBeenCalledWith('/purchase-prepays/9/voucher')
    expect(apiMock.get).toHaveBeenCalledWith(
      '/purchase-returns/10/stock-out',
    )
    expect(apiMock.get).toHaveBeenCalledWith('/purchase-returns/10/refund')
    expect(apiMock.post).toHaveBeenCalledWith(
      '/purchase-invoices/11/unaudit',
      { reason: '作废重开' },
    )
    expect(apiMock.get).toHaveBeenCalledWith('/purchase-invoices/11/cost-detail')
    expect(apiMock.get).toHaveBeenCalledWith('/stock-ins', {
      params: {
        keyword: 'RK',
        purchase_order_id: 8,
        audit_status: 'APPROVED',
        page: 1,
        page_size: 200,
      },
    })
    expect(apiMock.get).toHaveBeenCalledWith('/reports/purchase-invoices', {
      params: { write_off_status: 'FULL' },
    })
    expect(apiMock.get).toHaveBeenCalledWith(
      '/reports/inventory-in-transit',
      { params: { warehouse_id: 2, keyword: '旅行' } },
    )
  })
})
