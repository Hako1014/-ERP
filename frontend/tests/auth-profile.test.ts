import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  window.localStorage.clear()
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('本人资料与登录身份同步', () => {
  it('drops a late permission/profile response after switching accounts', async () => {
    const auth = useAuthStore()
    auth.setLoginSession({ token: 'token-a', staff_id: 1, name: '甲', roles: ['PURCHASER'] })
    let resolveRequest!: (value: unknown) => void
    vi.spyOn(api, 'get').mockReturnValue(new Promise((resolve) => { resolveRequest = resolve }))

    const pending = auth.fetchPermissions()
    auth.setLoginSession({ token: 'token-b', staff_id: 2, name: '乙', roles: ['FINANCE'] })
    resolveRequest({
      staff_id: 1,
      name: '甲旧名称',
      formal_name: '甲正式姓名',
      display_name: '甲旧显示名',
      avatar_version: 'old-avatar',
      roles: ['PURCHASER'],
      permissions: ['module:purchase'],
      modules: ['purchase'],
    })
    await pending

    expect(auth.profile?.staff_id).toBe(2)
    expect(auth.displayName).toBe('乙')
  })

  it('applies only the current user profile and permits clearing an avatar', () => {
    const auth = useAuthStore()
    auth.setLoginSession({ token: 'token-a', staff_id: 1, name: '正式姓名', roles: ['PURCHASER'] })
    auth.setPermissionSnapshot({
      staff_id: 1,
      roles: ['PURCHASER'],
      permissions: ['module:purchase'],
      modules: ['purchase'],
      avatar_version: 'avatar-v1',
    })
    expect(auth.applySelfProfile({ staff_id: 1, name: '正式姓名', display_name: '新显示名', avatar_version: null }, 'token-a')).toBe(true)
    expect(auth.displayName).toBe('新显示名')
    expect(auth.profile?.avatar_version).toBeNull()
    expect(auth.applySelfProfile({ staff_id: 2, name: '其他人', display_name: '其他人', avatar_version: null }, 'token-a')).toBe(false)
  })
})
