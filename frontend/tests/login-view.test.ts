import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import { installRouterGuards } from '@/router/guards'
import { useAuthStore } from '@/stores/auth'
import LoginView from '@/views/login/LoginView.vue'

async function mountLoginView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
      { path: '/forgot-password', name: 'forgot-password', component: { template: '<div>forgot</div>' }, meta: { public: true } },
      { path: '/dashboard', name: 'dashboard', component: { template: '<div>dashboard</div>' }, meta: { permission: 'module:dashboard' } },
    ] as RouteRecordRaw[],
  })
  installRouterGuards(router, pinia)
  await router.push('/login')
  await router.isReady()

  useAuthStore(pinia)
  vi.spyOn(api, 'get').mockImplementation(async () => {
    return {
      staff_id: 1,
      roles: ['ADMIN'],
      permissions: ['*'],
      modules: [
        'dashboard',
        'purchase',
        'product',
        'stock',
        'order',
        'approval',
        'finance',
        'settings',
      ],
    } as never
  })

  const wrapper = mount(LoginView, {
    global: {
      plugins: [pinia, router, ElementPlus],
    },
  })
  return { wrapper, router }
}

describe('login view integration', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('stores the session and enters the role landing page after login', async () => {
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue({
      token: 'login-token',
      staff_id: 1,
      name: '管理员',
      roles: ['ADMIN'],
    })
    const { wrapper, router } = await mountLoginView()
    const inputs = wrapper.findAll('input')

    await inputs[0].setValue('guanliyuan')
    await inputs[1].setValue('123456')
    await wrapper.find('.auth-form__submit').trigger('click')
    await flushPromises()

    expect(postSpy).toHaveBeenCalledWith('/auth/login', {
      account: 'guanliyuan',
      password: '123456',
    })
    expect(window.localStorage.getItem('erp_access_token')).toBe('login-token')
    expect(api.get).toHaveBeenCalledWith('/auth/me/permissions')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.fullPath).toBe('/dashboard')
    })
  })
})
