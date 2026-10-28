import type { Pinia } from 'pinia'
import type { Router } from 'vue-router'

import { resolveLandingPath } from '@/router/landing'
import { useAuthStore } from '@/stores/auth'

const AUTHENTICATION_ROUTES = new Set(['login', 'forgot-password'])

export function safeRedirectPath(value: unknown): string | null {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw !== 'string' || !raw.startsWith('/') || raw.startsWith('//')) {
    return null
  }
  if (raw.startsWith('/login') || raw.startsWith('/forgot-password')) {
    return null
  }
  return raw
}

export function installRouterGuards(router: Router, pinia: Pinia): void {
  router.beforeEach(async (to) => {
    const authStore = useAuthStore(pinia)
    const routeName = String(to.name || '')

    if (to.meta.public) {
      if (authStore.isAuthenticated && AUTHENTICATION_ROUTES.has(routeName)) {
        return resolveLandingPath(authStore.roles, authStore.hasPermission)
      }
      return true
    }

    if (!authStore.isAuthenticated) {
      return {
        name: 'login',
        query: { redirect: to.fullPath },
      }
    }

    try {
      await authStore.ensureProfile()
    } catch {
      authStore.clearSession()
      return {
        name: 'login',
        query: { redirect: to.fullPath },
      }
    }

    if (to.meta.permission && !authStore.hasPermission(to.meta.permission)) {
      return { name: 'forbidden' }
    }

    return true
  })
}
