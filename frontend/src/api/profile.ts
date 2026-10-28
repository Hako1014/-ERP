import { api } from '@/api/client'

export interface MyProfile {
  staff_id: number
  name: string
  display_name: string
  staff_no: string | null
  login_account: string | null
  phone_masked: string | null
  company_id: number
  company_name: string | null
  department_id: number | null
  department_name: string | null
  position_id: number | null
  position_name: string | null
  roles: string[]
  data_scope: string
  gender: string | null
  region: string | null
  is_active: boolean
  avatar_version: string | null
}

export interface MyProfileUpdate {
  display_name?: string | null
  gender?: string | null
  region?: string | null
}

export const profileApi = {
  getMine(): Promise<MyProfile> {
    return api.get<MyProfile>('/auth/me/profile')
  },
  updateMine(payload: MyProfileUpdate): Promise<MyProfile> {
    return api.put<MyProfile>('/auth/me/profile', payload)
  },
  uploadAvatar(file: File): Promise<MyProfile> {
    const form = new FormData()
    form.append('file', file)
    return api.post<MyProfile>('/auth/me/avatar', form)
  },
  deleteAvatar(): Promise<MyProfile> {
    return api.delete<MyProfile>('/auth/me/avatar')
  },
  getAvatar(): Promise<Blob> {
    return api.get<Blob>('/auth/me/avatar', { responseType: 'blob' })
  },
}
