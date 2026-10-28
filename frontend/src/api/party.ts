import { api } from '@/api/client'

export interface PartyRecord {
  id: number
  code: string
  name: string
  contact?: string | null
  phone?: string | null
  fax?: string | null
  address?: string | null
  settlement_type?: string | null
  email?: string | null
  remark?: string | null
  is_active: boolean
  accounts?: PartyAccount[]
}

export interface PartyAccount {
  id: number
  supplier_id?: number | null
  customer_id?: number | null
  bank?: string | null
  branch?: string | null
  bank_no?: string | null
  account_no?: string | null
  address?: string | null
  is_default: boolean
  balance: string | number
  kingdee_account_code?: string | null
  kingdee_sync_status?: string | null
  kingdee_sync_message?: string | null
  balance_synced_at?: string | null
}

export interface SupplierPrice {
  id: number
  supplier_id: number
  product_id: number
  product_name?: string | null
  product_code?: string | null
  product_status?: string | null
  sku_id?: number | null
  sku_spec_code?: string | null
  price: string | number
  status: string
}

export interface PartyListQuery {
  keyword?: string
  is_active?: boolean
  page?: number
  page_size?: number
}

export interface PageResult<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export type PartyPayload = Partial<
  Pick<
    PartyRecord,
    | 'code'
    | 'name'
    | 'contact'
    | 'phone'
    | 'fax'
    | 'address'
    | 'settlement_type'
    | 'email'
    | 'remark'
    | 'is_active'
  >
>

export type PartyAccountPayload = Partial<
  Pick<
    PartyAccount,
    'bank' | 'branch' | 'bank_no' | 'account_no' | 'address' | 'is_default'
  >
>

export interface SupplierPricePayload {
  product_id: number
  sku_id?: number | null
  price: string | number
  status?: string
}

export const partyApi = {
  listSuppliers(query: PartyListQuery = {}): Promise<PageResult<PartyRecord>> {
    return api.get<PageResult<PartyRecord>>('/suppliers', { params: query })
  },
  getSupplier(id: number): Promise<PartyRecord> {
    return api.get<PartyRecord>(`/suppliers/${id}`)
  },
  createSupplier(payload: PartyPayload): Promise<PartyRecord> {
    return api.post<PartyRecord>('/suppliers', payload)
  },
  updateSupplier(id: number, payload: PartyPayload): Promise<PartyRecord> {
    return api.put<PartyRecord>(`/suppliers/${id}`, payload)
  },
  deleteSupplier(id: number): Promise<void> {
    return api.delete<void>(`/suppliers/${id}`)
  },
  enableSupplier(id: number): Promise<PartyRecord> {
    return api.post<PartyRecord>(`/suppliers/${id}/enable`)
  },
  disableSupplier(id: number): Promise<PartyRecord> {
    return api.post<PartyRecord>(`/suppliers/${id}/disable`)
  },
  listSupplierAccounts(id: number): Promise<PartyAccount[]> {
    return api.get<PartyAccount[]>(`/suppliers/${id}/accounts`)
  },
  createSupplierAccount(
    id: number,
    payload: PartyAccountPayload,
  ): Promise<PartyAccount> {
    return api.post<PartyAccount>(`/suppliers/${id}/accounts`, payload)
  },
  listCustomers(query: PartyListQuery = {}): Promise<PageResult<PartyRecord>> {
    return api.get<PageResult<PartyRecord>>('/customers', { params: query })
  },
  getCustomer(id: number): Promise<PartyRecord> {
    return api.get<PartyRecord>(`/customers/${id}`)
  },
  createCustomer(payload: PartyPayload): Promise<PartyRecord> {
    return api.post<PartyRecord>('/customers', payload)
  },
  updateCustomer(id: number, payload: PartyPayload): Promise<PartyRecord> {
    return api.put<PartyRecord>(`/customers/${id}`, payload)
  },
  deleteCustomer(id: number): Promise<void> {
    return api.delete<void>(`/customers/${id}`)
  },
  enableCustomer(id: number): Promise<PartyRecord> {
    return api.post<PartyRecord>(`/customers/${id}/enable`)
  },
  disableCustomer(id: number): Promise<PartyRecord> {
    return api.post<PartyRecord>(`/customers/${id}/disable`)
  },
  listCustomerAccounts(id: number): Promise<PartyAccount[]> {
    return api.get<PartyAccount[]>(`/customers/${id}/accounts`)
  },
  createCustomerAccount(
    id: number,
    payload: PartyAccountPayload,
  ): Promise<PartyAccount> {
    return api.post<PartyAccount>(`/customers/${id}/accounts`, payload)
  },
  updateAccount(
    id: number,
    payload: PartyAccountPayload,
  ): Promise<PartyAccount> {
    return api.put<PartyAccount>(`/accounts/${id}`, payload)
  },
  setDefaultAccount(id: number): Promise<PartyAccount> {
    return api.post<PartyAccount>(`/accounts/${id}/default`)
  },
  deleteAccount(id: number): Promise<void> {
    return api.delete<void>(`/accounts/${id}`)
  },
  listSupplierPrices(
    supplierId: number,
    query: { product_id?: number; page?: number; page_size?: number } = {},
  ): Promise<PageResult<SupplierPrice>> {
    return api.get<PageResult<SupplierPrice>>(
      `/suppliers/${supplierId}/prices`,
      { params: query },
    )
  },
  createSupplierPrice(
    supplierId: number,
    payload: SupplierPricePayload,
  ): Promise<SupplierPrice> {
    return api.post<SupplierPrice>(
      `/suppliers/${supplierId}/prices`,
      payload,
    )
  },
  updateSupplierPrice(
    id: number,
    payload: Partial<SupplierPricePayload>,
  ): Promise<SupplierPrice> {
    return api.put<SupplierPrice>(`/supplier-prices/${id}`, payload)
  },
  setSupplierPriceStatus(
    id: number,
    status: string,
  ): Promise<SupplierPrice> {
    return api.post<SupplierPrice>(`/supplier-prices/${id}/status`, { status })
  },
  deleteSupplierPrice(id: number): Promise<void> {
    return api.delete<void>(`/supplier-prices/${id}`)
  },
}
