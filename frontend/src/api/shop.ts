import { api } from '@/api/client'
import type { PageResult } from '@/api/party'

export interface Platform {
  id: number
  name: string
  code?: string | null
}

export interface ShopApiConfig {
  id: number
  shop_id: number
  api_code?: string | null
  verify_code?: string | null
  app_key?: string | null
  has_app_secret: boolean
}

export interface Shop {
  id: number
  platform_id: number
  platform_name?: string | null
  name: string
  login_account?: string | null
  url?: string | null
  tag?: string | null
  supplier_id?: number | null
  supplier_name?: string | null
  default_address?: string | null
  is_locked: boolean
  auto_sync_order: boolean
  auto_sync_stock: boolean
  download_mode?: 'ALL' | 'MATCH_PRODUCT' | string | null
  sms_ship_template?: string | null
  sms_deliver_template?: string | null
  enable_logistics_params: boolean
  message_match?: string | null
  api_configured: boolean
  api_config?: ShopApiConfig | null
}

export interface ShopListQuery {
  platform_id?: number
  keyword?: string
  is_locked?: boolean
  auto_sync_order?: boolean
  auto_sync_stock?: boolean
  page?: number
  page_size?: number
}

export type PlatformPayload = Partial<Pick<Platform, 'name' | 'code'>>

export type ShopPayload = Partial<
  Pick<
    Shop,
    | 'platform_id'
    | 'name'
    | 'login_account'
    | 'url'
    | 'tag'
    | 'supplier_id'
    | 'default_address'
    | 'is_locked'
    | 'auto_sync_order'
    | 'auto_sync_stock'
    | 'download_mode'
    | 'sms_ship_template'
    | 'sms_deliver_template'
    | 'enable_logistics_params'
    | 'message_match'
  >
>

export interface ShopApiPayload {
  api_code?: string | null
  verify_code?: string | null
  app_key?: string | null
  app_secret?: string | null
}

export const shopApi = {
  listPlatforms(keyword?: string): Promise<Platform[]> {
    return api.get<Platform[]>('/platforms', { params: { keyword } })
  },
  createPlatform(payload: PlatformPayload): Promise<Platform> {
    return api.post<Platform>('/platforms', payload)
  },
  updatePlatform(id: number, payload: PlatformPayload): Promise<Platform> {
    return api.put<Platform>(`/platforms/${id}`, payload)
  },
  deletePlatform(id: number): Promise<void> {
    return api.delete<void>(`/platforms/${id}`)
  },
  listShops(query: ShopListQuery = {}): Promise<PageResult<Shop>> {
    return api.get<PageResult<Shop>>('/shops', { params: query })
  },
  getShop(id: number): Promise<Shop> {
    return api.get<Shop>(`/shops/${id}`)
  },
  createShop(payload: ShopPayload): Promise<Shop> {
    return api.post<Shop>('/shops', payload)
  },
  updateShop(id: number, payload: ShopPayload): Promise<Shop> {
    return api.put<Shop>(`/shops/${id}`, payload)
  },
  deleteShop(id: number): Promise<void> {
    return api.delete<void>(`/shops/${id}`)
  },
  setSyncOrder(id: number, enabled: boolean): Promise<Shop> {
    return api.post<Shop>(`/shops/${id}/sync-order`, { enabled })
  },
  setSyncStock(id: number, enabled: boolean): Promise<Shop> {
    return api.post<Shop>(`/shops/${id}/sync-stock`, { enabled })
  },
  lockShop(id: number): Promise<Shop> {
    return api.post<Shop>(`/shops/${id}/lock`)
  },
  unlockShop(id: number): Promise<Shop> {
    return api.post<Shop>(`/shops/${id}/unlock`)
  },
  getShopApi(id: number): Promise<ShopApiConfig | null> {
    return api.get<ShopApiConfig | null>(`/shops/${id}/api`)
  },
  saveShopApi(id: number, payload: ShopApiPayload): Promise<ShopApiConfig> {
    return api.put<ShopApiConfig>(`/shops/${id}/api`, payload)
  },
  deleteShopApi(id: number): Promise<void> {
    return api.delete<void>(`/shops/${id}/api`)
  },
}
