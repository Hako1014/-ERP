import { createPinia, setActivePinia } from 'pinia'
import {
  createMemoryHistory,
  createRouter,
  type RouteRecordRaw,
} from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { installRouterGuards, safeRedirectPath } from '@/router/guards'
import { resolveLandingPath } from '@/router/landing'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: { template: '<div>login</div>' },
    meta: { public: true },
  },
  {
    path: '/forgot-password',
    name: 'forgot-password',
    component: { template: '<div>forgot</div>' },
    meta: { public: true },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: { template: '<div>dashboard</div>' },
    meta: { permission: 'module:dashboard' },
  },
  {
    path: '/finance',
    name: 'finance',
    component: { template: '<div>finance</div>' },
    meta: { permission: 'module:finance' },
  },
  {
    path: '/403',
    name: 'forbidden',
    component: { template: '<div>forbidden</div>' },
  },
]

function createGuardedRouter() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes,
  })
  installRouterGuards(router, pinia)
  return { pinia, router }
}

describe('router guards', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('redirects unauthenticated users to login with the original path', async () => {
    const { router } = createGuardedRouter()

    await router.push('/dashboard')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/dashboard')
  })

  it('redirects users without the route permission to 403', async () => {
    const { pinia, router } = createGuardedRouter()
    const authStore = useAuthStore(pinia)
    authStore.setLoginSession({
      token: 'token',
      staff_id: 1,
      name: '测试用户',
      roles: ['PURCHASER'],
    })
    authStore.setPermissionSnapshot({
      staff_id: 1,
      roles: ['PURCHASER'],
      permissions: ['module:dashboard', 'module:purchase'],
      modules: ['dashboard', 'purchase'],
    })

    await router.push('/finance')

    expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('allows users with the route permission', async () => {
    const { pinia, router } = createGuardedRouter()
    const authStore = useAuthStore(pinia)
    authStore.setLoginSession({
      token: 'token',
      staff_id: 1,
      name: '测试用户',
      roles: ['ADMIN'],
    })
    authStore.setPermissionSnapshot({
      staff_id: 1,
      roles: ['ADMIN'],
      permissions: ['*'],
      modules: [],
    })

    await router.push('/dashboard')

    expect(router.currentRoute.value.name).toBe('dashboard')
  })
})

describe('landing and redirect helpers', () => {
  it('uses the role responsibility landing page', () => {
    const landing = resolveLandingPath(
      ['FINANCE'],
      (permission) => permission === 'module:finance',
    )

    expect(landing).toBe('/finance/vouchers')
  })

  it('rejects external and authentication redirect targets', () => {
    expect(safeRedirectPath('/purchase/orders')).toBe('/purchase/orders')
    expect(safeRedirectPath('//example.com')).toBeNull()
    expect(safeRedirectPath('/login')).toBeNull()
    expect(safeRedirectPath('/forgot-password')).toBeNull()
  })
})
