import { api } from '@/api/client'
import type { PageResult } from '@/api/party'

export type DecimalValue = string | number | null

export interface ProductSku {
  id: number
  product_id: number
  spec_name?: string | null
  spec_code: string
  barcode?: string | null
  tag_price?: DecimalValue
  sale_price?: DecimalValue
  cost_price?: DecimalValue
  min_price?: DecimalValue
  image?: string | null
  is_active: boolean
}

export interface ProductRecord {
  id: number
  name: string
  code: string
  art_no?: string | null
  category_id?: number | null
  category_name?: string | null
  brand_id?: number | null
  brand_name?: string | null
  default_supplier_id?: number | null
  measure_type: 'SINGLE' | 'MULTI' | string
  unit_group_id?: number | null
  main_unit_id?: number | null
  unit_name?: string | null
  online_price?: DecimalValue
  tag_price?: DecimalValue
  min_price?: DecimalValue
  cost_price?: DecimalValue
  weight?: DecimalValue
  volume?: DecimalValue
  min_stock: number
  max_stock: number
  warn_stock: number
  tax_rate?: DecimalValue
  tax_code?: string | null
  barcode?: string | null
  is_gift: boolean
  use_float_cost: boolean
  is_disabled: boolean
  listed: boolean
  listed_at?: string | null
  description?: string | null
  image?: string | null
  skus?: ProductSku[]
}

export type ProductPayload = Omit<
  ProductRecord,
  'id' | 'category_name' | 'brand_name' | 'unit_name' | 'listed' | 'listed_at' | 'skus'
> & {
  skus?: SkuPayload[]
}

export interface ProductListQuery {
  keyword?: string
  category_id?: number | null
  brand_id?: number | null
  listed?: boolean
  is_disabled?: boolean
  page?: number
  page_size?: number
}

export interface SkuPayload {
  spec_name?: string | null
  spec_code: string
  barcode?: string | null
  tag_price?: DecimalValue
  sale_price?: DecimalValue
  cost_price?: DecimalValue
  min_price?: DecimalValue
  image?: string | null
  is_active?: boolean
}

export interface ComboItem {
  id?: number
  product_id: number
  product_name?: string | null
  sku_id?: number | null
  sku_spec_code?: string | null
  qty: DecimalValue
  sort_order: number
}

export interface ComboRecord {
  id: number
  combo_no: string
  name: string
  main_product_id: number
  main_product_name?: string | null
  main_sku_id?: number | null
  main_sku_spec_code?: string | null
  is_enabled: boolean
  remark?: string | null
  items?: ComboItem[]
}

export interface ComboPayload {
  combo_no: string
  name: string
  main_product_id: number
  main_sku_id?: number | null
  is_enabled?: boolean
  remark?: string | null
  items: Array<{
    product_id: number
    sku_id?: number | null
    qty: DecimalValue
    sort_order: number
  }>
}

export interface ComboListQuery {
  keyword?: string
  is_enabled?: boolean
  page?: number
  page_size?: number
}

export const productApi = {
  listProducts(query: ProductListQuery = {}): Promise<PageResult<ProductRecord>> {
    return api.get<PageResult<ProductRecord>>('/products', { params: query })
  },
  getProduct(id: number): Promise<ProductRecord> {
    return api.get<ProductRecord>(`/products/${id}`)
  },
  createProduct(payload: ProductPayload): Promise<ProductRecord> {
    return api.post<ProductRecord>('/products', payload)
  },
  updateProduct(id: number, payload: Partial<ProductPayload>): Promise<ProductRecord> {
    return api.put<ProductRecord>(`/products/${id}`, payload)
  },
  deleteProduct(id: number): Promise<void> {
    return api.delete<void>(`/products/${id}`)
  },
  batchDeleteProducts(ids: number[]): Promise<{ deleted: number }> {
    return api.post<{ deleted: number }>('/products/batch-delete', { ids })
  },
  setProductListing(ids: number[], listed: boolean): Promise<{ count: number }> {
    return api.post<{ count: number }>('/products/listing', { ids, listed })
  },
  setProductDisabled(id: number, disabled: boolean): Promise<ProductRecord> {
    const action = disabled ? 'disable' : 'enable'
    return api.post<ProductRecord>(`/products/${id}/${action}`)
  },
  listSkus(productId: number): Promise<ProductSku[]> {
    return api.get<ProductSku[]>(`/products/${productId}/skus`)
  },
  createSku(productId: number, payload: SkuPayload): Promise<ProductSku> {
    return api.post<ProductSku>(`/products/${productId}/skus`, payload)
  },
  updateSku(skuId: number, payload: Partial<SkuPayload>): Promise<ProductSku> {
    return api.put<ProductSku>(`/product-skus/${skuId}`, payload)
  },
  deleteSku(skuId: number): Promise<void> {
    return api.delete<void>(`/product-skus/${skuId}`)
  },
  listCombos(query: ComboListQuery = {}): Promise<PageResult<ComboRecord>> {
    return api.get<PageResult<ComboRecord>>('/combo-products', { params: query })
  },
  getCombo(id: number): Promise<ComboRecord> {
    return api.get<ComboRecord>(`/combo-products/${id}`)
  },
  createCombo(payload: ComboPayload): Promise<ComboRecord> {
    return api.post<ComboRecord>('/combo-products', payload)
  },
  updateCombo(id: number, payload: Partial<ComboPayload>): Promise<ComboRecord> {
    return api.put<ComboRecord>(`/combo-products/${id}`, payload)
  },
  deleteCombo(id: number): Promise<void> {
    return api.delete<void>(`/combo-products/${id}`)
  },
  setComboEnabled(id: number, enabled: boolean): Promise<void> {
    const action = enabled ? 'enable' : 'disable'
    return api.post<void>(`/combo-products/${id}/${action}`)
  },
}

export const PRODUCT_EXPORT_URL = '/products/export'
export const PRODUCT_IMPORT_URL = '/products/import'
export const PRODUCT_TEMPLATE_URL = '/common/excel-template/product'
