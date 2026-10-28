import { api } from '@/api/client'
import type { PageResult } from '@/api/party'
import type { DecimalValue } from '@/api/product'

export interface OrderApprovalState {
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

export interface OrderItem {
  id: number
  product_id: number
  product_code?: string | null
  product_name?: string | null
  product_image?: string | null
  sku_id?: number | null
  sku_name?: string | null
  spec_code?: string | null
  qty: DecimalValue
  price?: DecimalValue
  amount?: DecimalValue
  is_gift: boolean
}

export interface OrderRecord {
  id: number
  order_no: string
  shop_id: number
  shop_name?: string | null
  platform_id?: number | null
  platform_name?: string | null
  warehouse_id?: number | null
  warehouse_name?: string | null
  logistics_id?: number | null
  logistics_name?: string | null
  logistics_company?: string | null
  tracking_no?: string | null
  buyer_nick?: string | null
  buyer_message?: string | null
  seller_remark?: string | null
  receiver_name?: string | null
  receiver_phone?: string | null
  receiver_province?: string | null
  receiver_city?: string | null
  receiver_region?: string | null
  receiver_address?: string | null
  status: string
  status_label?: string | null
  audit_status: string
  pay_status?: string | null
  flags: string[]
  flags_label?: string | null
  order_amount: DecimalValue
  paid_amount: DecimalValue
  freight: DecimalValue
  platform_discount: DecimalValue
  seller_discount: DecimalValue
  weight?: DecimalValue
  total_qty: DecimalValue
  merge_order_no?: string | null
  parent_order_no?: string | null
  order_time?: string | null
  pay_time?: string | null
  ship_time?: string | null
  remark?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: OrderItem[]
  approval?: OrderApprovalState
}

export interface OrderQuery {
  shop_id?: number
  warehouse_id?: number
  status?: string
  audit_status?: string
  pay_status?: string
  flag?: string
  keyword?: string
  start_time?: string
  end_time?: string
  page?: number
  page_size?: number
}

export interface OrderItemPayload {
  product_id: number
  sku_id?: number | null
  sku_name?: string | null
  qty: string
  price?: string | null
  is_gift?: boolean
}

export interface OrderPayload {
  order_no?: string | null
  shop_id: number
  warehouse_id?: number | null
  logistics_id?: number | null
  buyer_nick?: string | null
  buyer_message?: string | null
  seller_remark?: string | null
  receiver_name?: string | null
  receiver_phone?: string | null
  receiver_province?: string | null
  receiver_city?: string | null
  receiver_address?: string | null
  order_time?: string | null
  pay_time?: string | null
  pay_status?: string | null
  freight?: string | null
  platform_discount?: string | null
  seller_discount?: string | null
  paid_amount?: string | null
  weight?: string | null
  flags?: string[]
  remark?: string | null
  items: OrderItemPayload[]
}

export interface OrderFlag {
  code: string
  label: string
}

export interface OrderLog {
  source: string
  action: string
  operator_id?: number | null
  operator_name?: string | null
  summary?: string | null
  reason?: string | null
  detail?: unknown
  created_at?: string | null
}

export interface WaveStrategy {
  id: number
  name: string
  logistics_id?: number | null
  logistics_name?: string | null
  warehouse_id?: number | null
  warehouse_name?: string | null
  shop_ids: number[]
  wave_order_count: number
  load_no_type: string
  sort_type: string
  is_enabled: boolean
  description?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface WaveStrategyPayload {
  name: string
  logistics_id?: number | null
  warehouse_id?: number | null
  shop_ids?: number[]
  wave_order_count?: number
  load_no_type?: string
  sort_type?: string
  is_enabled?: boolean
  description?: string | null
}

export interface WaveOrder {
  id: number
  order_no: string
  shop_id: number
  warehouse_id?: number | null
  warehouse_name?: string | null
  logistics_id?: number | null
  logistics_name?: string | null
  tracking_no?: string | null
  receiver_name?: string | null
  receiver_phone?: string | null
  paid_amount?: DecimalValue
  status: string
  order_time?: string | null
  load_no?: string | null
  sequence: number
  sort_key?: string | null
}

export interface WaveLoadNumber {
  id: number
  wave_id: number
  load_no: string
  express_type: string
}

export interface WaveRecord {
  id: number
  wave_no: string
  strategy_id?: number | null
  name: string
  express_type?: string | null
  total_order_count: number
  created_by?: number | null
  created_by_name?: string | null
  created_at?: string | null
  updated_at?: string | null
  load_numbers: WaveLoadNumber[]
  orders?: WaveOrder[]
}

export interface RunWaveResult {
  strategy_count: number
  wave_count: number
  order_count: number
  waves: WaveRecord[]
}

export interface WaybillItem {
  waybill_id: number
  order_id: number
  order_no: string
  waybill_no?: string | null
  print_type: string
  is_printed: boolean
}

export interface PrintBatchOrder {
  id: number
  order_no: string
  receiver_name?: string | null
  receiver_phone?: string | null
  tracking_no?: string | null
  wave_id?: number | null
  wave_no?: string | null
  load_no?: string | null
}

export interface PrintBatch {
  batch_no: string
  print_type: string
  printer_name?: string | null
  template?: Record<string, unknown> | null
  include_shipping_slip: boolean
  sort_type: string
  order_count: number
  created_at?: string | null
  orders: PrintBatchOrder[]
  waybills: WaybillItem[]
  pick_list?: Record<string, unknown> | null
  delivery_lists: Array<Record<string, unknown>>
  shipping_slips: Array<Record<string, unknown>>
}

export interface PrintBatchPayload {
  order_ids: number[]
  print_type?: string
  printer_name?: string | null
  template_id?: number | null
  include_shipping_slip?: boolean
  sort_type?: string
  waybill_nos?: Record<string, string> | null
}

export interface ShipmentItem {
  id: number
  order_item_id: number
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  qty: DecimalValue
  cost_price?: DecimalValue
  barcode?: string | null
  is_gift: boolean
}

export interface Shipment {
  id: number
  shipment_no: string
  order_id: number
  order_no?: string | null
  warehouse_id: number
  stock_out_id?: number | null
  stock_out_no?: string | null
  package_no?: number | null
  tracking_no?: string | null
  shipping_type: string
  source: string
  status: string
  expected_weight_g?: DecimalValue
  actual_weight_g?: DecimalValue
  weight_tolerance_g?: DecimalValue
  weight_out_of_range: boolean
  weight_overridden: boolean
  platform_sync_status: string
  platform_sync_message?: string | null
  shipped_by?: number | null
  shipped_by_name?: string | null
  shipped_at?: string | null
  remark?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: ShipmentItem[]
}

export interface ScanShipmentPayload {
  scan_code: string
  barcode?: string | null
  qty?: string
  tracking_no?: string | null
  package_no?: number | null
  actual_weight_g?: string | null
  weight_tolerance_g?: string | null
  allow_weight_override?: boolean
  remark?: string | null
}

export interface ScanShipmentResult {
  scan_code: string
  scan_source: string
  barcode?: string | null
  completed: boolean
  package_count: number
  remaining: Record<string, string>
  shipment: Shipment
}

export interface ReturnItem {
  id: number
  product_id: number
  product_code?: string | null
  product_name?: string | null
  product_image?: string | null
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  qty: DecimalValue
  refund_amount?: DecimalValue
}

export interface ReturnRegister {
  id: number
  return_order_id?: number | null
  exchange_order_id?: number | null
  target_type?: string | null
  target_id?: number | null
  target_no?: string | null
  logistics_company?: string | null
  tracking_no?: string | null
  sender?: string | null
  sender_phone?: string | null
  product_info?: string | null
  image?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface AfterSaleRecord {
  id: number
  return_no?: string
  exchange_no?: string
  order_id: number
  order_no?: string | null
  order_status?: string | null
  shop_id: number
  shop_name?: string | null
  warehouse_id?: number | null
  warehouse_name?: string | null
  status: string
  status_label?: string | null
  refund_amount?: DecimalValue
  return_qty?: DecimalValue
  exchange_qty?: DecimalValue
  logistics_company?: string | null
  tracking_no?: string | null
  reason?: string | null
  buyer_phone?: string | null
  is_arrived?: boolean
  freight?: DecimalValue
  freight_refunded?: boolean
  is_rejected_sign?: boolean
  return_person?: string | null
  goods_status?: string | null
  need_return_goods?: boolean
  stock_in_id?: number | null
  stock_in_no?: string | null
  stock_in_status?: string | null
  audit_time?: string | null
  product_info?: string | null
  is_cod?: boolean
  is_return?: boolean
  compensate_order_no?: string | null
  compensate_amount?: DecimalValue
  compensate_direction?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: Array<ReturnItem & { exchange_qty?: DecimalValue }>
  register?: ReturnRegister | null
}

export interface AfterSaleQuery {
  order_id?: number
  shop_id?: number
  warehouse_id?: number
  status?: string
  keyword?: string
  start_time?: string
  end_time?: string
  page?: number
  page_size?: number
}

export interface ConfirmReturnPayload {
  location_id?: number | null
  comment?: string | null
}

export interface ReturnItemPayload {
  product_id: number
  sku_id?: number | null
  qty: string
  refund_amount?: string | null
}

export interface ReturnCreatePayload {
  order_id: number
  warehouse_id: number
  reason?: string | null
  logistics_company?: string | null
  tracking_no?: string | null
  buyer_phone?: string | null
  is_arrived?: boolean
  freight?: string | null
  is_rejected_sign?: boolean
  return_person?: string | null
  goods_status?: string | null
  need_return_goods?: boolean
  items: ReturnItemPayload[]
}

export interface ExchangeItemPayload {
  product_id: number
  sku_id?: number | null
  qty?: string | null
  exchange_qty: string
}

export interface ExchangeCreatePayload {
  order_id: number
  reason?: string | null
  is_cod?: boolean
  is_return?: boolean
  compensate_order_no?: string | null
  compensate_amount?: string | null
  compensate_direction?: string | null
  register_id?: number | null
  items: ExchangeItemPayload[]
}

export interface ReturnRegisterPayload {
  return_order_id?: number | null
  exchange_order_id?: number | null
  logistics_company?: string | null
  tracking_no?: string | null
  sender?: string | null
  sender_phone?: string | null
  product_info?: string | null
  image?: string | null
}

export const ORDER_TEMPLATE_URL = '/orders/template'
export const ORDER_EXPORT_URL = '/orders/export'
export const AFTER_SALE_RETURN_EXPORT_URL = '/after-sales/returns/export'

export const orderApi = {
  list(query: OrderQuery = {}): Promise<PageResult<OrderRecord>> {
    return api.get<PageResult<OrderRecord>>('/orders', { params: query })
  },
  get(id: number): Promise<OrderRecord> {
    return api.get<OrderRecord>(`/orders/${id}`)
  },
  create(payload: OrderPayload): Promise<OrderRecord> {
    return api.post<OrderRecord>('/orders', payload)
  },
  update(id: number, payload: Partial<OrderPayload>): Promise<OrderRecord> {
    return api.put<OrderRecord>(`/orders/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/orders/${id}`)
  },
  submit(id: number): Promise<OrderRecord> {
    return api.post<OrderRecord>(`/orders/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<OrderRecord> {
    return api.post<OrderRecord>(`/orders/${id}/approve`, { comment })
  },
  reject(id: number, comment: string): Promise<OrderRecord> {
    return api.post<OrderRecord>(`/orders/${id}/reject`, { comment })
  },
  unaudit(id: number, reason: string): Promise<OrderRecord> {
    return api.post<OrderRecord>(`/orders/${id}/unaudit`, { reason })
  },
  setWarehouse(id: number, warehouseId: number): Promise<OrderRecord> {
    return api.post<OrderRecord>(`/orders/${id}/warehouse`, {
      warehouse_id: warehouseId,
    })
  },
  setLogistics(
    id: number,
    payload: {
      logistics_id?: number | null
      logistics_company?: string | null
      tracking_no?: string | null
    },
  ): Promise<OrderRecord> {
    return api.post<OrderRecord>(`/orders/${id}/logistics`, payload)
  },
  setFlags(
    id: number,
    flags: string[],
    mode: 'SET' | 'ADD' | 'REMOVE' = 'SET',
  ): Promise<OrderRecord> {
    return api.post<OrderRecord>(`/orders/${id}/flags`, { flags, mode })
  },
  logs(id: number): Promise<OrderLog[]> {
    return api.get<OrderLog[]>(`/orders/${id}/logs`)
  },
  flags(): Promise<OrderFlag[]> {
    return api.get<OrderFlag[]>('/orders/flags')
  },
  async downloadTemplate(): Promise<Blob> {
    return api.get<Blob>(ORDER_TEMPLATE_URL, { responseType: 'blob' })
  },
  async export(query: OrderQuery = {}): Promise<Blob> {
    return api.get<Blob>(ORDER_EXPORT_URL, {
      params: query,
      responseType: 'blob',
    })
  },
  async import(file: File): Promise<Record<string, unknown>> {
    const form = new FormData()
    form.append('file', file)
    return api.post<Record<string, unknown>>('/orders/import', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

export const waveApi = {
  listStrategies(
    query: {
      keyword?: string
      is_enabled?: boolean
      page?: number
      page_size?: number
    } = {},
  ): Promise<PageResult<WaveStrategy>> {
    return api.get<PageResult<WaveStrategy>>('/wave-strategies', {
      params: query,
    })
  },
  createStrategy(payload: WaveStrategyPayload): Promise<WaveStrategy> {
    return api.post<WaveStrategy>('/wave-strategies', payload)
  },
  updateStrategy(
    id: number,
    payload: Partial<WaveStrategyPayload>,
  ): Promise<WaveStrategy> {
    return api.put<WaveStrategy>(`/wave-strategies/${id}`, payload)
  },
  removeStrategy(id: number): Promise<void> {
    return api.delete<void>(`/wave-strategies/${id}`)
  },
  pendingCount(
    id: number,
    orderIds?: number[],
  ): Promise<{ strategy_id: number; pending_count: number }> {
    return api.get<{ strategy_id: number; pending_count: number }>(
      `/wave-strategies/${id}/pending-count`,
      { params: { order_ids: orderIds } },
    )
  },
  run(payload: {
    strategy_ids: number[]
    order_ids?: number[] | null
  }): Promise<RunWaveResult> {
    return api.post<RunWaveResult>('/waves/run', payload)
  },
  list(
    query: {
      strategy_id?: number
      express_type?: string
      keyword?: string
      start_time?: string
      end_time?: string
      page?: number
      page_size?: number
    } = {},
  ): Promise<PageResult<WaveRecord>> {
    return api.get<PageResult<WaveRecord>>('/waves', { params: query })
  },
  get(id: number): Promise<WaveRecord> {
    return api.get<WaveRecord>(`/waves/${id}`)
  },
  loadNumbers(id: number): Promise<WaveLoadNumber[]> {
    return api.get<WaveLoadNumber[]>(`/waves/${id}/load-numbers`)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/waves/${id}`)
  },
}

export const orderPrintApi = {
  acquireWaybills(
    orderIds: number[],
    printType: string,
    waybillNos?: Record<string, string>,
  ): Promise<{
    print_type: string
    source: string
    count: number
    items: WaybillItem[]
  }> {
    return api.post('/print/waybills/acquire', {
      order_ids: orderIds,
      print_type: printType,
      waybill_nos: waybillNos,
    })
  },
  recycleWaybills(orderIds: number[]): Promise<{
    count: number
    items: WaybillItem[]
    skipped: Array<{ order_id: number; reason: string }>
  }> {
    return api.post('/print/waybills/recycle', { order_ids: orderIds })
  },
  createBatch(payload: PrintBatchPayload): Promise<PrintBatch> {
    return api.post<PrintBatch>('/print/batches', payload)
  },
  getBatch(batchNo: string): Promise<PrintBatch> {
    return api.get<PrintBatch>(
      `/print/batches/${encodeURIComponent(batchNo)}`,
    )
  },
  async downloadPdf(
    batchNo: string,
    documentType = 'ALL',
  ): Promise<Blob> {
    return api.get<Blob>(
      `/print/batches/${encodeURIComponent(batchNo)}/pdf`,
      {
        params: { document_type: documentType },
        responseType: 'blob',
      },
    )
  },
}

export const shipmentApi = {
  scan(payload: ScanShipmentPayload): Promise<ScanShipmentResult> {
    return api.post<ScanShipmentResult>('/shipments/scan', payload)
  },
  list(
    query: {
      order_id?: number
      status?: string
      tracking_no?: string
      platform_sync_status?: string
      start_time?: string
      end_time?: string
      page?: number
      page_size?: number
    } = {},
  ): Promise<PageResult<Shipment>> {
    return api.get<PageResult<Shipment>>('/shipments', { params: query })
  },
  listByOrder(orderId: number): Promise<Shipment[]> {
    return api.get<Shipment[]>(`/orders/${orderId}/shipments`)
  },
  get(id: number): Promise<Shipment> {
    return api.get<Shipment>(`/shipments/${id}`)
  },
  ship(
    orderId: number,
    payload: {
      tracking_no?: string | null
      package_no?: number | null
      actual_weight_g?: string | null
      weight_tolerance_g?: string | null
      allow_weight_override?: boolean
      remark?: string | null
    } = {},
  ): Promise<{
    completed: boolean
    package_count: number
    shipment: Shipment
  }> {
    return api.post(`/orders/${orderId}/ship`, payload)
  },
  pickup(
    orderId: number,
    payload: {
      package_no?: number | null
      actual_weight_g?: string | null
      weight_tolerance_g?: string | null
      allow_weight_override?: boolean
      remark?: string | null
    } = {},
  ): Promise<{
    completed: boolean
    package_count: number
    shipment: Shipment
  }> {
    return api.post(`/orders/${orderId}/pickup`, payload)
  },
  syncPlatform(
    shipmentId: number,
    status: string,
    message?: string | null,
  ): Promise<Shipment> {
    return api.post<Shipment>(`/shipments/${shipmentId}/platform-sync`, {
      status,
      message,
    })
  },
}

export const afterSaleApi = {
  listReturns(query: AfterSaleQuery = {}): Promise<PageResult<AfterSaleRecord>> {
    return api.get<PageResult<AfterSaleRecord>>('/after-sales/returns', {
      params: query,
    })
  },
  createReturn(payload: ReturnCreatePayload): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>('/after-sales/returns', payload)
  },
  getReturn(id: number): Promise<AfterSaleRecord> {
    return api.get<AfterSaleRecord>(`/after-sales/returns/${id}`)
  },
  updateReturn(
    id: number,
    payload: Partial<ReturnCreatePayload>,
  ): Promise<AfterSaleRecord> {
    return api.put<AfterSaleRecord>(`/after-sales/returns/${id}`, payload)
  },
  removeReturn(id: number): Promise<void> {
    return api.delete<void>(`/after-sales/returns/${id}`)
  },
  submitReturn(id: number): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>(`/after-sales/returns/${id}/submit`)
  },
  confirmReturn(
    id: number,
    payload: ConfirmReturnPayload = {},
  ): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>(
      `/after-sales/returns/${id}/confirm`,
      payload,
    )
  },
  cancelReturn(id: number, reason?: string): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>(`/after-sales/returns/${id}/cancel`, {
      reason,
    })
  },
  updateReturnLogistics(
    id: number,
    payload: { logistics_company?: string | null; tracking_no?: string | null },
  ): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>(
      `/after-sales/returns/${id}/logistics`,
      payload,
    )
  },
  attachReturnRegister(id: number, registerId: number): Promise<ReturnRegister> {
    return api.post<ReturnRegister>(`/after-sales/returns/${id}/register`, {
      register_id: registerId,
    })
  },
  detachReturnRegister(id: number): Promise<void> {
    return api.delete<void>(`/after-sales/returns/${id}/register`)
  },
  async exportReturns(query: AfterSaleQuery = {}): Promise<Blob> {
    return api.get<Blob>(AFTER_SALE_RETURN_EXPORT_URL, {
      params: query,
      responseType: 'blob',
    })
  },
  createExchange(payload: ExchangeCreatePayload): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>('/after-sales/exchanges', payload)
  },
  listExchanges(
    query: AfterSaleQuery = {},
  ): Promise<PageResult<AfterSaleRecord>> {
    return api.get<PageResult<AfterSaleRecord>>('/after-sales/exchanges', {
      params: query,
    })
  },
  getExchange(id: number): Promise<AfterSaleRecord> {
    return api.get<AfterSaleRecord>(`/after-sales/exchanges/${id}`)
  },
  updateExchange(
    id: number,
    payload: Partial<ExchangeCreatePayload>,
  ): Promise<AfterSaleRecord> {
    return api.put<AfterSaleRecord>(`/after-sales/exchanges/${id}`, payload)
  },
  removeExchange(id: number): Promise<void> {
    return api.delete<void>(`/after-sales/exchanges/${id}`)
  },
  submitExchange(id: number): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>(`/after-sales/exchanges/${id}/submit`)
  },
  cancelExchange(id: number, reason?: string): Promise<AfterSaleRecord> {
    return api.post<AfterSaleRecord>(`/after-sales/exchanges/${id}/cancel`, {
      reason,
    })
  },
  attachExchangeRegister(
    id: number,
    registerId: number,
  ): Promise<ReturnRegister> {
    return api.post<ReturnRegister>(`/after-sales/exchanges/${id}/register`, {
      register_id: registerId,
    })
  },
  detachExchangeRegister(id: number): Promise<void> {
    return api.delete<void>(`/after-sales/exchanges/${id}/register`)
  },
  listRegisters(bound?: boolean): Promise<ReturnRegister[]> {
    return api.get<ReturnRegister[]>('/after-sales/registers', {
      params: { bound },
    })
  },
  createRegister(payload: ReturnRegisterPayload): Promise<ReturnRegister> {
    return api.post<ReturnRegister>('/after-sales/registers', payload)
  },
  updateRegister(
    id: number,
    payload: Partial<ReturnRegisterPayload>,
  ): Promise<ReturnRegister> {
    return api.put<ReturnRegister>(`/after-sales/registers/${id}`, payload)
  },
  removeRegister(id: number): Promise<void> {
    return api.delete<void>(`/after-sales/registers/${id}`)
  },
}
