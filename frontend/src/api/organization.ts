import { api } from '@/api/client'

export interface OrganizationDepartment {
  id: number
  name: string
  parent_id: number | null
  sort_order: number
  children?: OrganizationDepartment[]
}

export interface OrganizationPerson {
  staff_id: number
  name: string
  department_id: number | null
  department_name: string | null
  position_id: number | null
  position_name: string | null
  avatar_available: boolean
}

export interface OrganizationPage {
  total: number
  page: number
  page_size: number
  items: OrganizationPerson[]
}

export const organizationApi = {
  listDepartments(): Promise<{ items: OrganizationDepartment[] }> {
    return api.get<{ items: OrganizationDepartment[] }>('/organization/departments')
  },
  listPeople(params: {
    q?: string
    department_id?: number
    page: number
    page_size: number
  }): Promise<OrganizationPage> {
    return api.get<OrganizationPage>('/organization/directory', { params })
  },
  getPerson(staffId: number): Promise<OrganizationPerson> {
    return api.get<OrganizationPerson>(`/organization/directory/${staffId}`)
  },
  getAvatar(staffId: number): Promise<Blob> {
    return api.get<Blob>(`/organization/directory/${staffId}/avatar`, { responseType: 'blob' })
  },
}
