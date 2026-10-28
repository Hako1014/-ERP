import { api } from '@/api/client'

export interface Company {
  id: number
  full_name: string
  short_name?: string | null
  legal_person?: string | null
  phone?: string | null
  tax_no?: string | null
}

export interface Department {
  id: number
  name: string
  parent_id?: number | null
  sort_order: number
}

export interface Position {
  id: number
  name: string
}

export interface Staff {
  id: number
  company_id: number
  name: string
  staff_no?: string | null
  login_account?: string | null
  phone?: string | null
  department_id?: number | null
  department_name?: string | null
  position_id?: number | null
  position_name?: string | null
  gender?: string | null
  region?: string | null
  remark?: string | null
  is_distribution: boolean
  is_active: boolean
  has_password: boolean
}

export interface IpRule {
  id: number
  rule_type: 'ALLOW' | 'DENY'
  ip_address: string
  remark?: string | null
}

export type DataScopeType = 'SELF' | 'DEPARTMENT' | 'COMPANY'

export interface StaffDataScope {
  staff_id: number
  scope_type: DataScopeType
}

export interface PagedResult<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export interface StaffQuery {
  keyword?: string
  department_id?: number
  position_id?: number
  is_active?: boolean
  page?: number
  page_size?: number
}

export type CompanyPayload = Partial<
  Pick<Company, 'full_name' | 'short_name' | 'legal_person' | 'phone' | 'tax_no'>
>

export type DepartmentPayload = Partial<
  Pick<Department, 'name' | 'parent_id' | 'sort_order'>
>

export type PositionPayload = Partial<Pick<Position, 'name'>>

export type StaffPayload = Partial<
  Pick<
    Staff,
    | 'name'
    | 'staff_no'
    | 'login_account'
    | 'phone'
    | 'department_id'
    | 'position_id'
    | 'gender'
    | 'region'
    | 'remark'
    | 'is_distribution'
    | 'is_active'
  >
> & {
  password?: string
}

export type IpRulePayload = Partial<
  Pick<IpRule, 'rule_type' | 'ip_address' | 'remark'>
>

export const companyApi = {
  getCompany(): Promise<Company> {
    return api.get<Company>('/company')
  },
  updateCompany(payload: CompanyPayload): Promise<Company> {
    return api.put<Company>('/company', payload)
  },
  listDepartments(): Promise<Department[]> {
    return api.get<Department[]>('/departments')
  },
  createDepartment(payload: DepartmentPayload): Promise<Department> {
    return api.post<Department>('/departments', payload)
  },
  updateDepartment(
    id: number,
    payload: DepartmentPayload,
  ): Promise<Department> {
    return api.put<Department>(`/departments/${id}`, payload)
  },
  deleteDepartment(id: number): Promise<void> {
    return api.delete<void>(`/departments/${id}`)
  },
  listPositions(): Promise<Position[]> {
    return api.get<Position[]>('/positions')
  },
  createPosition(payload: PositionPayload): Promise<Position> {
    return api.post<Position>('/positions', payload)
  },
  updatePosition(id: number, payload: PositionPayload): Promise<Position> {
    return api.put<Position>(`/positions/${id}`, payload)
  },
  deletePosition(id: number): Promise<void> {
    return api.delete<void>(`/positions/${id}`)
  },
  listStaff(query: StaffQuery = {}): Promise<PagedResult<Staff>> {
    return api.get<PagedResult<Staff>>('/staff', { params: query })
  },
  createStaff(payload: StaffPayload): Promise<Staff> {
    return api.post<Staff>('/staff', payload)
  },
  updateStaff(id: number, payload: StaffPayload): Promise<Staff> {
    return api.put<Staff>(`/staff/${id}`, payload)
  },
  deleteStaff(id: number): Promise<void> {
    return api.delete<void>(`/staff/${id}`)
  },
  enableStaff(id: number): Promise<Staff> {
    return api.post<Staff>(`/staff/${id}/enable`)
  },
  disableStaff(id: number): Promise<Staff> {
    return api.post<Staff>(`/staff/${id}/disable`)
  },
  resetPassword(id: number, newPassword: string): Promise<void> {
    return api.post<void>(`/staff/${id}/reset-password`, {
      new_password: newPassword,
    })
  },
  getStaffDataScope(id: number): Promise<StaffDataScope> {
    return api.get<StaffDataScope>(`/staff/${id}/data-scope`)
  },
  setStaffDataScope(id: number, scopeType: DataScopeType): Promise<StaffDataScope> {
    return api.put<StaffDataScope>(`/staff/${id}/data-scope`, { scope_type: scopeType })
  },
  listIpRules(): Promise<IpRule[]> {
    return api.get<IpRule[]>('/ip-rules')
  },
  createIpRule(payload: IpRulePayload): Promise<IpRule> {
    return api.post<IpRule>('/ip-rules', payload)
  },
  updateIpRule(id: number, payload: IpRulePayload): Promise<IpRule> {
    return api.put<IpRule>(`/ip-rules/${id}`, payload)
  },
  deleteIpRule(id: number): Promise<void> {
    return api.delete<void>(`/ip-rules/${id}`)
  },
}
