import { api } from '@/api/client'

export type FinanceRow = Record<string, any> & { id: number }
export interface FinancePage<T = FinanceRow> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export const financeApi = {
  list(path: string, params: Record<string, unknown> = {}): Promise<FinancePage | FinanceRow[]> {
    return api.get<FinancePage | FinanceRow[]>(path, { params })
  },
  detail(path: string, id: number): Promise<FinanceRow> {
    return api.get<FinanceRow>(`${path}/${id}`)
  },
  create(path: string, payload: Record<string, unknown>): Promise<FinanceRow> {
    return api.post<FinanceRow>(path, payload)
  },
  update(path: string, id: number, payload: Record<string, unknown>): Promise<FinanceRow> {
    return api.put<FinanceRow>(`${path}/${id}`, payload)
  },
  action(path: string, id: number, action: string, payload?: Record<string, unknown>): Promise<FinanceRow> {
    return api.post<FinanceRow>(`${path}/${id}/${action}`, payload || {})
  },
  remove(path: string, id: number, reason?: string): Promise<void> {
    return api.delete<void>(`${path}/${id}`, { params: reason ? { reason } : undefined })
  },
}
