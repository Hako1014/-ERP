import { api } from '@/api/client'
import type { DecimalValue } from '@/api/product'
import type { PageResult } from '@/api/party'

export interface PurchasePlanItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  art_no?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  brand_id?: number | null
  brand_name?: string | null
  unit_id?: number | null
  unit_name?: string | null
  planned_qty: DecimalValue
  purchased_qty: DecimalValue
  remaining_qty: DecimalValue
  purchase_price?: DecimalValue
  amount?: DecimalValue
}

export interface PurchasePlanAttachment {
  id: string
  display_name: string
  media_type: string
  size: number
  created_at: string
}

export interface PurchasePlan {
  id: number
  plan_no: string
  supplier_id: number
  supplier_name?: string | null
  plan_date?: string | null
  status: string
  audit_status: string
  total_qty: DecimalValue
  total_amount: DecimalValue
  remark?: string | null
  created_by?: number | null
  auditor_id?: number | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: PurchasePlanItem[]
  attachments?: PurchasePlanAttachment[]
  approval?: PurchaseApprovalState
}

export interface PurchaseNeedItem {
  product_id: number
  product_name?: string | null
  product_code?: string | null
  art_no?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  brand_id?: number | null
  brand_name?: string | null
  supplier_id: number
  purchase_price?: DecimalValue
  sales_20d: DecimalValue
  current_stock: DecimalValue
  in_transit_stock: DecimalValue
  in_progress_qty: DecimalValue
  suggested_qty: DecimalValue
  estimated_amount?: DecimalValue
}

export interface PurchaseNeedResult {
  total: number
  page: number
  page_size: number
  items_count: number
  items: PurchaseNeedItem[]
  sales_window_days: number
}

export interface PurchasePlanQuery {
  supplier_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface PurchasePlanItemPayload {
  product_id: number
  sku_id?: number | null
  planned_qty: string
  purchase_price?: string | null
}

export interface PurchasePlanCreatePayload {
  supplier_id: number
  plan_date?: string | null
  remark?: string | null
  items: PurchasePlanItemPayload[]
}

export interface PurchasePlanCreationRequestResult {
  status: 'NOT_FOUND' | 'PENDING' | 'SUCCEEDED' | 'FAILED'
  result: PurchasePlan | null
  message: string | null
}

export interface PurchaseOrderItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  art_no?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  brand_id?: number | null
  brand_name?: string | null
  unit_id?: number | null
  unit_name?: string | null
  purchase_price?: DecimalValue
  qty: DecimalValue
  in_transit_qty: DecimalValue
  current_stock: DecimalValue
  in_transit_stock: DecimalValue
  arrived_qty: DecimalValue
  difference_qty: DecimalValue
  amount?: DecimalValue
}

export interface PurchaseOrder {
  id: number
  order_no: string
  plan_id?: number | null
  plan_no?: string | null
  supplier_id: number
  supplier_name?: string | null
  warehouse_id?: number | null
  warehouse_name?: string | null
  purchaser_id?: number | null
  purchaser_name?: string | null
  status: string
  audit_status: string
  arrival_status: string
  total_qty: DecimalValue
  arrived_qty: DecimalValue
  difference_qty: DecimalValue
  total_amount: DecimalValue
  prepay_amount: DecimalValue
  description?: string | null
  attachments?: string[]
  inbound_warehouse_locked: boolean
  created_by?: number | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: PurchaseOrderItem[]
  approval?: PurchaseApprovalState
}

export interface PurchaseOrderQuery {
  plan_id?: number
  supplier_id?: number
  warehouse_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface PurchaseOrderItemPayload {
  product_id: number
  sku_id?: number | null
  qty: string
  purchase_price?: string | null
}

export interface PurchaseOrderCreatePayload {
  supplier_id: number
  warehouse_id?: number | null
  purchaser_id?: number | null
  description?: string | null
  attachments?: string[]
  items: PurchaseOrderItemPayload[]
}

export interface PurchaseOrderFromPlanPayload {
  plan_id: number
  warehouse_id?: number | null
  purchaser_id?: number | null
  description?: string | null
  attachments?: string[]
  items?: Array<{ plan_item_id: number; qty: string }>
}

export interface PurchasePrepay {
  id: number
  prepay_no: string
  supplier_id: number
  supplier_name?: string | null
  order_id?: number | null
  order_no?: string | null
  order_amount?: DecimalValue
  prepay_amount: DecimalValue
  write_off_amount: DecimalValue
  unwritten_amount: DecimalValue
  bank?: string | null
  account_no?: string | null
  account_balance?: DecimalValue
  pay_note?: string | null
  write_off_note?: string | null
  status: string
  audit_status: string
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  approval?: PurchaseApprovalState
  voucher?: PurchaseVoucher | null
}

export interface PurchasePrepayPayload {
  supplier_id?: number | null
  order_id?: number | null
  prepay_amount: string
  bank?: string | null
  account_no?: string | null
  account_balance?: string | null
  pay_note?: string | null
  write_off_note?: string | null
}

export interface PurchasePrepayQuery {
  supplier_id?: number
  order_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface PurchaseReturnItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  art_no?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  unit_id?: number | null
  unit_name?: string | null
  unit_price?: DecimalValue
  return_qty: DecimalValue
  amount?: DecimalValue
}

export interface PurchaseReturn {
  id: number
  return_no: string
  supplier_id: number
  supplier_name?: string | null
  order_id?: number | null
  order_no?: string | null
  warehouse_id?: number | null
  warehouse_name?: string | null
  warehouse_is_virtual?: boolean | null
  auto_out: boolean
  return_reason?: string | null
  description?: string | null
  total_qty: DecimalValue
  returned_qty: DecimalValue
  refund_amount: DecimalValue
  refund_status: string
  status: string
  audit_status: string
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  stock_out?: PurchaseReturnStockOut | null
  refund_income?: PurchaseReturnRefund | null
  created_at?: string | null
  updated_at?: string | null
  items?: PurchaseReturnItem[]
  approval?: PurchaseApprovalState
}

export interface PurchaseReturnStockOut {
  id: number
  stock_out_no: string
  warehouse_id: number
  stock_out_type: string
  status: string
  audit_status: string
  off_shelf_status: string
  total_qty: DecimalValue
  auditor_id?: number | null
  audit_time?: string | null
  created_at?: string | null
}

export interface PurchaseReturnRefund {
  id: number
  income_no: string
  supplier_id: number
  income_type: string
  amount: DecimalValue
  status: string
  audit_status: string
  description?: string | null
  created_at?: string | null
}

export interface PurchaseReturnPayload {
  supplier_id?: number | null
  order_id?: number | null
  warehouse_id: number
  auto_out?: boolean
  return_reason?: string | null
  description?: string | null
  items: Array<{
    product_id: number
    sku_id: number
    unit_id?: number | null
    unit_price?: string | null
    return_qty: string
  }>
}

export interface PurchaseReturnQuery {
  supplier_id?: number
  order_id?: number
  warehouse_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface PurchaseInvoiceCostDetail {
  stock_in_id: number
  stock_in_no?: string | null
  product_id: number
  product_name?: string | null
  product_code?: string | null
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  qty?: DecimalValue
  original_amount?: DecimalValue
  original_cost_price?: DecimalValue
  allocated_amount?: DecimalValue
  new_amount?: DecimalValue
  new_cost_price?: DecimalValue
}

export interface PurchaseInvoice {
  id: number
  invoice_no: string
  supplier_id: number
  supplier_name?: string | null
  invoice_type: string
  invoice_type_name?: string | null
  default_tax_rate?: DecimalValue
  tax_rate?: DecimalValue
  amount: DecimalValue
  tax_amount: DecimalValue
  net_amount: DecimalValue
  write_off_amount: DecimalValue
  write_off_status: string
  business_type?: string | null
  voucher_no?: string | null
  description?: string | null
  status: string
  audit_status: string
  stock_in_ids?: number[]
  stock_in_nos?: string[]
  cost_allocation?: PurchaseInvoiceCostDetail[]
  cost_accounted_at?: string | null
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  approval?: PurchaseApprovalState
}

export interface PurchaseInvoicePayload {
  supplier_id: number
  invoice_type: string
  tax_rate?: string | null
  amount: string
  business_type?: string | null
  voucher_no?: string | null
  description?: string | null
  stock_in_ids: number[]
}

export interface PurchaseInvoiceQuery {
  supplier_id?: number
  invoice_type?: string
  status?: string
  audit_status?: string
  write_off_status?: string
  business_type?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface PurchaseVoucher {
  id: number
  voucher_no: string
  voucher_date?: string | null
  status?: string | null
  source_type?: string | null
  source_id?: number | null
}

export interface WarehouseOption {
  id: number
  code: string
  name: string
  warehouse_type: string
  is_enabled: boolean
  is_virtual: boolean
  selectable: boolean
}

export interface StockInOption {
  id: number
  stock_in_no: string
  warehouse_id: number
  warehouse_name?: string | null
  purchase_order_id?: number | null
  purchase_order_no?: string | null
  status: string
  audit_status: string
  total_qty: DecimalValue
  item_count: number
}

export interface PurchaseOrderReportRow {
  supplier_id: number
  supplier_name: string
  order_count: number
  total_qty: DecimalValue
  total_amount: DecimalValue
  arrived_qty: DecimalValue
  in_transit_qty: DecimalValue
}

export interface PurchaseInboundReportRow {
  supplier_id: number
  supplier_name: string
  purchaser_id?: number | null
  purchaser_name?: string | null
  product_id: number
  product_code: string
  product_name: string
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  stock_in_count: number
  qty: DecimalValue
  amount: DecimalValue
}

export interface PurchaseInvoiceReportRow {
  supplier_id: number
  supplier_name: string
  write_off_status: string
  invoice_count: number
  amount: DecimalValue
  tax_amount: DecimalValue
  net_amount: DecimalValue
  write_off_amount: DecimalValue
}

export interface InventoryInTransitRow {
  product_id: number
  product_code: string
  product_name: string
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  current_qty: DecimalValue
  in_transit_qty: DecimalValue
  total_qty: DecimalValue
}

export interface PurchaseReportResult<T> {
  items: T[]
  summary: Record<string, string | number>
}

export interface PurchaseApprovalState {
  doc_type: string
  doc_id: number
  flow_name?: string | null
  state: {
    status: string
    current_step: number | null
    total_steps: number
    approved_steps: number[]
  }
  records: Array<{
    id: number
    step: number
    step_name?: string | null
    action: string
    status: string
    approver_id?: number | null
    approver_name?: string | null
    comment?: string | null
    reason?: string | null
    audit_time?: string | null
    created_at?: string | null
  }>
}

export interface PurchaseReportQuery {
  start_time?: string
  end_time?: string
  supplier_id?: number
  purchaser_id?: number
  product_id?: number
  warehouse_id?: number
  write_off_status?: string
  keyword?: string
}

function action<T>(
  path: string,
  data?: Record<string, unknown>,
): Promise<T> {
  return api.post<T>(path, data)
}

export const purchasePlanApi = {
  list(query: PurchasePlanQuery = {}): Promise<PageResult<PurchasePlan>> {
    return api.get<PageResult<PurchasePlan>>('/purchase-plans', { params: query })
  },
  get(id: number): Promise<PurchasePlan> {
    return api.get<PurchasePlan>(`/purchase-plans/${id}`)
  },
  downloadAttachment(planId: number, attachmentId: string): Promise<Blob> {
    return api.get<Blob>(`/purchase-plans/${planId}/attachments/${encodeURIComponent(attachmentId)}/content`, { responseType: 'blob' })
  },
  needs(
    supplierId: number,
    keyword?: string,
    page = 1,
    pageSize = 200,
  ): Promise<PurchaseNeedResult> {
    return api.get<PurchaseNeedResult>('/purchase-plans/needs', {
      params: { supplier_id: supplierId, keyword, page, page_size: pageSize },
    })
  },
  create(
    payload: PurchasePlanCreatePayload,
    options: { idempotencyKey?: string; assistantDraftId?: string } = {},
  ): Promise<PurchasePlan> {
    const headers: Record<string, string> = {}
    if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey
    if (options.assistantDraftId) headers['X-Assistant-Draft-Id'] = options.assistantDraftId
    if (!Object.keys(headers).length) {
      return api.post<PurchasePlan>('/purchase-plans', payload)
    }
    return api.post<PurchasePlan>('/purchase-plans', payload, { headers })
  },
  getCreationRequest(requestId: string): Promise<PurchasePlanCreationRequestResult> {
    return api.get<PurchasePlanCreationRequestResult>(
      `/purchase-plans/creation-requests/${encodeURIComponent(requestId)}`,
    )
  },
  update(
    id: number,
    payload: { plan_date?: string | null; remark?: string | null },
  ): Promise<PurchasePlan> {
    return api.put<PurchasePlan>(`/purchase-plans/${id}`, payload)
  },
  batchUpdateItems(
    id: number,
    payload: {
      item_ids: number[]
      planned_qty?: string
      purchase_price?: string | null
    },
  ): Promise<PurchasePlan> {
    return api.put<PurchasePlan>(`/purchase-plans/${id}/items/batch`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/purchase-plans/${id}`)
  },
  submit(id: number): Promise<PurchasePlan> {
    return action<PurchasePlan>(`/purchase-plans/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<PurchasePlan> {
    return action<PurchasePlan>(`/purchase-plans/${id}/approve`, { comment })
  },
  reject(id: number, comment?: string): Promise<PurchasePlan> {
    return action<PurchasePlan>(`/purchase-plans/${id}/reject`, { comment })
  },
  unaudit(id: number, reason: string): Promise<PurchasePlan> {
    return action<PurchasePlan>(`/purchase-plans/${id}/unaudit`, { reason })
  },
  abort(id: number): Promise<PurchasePlan> {
    return action<PurchasePlan>(`/purchase-plans/${id}/abort`)
  },
  finish(id: number): Promise<PurchasePlan> {
    return action<PurchasePlan>(`/purchase-plans/${id}/finish`)
  },
}

export const purchaseOrderApi = {
  list(query: PurchaseOrderQuery = {}): Promise<PageResult<PurchaseOrder>> {
    return api.get<PageResult<PurchaseOrder>>('/purchase-orders', {
      params: query,
    })
  },
  get(id: number): Promise<PurchaseOrder> {
    return api.get<PurchaseOrder>(`/purchase-orders/${id}`)
  },
  create(payload: PurchaseOrderCreatePayload): Promise<PurchaseOrder> {
    return api.post<PurchaseOrder>('/purchase-orders', payload)
  },
  fromPlan(payload: PurchaseOrderFromPlanPayload): Promise<PurchaseOrder> {
    return api.post<PurchaseOrder>('/purchase-orders/from-plan', payload)
  },
  update(
    id: number,
    payload: Partial<PurchaseOrderCreatePayload>,
  ): Promise<PurchaseOrder> {
    return api.put<PurchaseOrder>(`/purchase-orders/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/purchase-orders/${id}`)
  },
  submit(id: number): Promise<PurchaseOrder> {
    return action<PurchaseOrder>(`/purchase-orders/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<PurchaseOrder> {
    return action<PurchaseOrder>(`/purchase-orders/${id}/approve`, { comment })
  },
  reject(id: number, comment?: string): Promise<PurchaseOrder> {
    return action<PurchaseOrder>(`/purchase-orders/${id}/reject`, { comment })
  },
  unaudit(id: number, reason: string): Promise<PurchaseOrder> {
    return action<PurchaseOrder>(`/purchase-orders/${id}/unaudit`, { reason })
  },
  terminate(id: number, reason: string): Promise<PurchaseOrder> {
    return action<PurchaseOrder>(`/purchase-orders/${id}/terminate`, { reason })
  },
  downloadPdf(id: number): Promise<Blob> {
    return api.get<Blob>(`/purchase-orders/${id}/pdf`, {
      responseType: 'blob',
    })
  },
}

export const purchasePrepayApi = {
  list(query: PurchasePrepayQuery = {}): Promise<PageResult<PurchasePrepay>> {
    return api.get<PageResult<PurchasePrepay>>('/purchase-prepays', {
      params: query,
    })
  },
  get(id: number): Promise<PurchasePrepay> {
    return api.get<PurchasePrepay>(`/purchase-prepays/${id}`)
  },
  create(payload: PurchasePrepayPayload): Promise<PurchasePrepay> {
    return api.post<PurchasePrepay>('/purchase-prepays', payload)
  },
  update(
    id: number,
    payload: Partial<PurchasePrepayPayload>,
  ): Promise<PurchasePrepay> {
    return api.put<PurchasePrepay>(`/purchase-prepays/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/purchase-prepays/${id}`)
  },
  submit(id: number, comment?: string): Promise<PurchasePrepay> {
    return action<PurchasePrepay>(`/purchase-prepays/${id}/submit`, { comment })
  },
  approve(id: number, comment?: string): Promise<PurchasePrepay> {
    return action<PurchasePrepay>(`/purchase-prepays/${id}/approve`, { comment })
  },
  reject(id: number, comment?: string): Promise<PurchasePrepay> {
    return action<PurchasePrepay>(`/purchase-prepays/${id}/reject`, { comment })
  },
  unaudit(id: number, reason: string): Promise<PurchasePrepay> {
    return action<PurchasePrepay>(`/purchase-prepays/${id}/unaudit`, { reason })
  },
  terminate(id: number, reason: string): Promise<PurchasePrepay> {
    return action<PurchasePrepay>(`/purchase-prepays/${id}/terminate`, {
      reason,
    })
  },
  voucher(id: number): Promise<PurchaseVoucher | null> {
    return api.get<PurchaseVoucher | null>(`/purchase-prepays/${id}/voucher`)
  },
}

export const purchaseReturnApi = {
  list(query: PurchaseReturnQuery = {}): Promise<PageResult<PurchaseReturn>> {
    return api.get<PageResult<PurchaseReturn>>('/purchase-returns', {
      params: query,
    })
  },
  get(id: number): Promise<PurchaseReturn> {
    return api.get<PurchaseReturn>(`/purchase-returns/${id}`)
  },
  create(payload: PurchaseReturnPayload): Promise<PurchaseReturn> {
    return api.post<PurchaseReturn>('/purchase-returns', payload)
  },
  update(
    id: number,
    payload: Partial<PurchaseReturnPayload>,
  ): Promise<PurchaseReturn> {
    return api.put<PurchaseReturn>(`/purchase-returns/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/purchase-returns/${id}`)
  },
  submit(id: number, comment?: string): Promise<PurchaseReturn> {
    return action<PurchaseReturn>(`/purchase-returns/${id}/submit`, { comment })
  },
  approve(id: number, comment?: string): Promise<PurchaseReturn> {
    return action<PurchaseReturn>(`/purchase-returns/${id}/approve`, { comment })
  },
  reject(id: number, comment?: string): Promise<PurchaseReturn> {
    return action<PurchaseReturn>(`/purchase-returns/${id}/reject`, { comment })
  },
  unaudit(id: number, reason: string): Promise<PurchaseReturn> {
    return action<PurchaseReturn>(`/purchase-returns/${id}/unaudit`, { reason })
  },
  terminate(id: number, reason: string): Promise<PurchaseReturn> {
    return action<PurchaseReturn>(`/purchase-returns/${id}/terminate`, {
      reason,
    })
  },
  stockOut(id: number): Promise<PurchaseReturnStockOut | null> {
    return api.get<PurchaseReturnStockOut | null>(
      `/purchase-returns/${id}/stock-out`,
    )
  },
  refund(id: number): Promise<PurchaseReturnRefund | null> {
    return api.get<PurchaseReturnRefund | null>(
      `/purchase-returns/${id}/refund`,
    )
  },
}

export const purchaseInvoiceApi = {
  list(query: PurchaseInvoiceQuery = {}): Promise<PageResult<PurchaseInvoice>> {
    return api.get<PageResult<PurchaseInvoice>>('/purchase-invoices', {
      params: query,
    })
  },
  get(id: number): Promise<PurchaseInvoice> {
    return api.get<PurchaseInvoice>(`/purchase-invoices/${id}`)
  },
  create(payload: PurchaseInvoicePayload): Promise<PurchaseInvoice> {
    return api.post<PurchaseInvoice>('/purchase-invoices', payload)
  },
  update(
    id: number,
    payload: Partial<PurchaseInvoicePayload>,
  ): Promise<PurchaseInvoice> {
    return api.put<PurchaseInvoice>(`/purchase-invoices/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/purchase-invoices/${id}`)
  },
  submit(id: number, comment?: string): Promise<PurchaseInvoice> {
    return action<PurchaseInvoice>(`/purchase-invoices/${id}/submit`, {
      comment,
    })
  },
  approve(id: number, comment?: string): Promise<PurchaseInvoice> {
    return action<PurchaseInvoice>(`/purchase-invoices/${id}/approve`, {
      comment,
    })
  },
  reject(id: number, comment?: string): Promise<PurchaseInvoice> {
    return action<PurchaseInvoice>(`/purchase-invoices/${id}/reject`, {
      comment,
    })
  },
  unaudit(id: number, reason: string): Promise<PurchaseInvoice> {
    return action<PurchaseInvoice>(`/purchase-invoices/${id}/unaudit`, {
      reason,
    })
  },
  costDetail(id: number): Promise<PurchaseInvoiceCostDetail[]> {
    return api.get<PurchaseInvoiceCostDetail[]>(
      `/purchase-invoices/${id}/cost-detail`,
    )
  },
}

export interface PurchaseReferenceApi {
  listWarehouses(): Promise<WarehouseOption[]>
  listStockIns(
    keyword?: string,
    purchaseOrderId?: number,
  ): Promise<PageResult<StockInOption>>
}

export const purchaseReferenceApi: PurchaseReferenceApi = {
  listWarehouses(): Promise<WarehouseOption[]> {
    return api.get<WarehouseOption[]>('/warehouses', {
      params: { is_enabled: true },
    })
  },
  listStockIns(
    keyword?: string,
    purchaseOrderId?: number,
  ): Promise<PageResult<StockInOption>> {
    return api.get<PageResult<StockInOption>>('/stock-ins', {
      params: {
        keyword,
        purchase_order_id: purchaseOrderId,
        audit_status: 'APPROVED',
        page: 1,
        page_size: 200,
      },
    })
  },
}

export const purchaseReportApi = {
  orders(
    query: PurchaseReportQuery = {},
  ): Promise<PurchaseReportResult<PurchaseOrderReportRow>> {
    return api.get<PurchaseReportResult<PurchaseOrderReportRow>>(
      '/reports/purchase-orders',
      { params: query },
    )
  },
  inbounds(
    query: PurchaseReportQuery = {},
  ): Promise<PurchaseReportResult<PurchaseInboundReportRow>> {
    return api.get<PurchaseReportResult<PurchaseInboundReportRow>>(
      '/reports/purchase-inbounds',
      { params: query },
    )
  },
  invoices(
    query: PurchaseReportQuery = {},
  ): Promise<PurchaseReportResult<PurchaseInvoiceReportRow>> {
    return api.get<PurchaseReportResult<PurchaseInvoiceReportRow>>(
      '/reports/purchase-invoices',
      { params: query },
    )
  },
  inTransit(
    query: PurchaseReportQuery = {},
  ): Promise<PurchaseReportResult<InventoryInTransitRow>> {
    return api.get<PurchaseReportResult<InventoryInTransitRow>>(
      '/reports/inventory-in-transit',
      { params: query },
    )
  },
}

export const PURCHASE_ORDER_IMPORT_URL = '/purchase-orders/import'
export const PURCHASE_ORDER_EXPORT_URL = '/purchase-orders/export'
export const PURCHASE_ORDER_TEMPLATE_URL = '/purchase-orders/template'

