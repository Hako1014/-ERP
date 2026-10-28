import { api } from '@/api/client'
import type { PageResult } from '@/api/party'

export interface ProductOption {
  id: number
  code: string
  name: string
  is_disabled?: boolean
}

export interface ProductSkuOption {
  id: number
  product_id: number
  spec_code?: string | null
  spec_name?: string | null
}

export const productOptionsApi = {
  listProducts(
    keyword?: string,
    page = 1,
    pageSize = 50,
  ): Promise<PageResult<ProductOption>> {
    return api.get<PageResult<ProductOption>>('/products', {
      params: { keyword, page, page_size: pageSize },
    })
  },
  listSkus(productId: number): Promise<ProductSkuOption[]> {
    return api.get<ProductSkuOption[]>(`/products/${productId}/skus`)
  },
}
