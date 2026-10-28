import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  ACCESS_TOKEN_KEY,
  api,
  setAccessToken,
} from '@/api/client'

const PROFILE_KEY = 'erp_auth_profile'

export interface AuthProfile {
  staff_id: number
  name: string
  formal_name?: string
  display_name?: string
  avatar_version?: string | null
  company_id?: number
  company_name?: string | null
  roles: string[]
  permissions: string[]
  modules: string[]
}

export interface LoginSession {
  token: string
  staff_id: number
  name: string
  formal_name?: string
  display_name?: string
  avatar_version?: string | null
  roles: string[]
}

interface PermissionPayload {
  staff_id: number
  name?: string
  formal_name?: string
  display_name?: string
  avatar_version?: string | null
  company_id?: number
  company_name?: string | null
  roles: string[]
  permissions: string[]
  modules: string[]
}

function readProfile(): AuthProfile | null {
  const raw = window.localStorage.getItem(PROFILE_KEY)
  if (!raw) {
    return null
  }
  try {
    return JSON.parse(raw) as AuthProfile
  } catch {
    window.localStorage.removeItem(PROFILE_KEY)
    return null
  }
}

function saveProfile(profile: AuthProfile | null): void {
  if (profile) {
    window.localStorage.setItem(PROFILE_KEY, JSON.stringify(profile))
  } else {
    window.localStorage.removeItem(PROFILE_KEY)
  }
}

export const useAuthStore = defineStore('auth', () => {
  const profile = ref<AuthProfile | null>(readProfile())
  // 旧版本地快照没有公司展示字段；需刷新一次才能安全显示所属公司。
  const profileLoaded = ref(Boolean(profile.value && 'company_name' in profile.value))
  const token = ref<string | null>(
    window.localStorage.getItem(ACCESS_TOKEN_KEY),
  )

  const permissions = computed(() => profile.value?.permissions ?? [])
  const modules = computed(() => profile.value?.modules ?? [])
  const roles = computed(() => profile.value?.roles ?? [])
  const isAuthenticated = computed(() => Boolean(token.value))
  const displayName = computed(() => profile.value?.display_name || profile.value?.name || '未登录')

  function setPermissionSnapshot(payload: PermissionPayload): void {
    if (token.value && profile.value && profile.value.staff_id !== payload.staff_id) return
    const displayName = payload.display_name || payload.name || profile.value?.display_name || profile.value?.name || '当前用户'
    const next: AuthProfile = {
      staff_id: payload.staff_id,
      name: displayName,
      formal_name: payload.formal_name || profile.value?.formal_name,
      display_name: displayName,
      avatar_version: payload.avatar_version !== undefined
        ? payload.avatar_version
        : profile.value?.avatar_version ?? null,
      company_id: payload.company_id,
      company_name: payload.company_name ?? null,
      roles: payload.roles,
      permissions: payload.permissions,
      modules: payload.modules,
    }
    profile.value = next
    profileLoaded.value = true
    saveProfile(next)
  }

  function setLoginSession(session: LoginSession): void {
    token.value = session.token
    setAccessToken(session.token)
    const next: AuthProfile = {
      staff_id: session.staff_id,
      name: session.display_name || session.name,
      formal_name: session.formal_name || session.name,
      display_name: session.display_name || session.name,
      avatar_version: session.avatar_version ?? null,
      roles: session.roles,
      permissions: [],
      modules: [],
    }
    profile.value = next
    profileLoaded.value = false
    saveProfile(next)
  }

  async function fetchPermissions(): Promise<void> {
    if (!token.value) {
      return
    }
    const requestToken = token.value
    const requestStaffId = profile.value?.staff_id
    const payload = await api.get<PermissionPayload>('/auth/me/permissions')
    if (token.value !== requestToken || (requestStaffId && profile.value?.staff_id !== requestStaffId)) return
    if (profile.value && payload.staff_id !== profile.value.staff_id) return
    setPermissionSnapshot(payload)
  }

  function applySelfProfile(payload: { staff_id: number; name: string; display_name: string; avatar_version: string | null }, expectedToken: string): boolean {
    const current = profile.value
    if (!token.value || token.value !== expectedToken || !current || current.staff_id !== payload.staff_id) return false
    const next = {
      ...current,
      name: payload.display_name || payload.name,
      formal_name: payload.name,
      display_name: payload.display_name || payload.name,
      avatar_version: payload.avatar_version,
    }
    profile.value = next
    profileLoaded.value = true
    saveProfile(next)
    return true
  }

  async function ensureProfile(force = false): Promise<void> {
    if (!token.value || (profileLoaded.value && !force)) {
      return
    }
    await fetchPermissions()
  }

  function hasPermission(code: string): boolean {
    return permissions.value.includes('*') || permissions.value.includes(code)
  }

  function hasModule(module: string): boolean {
    return hasPermission(`module:${module}`)
  }

  function clearSession(): void {
    token.value = null
    profile.value = null
    profileLoaded.value = false
    setAccessToken(null)
    saveProfile(null)
  }

  return {
    token,
    profile,
    profileLoaded,
    permissions,
    modules,
    roles,
    isAuthenticated,
    displayName,
    setPermissionSnapshot,
    setLoginSession,
    applySelfProfile,
    fetchPermissions,
    ensureProfile,
    hasPermission,
    hasModule,
    clearSession,
  }
})
