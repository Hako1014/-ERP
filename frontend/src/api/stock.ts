import { api } from '@/api/client'
import type { PageResult } from '@/api/party'
import type { DecimalValue } from '@/api/product'

export interface StockApprovalState {
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

export interface Warehouse {
  id: number
  code: string
  name: string
  region?: string | null
  address?: string | null
  warehouse_type: string
  is_default: boolean
  sync_stock: boolean
  enable_location: boolean
  zero_stock_out: boolean
  is_virtual: boolean
  ship_regions?: string | null
  is_enabled: boolean
  lock_type: string
  remark?: string | null
  selectable: boolean
}

export interface WarehouseQuery {
  keyword?: string
  warehouse_type?: string
  is_enabled?: boolean
  selectable_only?: boolean
}

export interface WarehousePayload {
  code: string
  name: string
  region?: string | null
  address?: string | null
  warehouse_type: string
  is_default?: boolean
  sync_stock?: boolean
  enable_location?: boolean
  zero_stock_out?: boolean
  is_virtual?: boolean
  ship_regions?: string | null
  is_enabled?: boolean
  lock_type?: string
  remark?: string | null
}

export interface Zone {
  id: number
  warehouse_id: number
  warehouse_name?: string | null
  code: string
  name?: string | null
  sort_order: number
}

export interface ZonePayload {
  warehouse_id: number
  code: string
  name?: string | null
  sort_order?: number
}

export interface Location {
  id: number
  warehouse_id: number
  warehouse_name?: string | null
  zone_id?: number | null
  zone_code?: string | null
  zone_name?: string | null
  code: string
  row_no: number
  col_no: number
  layer_no: number
  capacity: number
  used_qty: DecimalValue
  remaining_qty?: DecimalValue
  priority: number
  sort_order: number
  is_virtual: boolean
  is_enabled: boolean
}

export interface LocationQuery {
  warehouse_id?: number
  zone_id?: number
  is_enabled?: boolean
  keyword?: string
}

export interface LocationPayload {
  warehouse_id?: number
  zone_id?: number | null
  code?: string | null
  row_no?: number
  col_no?: number
  layer_no?: number
  capacity?: number
  priority?: number
  sort_order?: number
  is_virtual?: boolean
  is_enabled?: boolean
}

export interface LocationGeneratePayload {
  warehouse_id: number
  zone_id: number
  row_count: number
  col_count: number
  layer_count: number
  capacity?: number
  priority?: number
  sort_order?: number
  is_virtual?: boolean
  is_enabled?: boolean
}

export interface LocationCapacity {
  location_id: number
  capacity: number
  used_qty: DecimalValue
  remaining_qty: DecimalValue
  unlimited: boolean
}

export interface StockInItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  qty: DecimalValue
  cost_price?: DecimalValue
  amount?: DecimalValue
  barcode?: string | null
  putaway_qty: DecimalValue
  pending_putaway_qty: DecimalValue
}

export interface StockIn {
  id: number
  stock_in_no: string
  warehouse_id: number
  warehouse_name?: string | null
  stock_in_type: string
  purchase_order_id?: number | null
  purchase_order_no?: string | null
  status: string
  audit_status: string
  total_qty: DecimalValue
  putaway_qty: DecimalValue
  pending_putaway_qty: DecimalValue
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  item_count: number
  items?: StockInItem[]
  approval?: StockApprovalState
}

export interface StockInQuery {
  warehouse_id?: number
  purchase_order_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface StockInItemPayload {
  product_id: number
  sku_id?: number | null
  qty: string
  cost_price?: string | null
  barcode?: string | null
}

export interface StockInPayload {
  warehouse_id: number
  purchase_order_id?: number | null
  stock_in_type: string
  remark?: string | null
  items: StockInItemPayload[]
}

export interface PendingPutaway {
  warehouse_id: number
  warehouse_name?: string | null
  product_id: number
  product_name?: string | null
  product_code?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  received_qty: DecimalValue
  putaway_qty: DecimalValue
  pending_putaway_qty: DecimalValue
}

export interface Putaway {
  id: number
  source_type: string
  source_id: number
  warehouse_id: number
  location_id: number
  location_code?: string | null
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  qty: DecimalValue
  created_at?: string | null
}

export interface PutawayPayloadItem {
  stock_in_item_id: number
  location_id: number
  qty: string
}

export interface StockOutItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  location_id?: number | null
  location_code?: string | null
  qty: DecimalValue
  cost_price?: DecimalValue
  barcode?: string | null
}

export interface StockOut {
  id: number
  stock_out_no: string
  warehouse_id: number
  warehouse_name?: string | null
  stock_out_type: string
  purchase_return_id?: number | null
  purchase_return_no?: string | null
  status: string
  audit_status: string
  off_shelf_status: string
  total_qty: DecimalValue
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: StockOutItem[]
  approval?: StockApprovalState
}

export interface StockOutQuery {
  warehouse_id?: number
  purchase_return_id?: number
  status?: string
  audit_status?: string
  off_shelf_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface StockOutPayload {
  purchase_return_id: number
  remark?: string | null
}

export interface TransferItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  sku_id: number
  spec_name?: string | null
  spec_code?: string | null
  qty: DecimalValue
  available_qty: DecimalValue
  barcode?: string | null
}

export interface Transfer {
  id: number
  transfer_no: string
  from_warehouse_id: number
  from_warehouse_name?: string | null
  to_warehouse_id: number
  to_warehouse_name?: string | null
  sync_stock: boolean
  status: string
  audit_status: string
  total_qty: DecimalValue
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: TransferItem[]
  transfer_out?: TransferOut | null
  transfer_in?: TransferIn | null
  approval?: StockApprovalState
}

export interface TransferQuery {
  from_warehouse_id?: number
  to_warehouse_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface TransferItemPayload {
  product_id: number
  sku_id: number
  qty: string
  barcode?: string | null
}

export interface TransferPayload {
  from_warehouse_id: number
  to_warehouse_id: number
  sync_stock?: boolean
  remark?: string | null
  items: TransferItemPayload[]
}

export interface TransferOutItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  location_id?: number | null
  location_code?: string | null
  qty: DecimalValue
}

export interface TransferOut {
  id: number
  transfer_out_no: string
  transfer_id: number
  warehouse_id: number
  warehouse_name?: string | null
  status: string
  audit_status: string
  total_qty: DecimalValue
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: TransferOutItem[]
  approval?: StockApprovalState
}

export interface TransferInItem {
  id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  sku_id?: number | null
  spec_name?: string | null
  spec_code?: string | null
  location_id?: number | null
  location_code?: string | null
  qty: DecimalValue
}

export interface TransferIn {
  id: number
  transfer_in_no: string
  transfer_id: number
  warehouse_id: number
  warehouse_name?: string | null
  status: string
  audit_status: string
  total_qty: DecimalValue
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: TransferInItem[]
  approval?: StockApprovalState
}

export interface TransferDocQuery {
  transfer_id?: number
  warehouse_id?: number
  status?: string
  audit_status?: string
  page?: number
  page_size?: number
}

export interface LocationOperationItem {
  id: number
  location_id: number
  qty: string
}

export interface StockCheckItem {
  id: number
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  barcode?: string | null
  location_id: number
  location_code?: string | null
  current_qty: DecimalValue
  actual_qty?: DecimalValue
  difference_qty?: DecimalValue
}

export interface StockCheck {
  id: number
  check_no: string
  warehouse_id: number
  warehouse_name?: string | null
  lock_warehouse: boolean
  warehouse_locked: boolean
  status: string
  audit_status: string
  remark?: string | null
  has_imported_result: boolean
  item_count: number
  result_entered_count: number
  current_total_qty: DecimalValue
  actual_total_qty: DecimalValue
  gain_total_qty: DecimalValue
  loss_total_qty: DecimalValue
  created_by?: number | null
  created_by_name?: string | null
  check_time?: string | null
  created_at?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  updated_at?: string | null
  items?: StockCheckItem[]
  approval?: StockApprovalState
}

export interface StockCheckQuery {
  warehouse_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface StockCheckItemPayload {
  product_id: number
  sku_id: number
  location_id: number
  actual_qty?: string | null
  barcode?: string | null
}

export interface StockCheckPayload {
  warehouse_id: number
  lock_warehouse: boolean
  remark?: string | null
  items?: StockCheckItemPayload[]
}

export interface StockFlow {
  id: number
  warehouse_id: number
  warehouse_name?: string | null
  product_id: number
  product_code?: string | null
  product_name?: string | null
  product_image?: string | null
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  location_id?: number | null
  location_code?: string | null
  unit_id?: number | null
  unit_name?: string | null
  direction: string
  biz_type: string
  qty: DecimalValue
  cost_price?: DecimalValue
  total_cost?: DecimalValue
  source_no?: string | null
  order_no?: string | null
  flow_time?: string | null
  remark?: string | null
  created_at?: string | null
}

export interface StockFlowQuery {
  warehouse_id?: number
  product_id?: number
  sku_id?: number
  location_id?: number
  direction?: string
  biz_type?: string
  source_no?: string
  order_no?: string
  start_time?: string
  end_time?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface StockSummary {
  warehouse_id: number
  warehouse_name?: string | null
  product_id: number
  product_code?: string | null
  product_name?: string | null
  product_image?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  normal_qty: DecimalValue
  defective_qty: DecimalValue
  total_qty: DecimalValue
  occupied_qty: DecimalValue
  available_qty: DecimalValue
  shortage_qty: DecimalValue
  warn_qty: DecimalValue
  is_below_warning: boolean
  cost_price?: DecimalValue
  stock_cost?: DecimalValue
}

export interface StockSummaryQuery {
  warehouse_id?: number
  product_id?: number
  sku_id?: number
  keyword?: string
  below_warning?: boolean
  page?: number
  page_size?: number
}

export interface StockLocationRow {
  stock_location_id: number
  warehouse_id: number
  warehouse_name?: string | null
  location_id: number
  location_code: string
  location_capacity: number
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  status: string
  qty: DecimalValue
}

export interface StockWarning {
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  current_qty: DecimalValue
  warn_qty: DecimalValue
  is_below_warning: boolean
}

export interface StockCost {
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  cost_price?: DecimalValue
  qty: DecimalValue
  total_cost?: DecimalValue
}

export interface StockManualPayload {
  warehouse_id: number
  location_id: number
  product_id: number
  sku_id: number
  qty: string
  cost_price?: string | null
  remark?: string | null
}

export interface LocationAdjustItem {
  id: number
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  from_location_id: number
  from_location_code?: string | null
  to_location_id: number
  to_location_code?: string | null
  qty: DecimalValue
}

export interface LocationAdjust {
  id: number
  adjust_no: string
  warehouse_id: number
  warehouse_name?: string | null
  status: string
  audit_status: string
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  auditor_id?: number | null
  auditor_name?: string | null
  audit_time?: string | null
  created_at?: string | null
  updated_at?: string | null
  items?: LocationAdjustItem[]
  approval?: StockApprovalState
}

export interface LocationAdjustQuery {
  warehouse_id?: number
  status?: string
  audit_status?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface LocationAdjustItemPayload {
  product_id: number
  sku_id: number
  from_location_id: number
  to_location_id: number
  qty: string
}

export interface LocationAdjustPayload {
  warehouse_id: number
  remark?: string | null
  items: LocationAdjustItemPayload[]
}

export interface ShopStock {
  id: number
  shop_id: number
  shop_name?: string | null
  platform_id?: number | null
  platform_name?: string | null
  shop_type?: string | null
  warehouse_id: number
  warehouse_name?: string | null
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id: number
  spec_code?: string | null
  spec_name?: string | null
  exclusive_qty: DecimalValue
  stock_qty: DecimalValue
  occupied_qty: DecimalValue
  available_qty: DecimalValue
  shop_available_qty: DecimalValue
  is_sync: boolean
  is_enabled: boolean
  is_effective: boolean
  effective_start?: string | null
  effective_end?: string | null
  last_sync_qty?: DecimalValue
  last_sync_at?: string | null
  remark?: string | null
  created_by?: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ShopStockQuery {
  shop_id?: number
  warehouse_id?: number
  product_id?: number
  sku_id?: number
  is_enabled?: boolean
  keyword?: string
  page?: number
  page_size?: number
}

export interface ShopStockPayload {
  shop_id?: number
  warehouse_id?: number
  product_id?: number
  sku_id?: number
  exclusive_qty?: string
  is_sync?: boolean
  is_enabled?: boolean
  effective_start?: string | null
  effective_end?: string | null
  remark?: string | null
}

export interface StockSetting {
  id: number
  company_id: number
  sync_stock_default: boolean
  shop_stock_enabled: boolean
  floating_cost_enabled: boolean
  off_shelf_below_warning: boolean
  updated_at?: string | null
}

export interface StockSettingPayload {
  sync_stock_default?: boolean
  shop_stock_enabled?: boolean
  floating_cost_enabled?: boolean
  off_shelf_below_warning?: boolean
}

export interface StockSettlement {
  id: number
  settlement_date: string
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  qty: DecimalValue
  cost_price?: DecimalValue
  total_cost?: DecimalValue
}

export interface SettlementPeriod {
  settlement_date: string
  item_count: number
  total_qty: DecimalValue
  total_cost: DecimalValue
}

export interface SettlementQuery {
  settlement_date?: string
  product_id?: number
  sku_id?: number
  keyword?: string
  page?: number
  page_size?: number
}

export interface CostRewriteLog {
  id: number
  batch_no: string
  period_start?: string | null
  period_end?: string | null
  target_type: string
  target_id: number
  warehouse_id?: number | null
  warehouse_name?: string | null
  product_id: number
  product_code?: string | null
  product_name?: string | null
  sku_id?: number | null
  spec_code?: string | null
  spec_name?: string | null
  qty: DecimalValue
  cost_source?: string | null
  old_cost_price?: DecimalValue
  new_cost_price?: DecimalValue
  old_total_cost?: DecimalValue
  new_total_cost?: DecimalValue
  created_by?: number | null
  created_by_name?: string | null
  created_at?: string | null
}

export interface CostRewriteLogQuery {
  batch_no?: string
  product_id?: number
  sku_id?: number
  target_type?: string
  start_time?: string
  end_time?: string
  keyword?: string
  page?: number
  page_size?: number
}

function action<T>(
  path: string,
  data?: Record<string, unknown>,
): Promise<T> {
  return api.post<T>(path, data)
}

export const warehouseApi = {
  list(query: WarehouseQuery = {}): Promise<Warehouse[]> {
    return api.get<Warehouse[]>('/warehouses', { params: query })
  },
  get(id: number): Promise<Warehouse> {
    return api.get<Warehouse>(`/warehouses/${id}`)
  },
  create(payload: WarehousePayload): Promise<Warehouse> {
    return api.post<Warehouse>('/warehouses', payload)
  },
  update(id: number, payload: Partial<WarehousePayload>): Promise<Warehouse> {
    return api.put<Warehouse>(`/warehouses/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/warehouses/${id}`)
  },
  enable(id: number): Promise<Warehouse> {
    return action<Warehouse>(`/warehouses/${id}/enable`)
  },
  disable(id: number): Promise<Warehouse> {
    return action<Warehouse>(`/warehouses/${id}/disable`)
  },
  setDefault(id: number): Promise<Warehouse> {
    return action<Warehouse>(`/warehouses/${id}/default`)
  },
  lock(id: number): Promise<Warehouse> {
    return action<Warehouse>(`/warehouses/${id}/lock`)
  },
  unlock(id: number): Promise<Warehouse> {
    return action<Warehouse>(`/warehouses/${id}/unlock`)
  },
  listZones(warehouseId: number): Promise<Zone[]> {
    return api.get<Zone[]>(`/warehouses/${warehouseId}/zones`)
  },
  getZone(id: number): Promise<Zone> {
    return api.get<Zone>(`/zones/${id}`)
  },
  createZone(payload: ZonePayload): Promise<Zone> {
    return api.post<Zone>('/zones', payload)
  },
  updateZone(id: number, payload: Partial<ZonePayload>): Promise<Zone> {
    return api.put<Zone>(`/zones/${id}`, payload)
  },
  removeZone(id: number): Promise<void> {
    return api.delete<void>(`/zones/${id}`)
  },
  listLocations(query: LocationQuery = {}): Promise<Location[]> {
    return api.get<Location[]>('/locations', { params: query })
  },
  getLocation(id: number): Promise<Location> {
    return api.get<Location>(`/locations/${id}`)
  },
  getLocationCapacity(id: number): Promise<LocationCapacity> {
    return api.get<LocationCapacity>(`/locations/${id}/capacity`)
  },
  createLocation(payload: LocationPayload): Promise<Location> {
    return api.post<Location>('/locations', payload)
  },
  updateLocation(
    id: number,
    payload: Partial<LocationPayload>,
  ): Promise<Location> {
    return api.put<Location>(`/locations/${id}`, payload)
  },
  removeLocation(id: number): Promise<void> {
    return api.delete<void>(`/locations/${id}`)
  },
  generateLocations(
    payload: LocationGeneratePayload,
  ): Promise<{ generated_count: number; items: Location[] }> {
    return api.post<{ generated_count: number; items: Location[] }>(
      '/locations/generate',
      payload,
    )
  },
  enableLocation(id: number): Promise<Location> {
    return action<Location>(`/locations/${id}/enable`)
  },
  disableLocation(id: number): Promise<Location> {
    return action<Location>(`/locations/${id}/disable`)
  },
}

export const stockInApi = {
  list(query: StockInQuery = {}): Promise<PageResult<StockIn>> {
    return api.get<PageResult<StockIn>>('/stock-ins', { params: query })
  },
  get(id: number): Promise<StockIn> {
    return api.get<StockIn>(`/stock-ins/${id}`)
  },
  create(payload: StockInPayload): Promise<StockIn> {
    return api.post<StockIn>('/stock-ins', payload)
  },
  update(id: number, payload: Partial<StockInPayload>): Promise<StockIn> {
    return api.put<StockIn>(`/stock-ins/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/stock-ins/${id}`)
  },
  submit(id: number): Promise<StockIn> {
    return action<StockIn>(`/stock-ins/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<StockIn> {
    return action<StockIn>(`/stock-ins/${id}/approve`, { comment })
  },
  reject(id: number, comment: string): Promise<StockIn> {
    return action<StockIn>(`/stock-ins/${id}/reject`, { comment })
  },
  pendingPutaways(
    warehouseId?: number,
    keyword?: string,
  ): Promise<PendingPutaway[]> {
    return api.get<PendingPutaway[]>('/stock-ins/putaway/pending', {
      params: { warehouse_id: warehouseId, keyword },
    })
  },
  listPutaways(query: {
    warehouse_id?: number
    location_id?: number
    product_id?: number
    sku_id?: number
    stock_in_id?: number
  } = {}): Promise<Putaway[]> {
    return api.get<Putaway[]>('/putaways', { params: query })
  },
  putaway(
    stockInId: number,
    items: PutawayPayloadItem[],
  ): Promise<Putaway[]> {
    return api.post<Putaway[]>(`/stock-ins/${stockInId}/putaway`, { items })
  },
}

export const stockOutApi = {
  list(query: StockOutQuery = {}): Promise<PageResult<StockOut>> {
    return api.get<PageResult<StockOut>>('/stock-outs', { params: query })
  },
  get(id: number): Promise<StockOut> {
    return api.get<StockOut>(`/stock-outs/${id}`)
  },
  create(payload: StockOutPayload): Promise<StockOut> {
    return api.post<StockOut>('/stock-outs', payload)
  },
  update(
    id: number,
    payload: { remark?: string | null },
  ): Promise<StockOut> {
    return api.put<StockOut>(`/stock-outs/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/stock-outs/${id}`)
  },
  submit(id: number): Promise<StockOut> {
    return action<StockOut>(`/stock-outs/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<StockOut> {
    return action<StockOut>(`/stock-outs/${id}/approve`, { comment })
  },
  reject(id: number, comment: string): Promise<StockOut> {
    return action<StockOut>(`/stock-outs/${id}/reject`, { comment })
  },
  offShelf(
    id: number,
    items: Array<{ stock_out_item_id: number; location_id: number; qty: string }>,
  ): Promise<StockOut> {
    return api.post<StockOut>(`/stock-outs/${id}/off-shelf`, { items })
  },
}

export const transferApi = {
  list(query: TransferQuery = {}): Promise<PageResult<Transfer>> {
    return api.get<PageResult<Transfer>>('/transfers', { params: query })
  },
  get(id: number): Promise<Transfer> {
    return api.get<Transfer>(`/transfers/${id}`)
  },
  create(payload: TransferPayload): Promise<Transfer> {
    return api.post<Transfer>('/transfers', payload)
  },
  update(id: number, payload: Partial<TransferPayload>): Promise<Transfer> {
    return api.put<Transfer>(`/transfers/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/transfers/${id}`)
  },
  submit(id: number): Promise<Transfer> {
    return action<Transfer>(`/transfers/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<Transfer> {
    return action<Transfer>(`/transfers/${id}/approve`, { comment })
  },
  reject(id: number, comment: string): Promise<Transfer> {
    return action<Transfer>(`/transfers/${id}/reject`, { comment })
  },
  listOuts(query: TransferDocQuery = {}): Promise<PageResult<TransferOut>> {
    return api.get<PageResult<TransferOut>>('/transfer-outs', {
      params: query,
    })
  },
  getOut(id: number): Promise<TransferOut> {
    return api.get<TransferOut>(`/transfer-outs/${id}`)
  },
  submitOut(id: number): Promise<TransferOut> {
    return action<TransferOut>(`/transfer-outs/${id}/submit`)
  },
  approveOut(id: number, comment?: string): Promise<TransferOut> {
    return action<TransferOut>(`/transfer-outs/${id}/approve`, { comment })
  },
  rejectOut(id: number, comment: string): Promise<TransferOut> {
    return action<TransferOut>(`/transfer-outs/${id}/reject`, { comment })
  },
  offShelfOut(
    id: number,
    items: Array<{
      transfer_out_item_id: number
      location_id: number
      qty: string
    }>,
  ): Promise<TransferOut> {
    return api.post<TransferOut>(`/transfer-outs/${id}/off-shelf`, { items })
  },
  listIns(query: TransferDocQuery = {}): Promise<PageResult<TransferIn>> {
    return api.get<PageResult<TransferIn>>('/transfer-ins', {
      params: query,
    })
  },
  getIn(id: number): Promise<TransferIn> {
    return api.get<TransferIn>(`/transfer-ins/${id}`)
  },
  submitIn(id: number): Promise<TransferIn> {
    return action<TransferIn>(`/transfer-ins/${id}/submit`)
  },
  approveIn(id: number, comment?: string): Promise<TransferIn> {
    return action<TransferIn>(`/transfer-ins/${id}/approve`, { comment })
  },
  rejectIn(id: number, comment: string): Promise<TransferIn> {
    return action<TransferIn>(`/transfer-ins/${id}/reject`, { comment })
  },
  receiveIn(
    id: number,
    items: Array<{ transfer_in_item_id: number; location_id: number; qty: string }>,
  ): Promise<TransferIn> {
    return api.post<TransferIn>(`/transfer-ins/${id}/receive`, { items })
  },
}

export const stockCheckApi = {
  list(query: StockCheckQuery = {}): Promise<PageResult<StockCheck>> {
    return api.get<PageResult<StockCheck>>('/stock-checks', { params: query })
  },
  get(id: number): Promise<StockCheck> {
    return api.get<StockCheck>(`/stock-checks/${id}`)
  },
  create(payload: StockCheckPayload): Promise<StockCheck> {
    return api.post<StockCheck>('/stock-checks', payload)
  },
  update(id: number, payload: Partial<StockCheckPayload>): Promise<StockCheck> {
    return api.put<StockCheck>(`/stock-checks/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/stock-checks/${id}`)
  },
  submit(id: number): Promise<StockCheck> {
    return action<StockCheck>(`/stock-checks/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<StockCheck> {
    return action<StockCheck>(`/stock-checks/${id}/approve`, { comment })
  },
  reject(id: number, comment: string): Promise<StockCheck> {
    return action<StockCheck>(`/stock-checks/${id}/reject`, { comment })
  },
  terminate(id: number, reason: string): Promise<StockCheck> {
    return action<StockCheck>(`/stock-checks/${id}/terminate`, { reason })
  },
}

export const stockQueryApi = {
  flows(query: StockFlowQuery = {}): Promise<PageResult<StockFlow>> {
    return api.get<PageResult<StockFlow>>('/stock-flows', { params: query })
  },
  summary(query: StockSummaryQuery = {}): Promise<PageResult<StockSummary>> {
    return api.get<PageResult<StockSummary>>('/stock-summary', {
      params: query,
    })
  },
  locations(query: {
    warehouse_id?: number
    location_id?: number
    product_id?: number
    sku_id?: number
  } = {}): Promise<StockLocationRow[]> {
    return api.get<StockLocationRow[]>('/stock-locations', { params: query })
  },
  manualIn(payload: StockManualPayload): Promise<StockFlow> {
    return api.post<StockFlow>('/stock/manual-in', payload)
  },
  manualOut(
    payload: Omit<StockManualPayload, 'cost_price'>,
  ): Promise<StockFlow> {
    return api.post<StockFlow>('/stock/manual-out', payload)
  },
  setLocationStock(
    payload: Omit<StockManualPayload, 'qty'> & { target_qty: string },
  ): Promise<StockFlow | null> {
    return api.put<StockFlow | null>('/stock/location-stock', payload)
  },
  warnings(onlyBelow = false): Promise<StockWarning[]> {
    return api.get<StockWarning[]>('/stock-warnings', {
      params: { only_below: onlyBelow },
    })
  },
  refreshWarnings(): Promise<StockWarning[]> {
    return action<StockWarning[]>('/stock-warnings/refresh')
  },
  costs(): Promise<StockCost[]> {
    return api.get<StockCost[]>('/stock-costs')
  },
  refreshCosts(): Promise<StockCost[]> {
    return action<StockCost[]>('/stock-costs/refresh')
  },
}

export const locationAdjustApi = {
  list(query: LocationAdjustQuery = {}): Promise<PageResult<LocationAdjust>> {
    return api.get<PageResult<LocationAdjust>>('/location-adjusts', {
      params: query,
    })
  },
  get(id: number): Promise<LocationAdjust> {
    return api.get<LocationAdjust>(`/location-adjusts/${id}`)
  },
  create(payload: LocationAdjustPayload): Promise<LocationAdjust> {
    return api.post<LocationAdjust>('/location-adjusts', payload)
  },
  update(
    id: number,
    payload: Partial<LocationAdjustPayload>,
  ): Promise<LocationAdjust> {
    return api.put<LocationAdjust>(`/location-adjusts/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/location-adjusts/${id}`)
  },
  submit(id: number): Promise<LocationAdjust> {
    return action<LocationAdjust>(`/location-adjusts/${id}/submit`)
  },
  approve(id: number, comment?: string): Promise<LocationAdjust> {
    return action<LocationAdjust>(`/location-adjusts/${id}/approve`, {
      comment,
    })
  },
  reject(id: number, comment: string): Promise<LocationAdjust> {
    return action<LocationAdjust>(`/location-adjusts/${id}/reject`, {
      comment,
    })
  },
}

export const shopStockApi = {
  list(query: ShopStockQuery = {}): Promise<PageResult<ShopStock>> {
    return api.get<PageResult<ShopStock>>('/shop-stocks', { params: query })
  },
  get(id: number): Promise<ShopStock> {
    return api.get<ShopStock>(`/shop-stocks/${id}`)
  },
  create(payload: ShopStockPayload): Promise<ShopStock> {
    return api.post<ShopStock>('/shop-stocks', payload)
  },
  update(id: number, payload: ShopStockPayload): Promise<ShopStock> {
    return api.put<ShopStock>(`/shop-stocks/${id}`, payload)
  },
  remove(id: number): Promise<void> {
    return api.delete<void>(`/shop-stocks/${id}`)
  },
  sync(payload: { shop_id?: number; ids?: number[] } = {}): Promise<{
    synced: number
    skipped: number
    items: ShopStock[]
  }> {
    return api.post<{
      synced: number
      skipped: number
      items: ShopStock[]
    }>('/shop-stocks/sync', payload)
  },
}

export const stockSettingApi = {
  get(): Promise<StockSetting> {
    return api.get<StockSetting>('/stock-settings')
  },
  update(payload: StockSettingPayload): Promise<StockSetting> {
    return api.put<StockSetting>('/stock-settings', payload)
  },
}

export const stockSettlementApi = {
  list(query: SettlementQuery = {}): Promise<PageResult<StockSettlement>> {
    return api.get<PageResult<StockSettlement>>('/stock-settlements', {
      params: query,
    })
  },
  periods(): Promise<SettlementPeriod[]> {
    return api.get<SettlementPeriod[]>('/stock-settlements/periods')
  },
  create(payload: {
    settlement_date?: string | null
    month?: string | null
  }): Promise<Record<string, unknown>> {
    return api.post<Record<string, unknown>>('/stock-settlements', payload)
  },
  rewriteCost(payload: {
    settlement_date?: string | null
  }): Promise<Record<string, unknown>> {
    return api.post<Record<string, unknown>>(
      '/stock-settlements/rewrite-outbound-cost',
      payload,
    )
  },
  rewriteLogs(
    query: CostRewriteLogQuery = {},
  ): Promise<PageResult<CostRewriteLog>> {
    return api.get<PageResult<CostRewriteLog>>(
      '/stock-settlements/cost-rewrite-logs',
      { params: query },
    )
  },
}

export const STOCK_CHECK_TEMPLATE_URL = '/stock-checks/template'
export const STOCK_SETTLEMENT_TEMPLATE_URL = '/stock-settlements/template'
export const STOCK_SETTLEMENT_EXPORT_URL = '/stock-settlements/export'
export const STOCK_SETTLEMENT_IMPORT_URL = '/stock-settlements/import'

export function stockCheckExportUrl(id: number): string {
  return `/stock-checks/${id}/export`
}

export function stockCheckImportUrl(id: number): string {
  return `/stock-checks/${id}/import`
}
