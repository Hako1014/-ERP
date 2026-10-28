import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminLayout from '@/layouts/AdminLayout.vue'
import AssistantFloat from '@/layouts/components/AssistantFloat.vue'
import { assistantApi } from '@/api/assistant'
import { notificationApi } from '@/api/notification'
import { organizationApi } from '@/api/organization'
import { profileApi, type MyProfile } from '@/api/profile'
import { workbenchApi } from '@/api/workbench'
import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  vi.restoreAllMocks()
})

const mountedWrappers: { unmount: () => void }[] = []

afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
  document.body.innerHTML = ''
})

async function buildLayout(permissions: string[], path = '/dashboard') {
  window.localStorage.clear()
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.setLoginSession({
    token: 'layout-test-token',
    staff_id: 1,
    name: '测试用户',
    roles: ['TEST'],
  })
  authStore.setPermissionSnapshot({
    staff_id: 1,
    roles: ['TEST'],
    permissions,
    modules: permissions.map((permission) =>
      permission.replace('module:', ''),
    ),
  })
  vi.spyOn(assistantApi, 'getCapabilities').mockResolvedValue({
    staff_id: 1,
    service_status: 'unconfigured',
    provider_mode: 'unavailable',
    roles: ['TEST'],
    permissions,
    modules: permissions
      .filter((permission) => permission.startsWith('module:'))
      .map((permission) => permission.slice('module:'.length)),
    features: [{ code: 'guide', enabled: true }],
  })
  vi.spyOn(notificationApi, 'list').mockResolvedValue({
    items: [],
    page_size: 6,
    next_cursor: null,
  })
  vi.spyOn(notificationApi, 'getUnreadCount').mockResolvedValue({ count: 0 })
  vi.spyOn(notificationApi, 'stream').mockResolvedValue()
  vi.spyOn(workbenchApi, 'listAnnouncements').mockResolvedValue({ total: 0, page: 1, page_size: 1, items: [] })
  vi.spyOn(workbenchApi, 'listTodos').mockResolvedValue({ available: true, total: 0, page: 1, page_size: 1, items: [] })
  vi.spyOn(workbenchApi, 'getHelp').mockResolvedValue({
    title: '首页看板', route_key: '/dashboard', guidance: '首页看板使用说明',
    faq: '常见问题说明', sources: [{ type: 'guide', title: '首页资料', href: '/dashboard', scope: '当前授权模块' }],
    assistant_available: true, assistant_path: '/assistant',
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/dashboard',
        component: { template: '<div>测试页面</div>' },
        meta: { title: '首页看板', permission: 'module:dashboard' },
      },
      {
        path: '/assistant',
        name: 'assistant',
        component: { template: '<div />' },
        meta: { title: 'AI 助手', permission: 'module:dashboard' },
      },
      { path: '/login', component: { template: '<div>登录页</div>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(AdminLayout, {
    global: {
      plugins: [pinia, router, ElementPlus],
    },
  })
  mountedWrappers.push(wrapper)
  return wrapper
}

describe('admin layout shell', () => {
  it('renders brand, breadcrumb, and permitted menu modules', async () => {
    const wrapper = await buildLayout([
      'module:dashboard',
      'module:purchase',
      'module:product',
      'module:approval',
    ])

    expect(wrapper.text()).toContain('电商ERP')
    expect(wrapper.text()).toContain('首页看板')
    expect(wrapper.text()).toContain('采购管理')
    expect(wrapper.text()).not.toContain('财务管理')
    expect(wrapper.findComponent(AssistantFloat).exists()).toBe(true)
  })

  it('renders all modules for administrator wildcard', async () => {
    const wrapper = await buildLayout(['*'])
    expect(wrapper.text()).toContain('库存管理')
    expect(wrapper.text()).toContain('财务管理')
    expect(wrapper.text()).toContain('基础设置')
  })

  it('renders the assistant as an in-content workspace and keeps one assistant instance', async () => {
    const wrapper = await buildLayout(['module:dashboard'], '/assistant')

    expect(wrapper.find('.admin-layout__content.is-assistant-workspace').exists()).toBe(true)
    expect(wrapper.find('.assistant-float.is-workspace').exists()).toBe(true)
    expect(wrapper.find('.assistant-float__panel.is-workspace').exists()).toBe(true)
    expect(wrapper.find('.assistant-float__launcher').exists()).toBe(false)
    expect(wrapper.findAllComponents(AssistantFloat)).toHaveLength(1)
  })

  it('keeps the fixed sidebar footer actions available', async () => {
    const wrapper = await buildLayout(['module:dashboard'])

    expect(wrapper.find('.app-sidebar__footer').exists()).toBe(true)
    expect(wrapper.text()).toContain('帮助')
    expect(wrapper.text()).toContain('版本')
    expect(wrapper.find('button[aria-label="收起菜单"]').exists()).toBe(true)
  })

  it('opens announcement, todo, and contextual help panels even when they have no records', async () => {
    const wrapper = await buildLayout(['module:dashboard', 'module:approval'])
    vi.mocked(workbenchApi.listAnnouncements)
      .mockRejectedValueOnce(new Error('公告接口临时不可用'))
      .mockResolvedValue({ total: 0, page: 1, page_size: 10, items: [] })
    await wrapper.get('button[aria-label="公告"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('公告接口临时不可用')
    const retryButton = [...document.body.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === '重试')
    retryButton?.click()
    await flushPromises()
    expect(document.body.textContent).toContain('暂无当前可见的有效公告')
    expect(workbenchApi.listAnnouncements).toHaveBeenCalled()

    await wrapper.get('button[aria-label="待办"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('目前没有需要你处理的审批')
    expect(workbenchApi.listTodos).toHaveBeenCalled()

    await wrapper.get('button[aria-label="打开帮助"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('首页看板使用说明')
    expect(document.body.textContent).toContain('常见问题说明')
    expect(workbenchApi.getHelp).toHaveBeenCalledWith('/dashboard', expect.any(AbortSignal))

    document.body.querySelector<HTMLButtonElement>('button.el-dialog__headerbtn')?.click()
    await flushPromises()
    await wrapper.get('button[aria-label="更多功能"]').trigger('click')
    await flushPromises()
    const mobileHelp = [...document.body.querySelectorAll('[role="menuitem"]')]
      .find((item) => item.textContent?.trim() === '帮助')
    expect(mobileHelp).toBeTruthy()
    ;(mobileHelp as HTMLElement | undefined)?.click()
    await flushPromises()
    expect(document.body.textContent).toContain('首页看板使用说明')
  })

  it('opens the self profile from the user menu and keeps the organization placeholder explicit', async () => {
    const ownProfile: MyProfile = {
      staff_id: 1,
      name: '正式姓名',
      display_name: '显示名称',
      staff_no: 'STAFF-1',
      login_account: 'staff-one',
      phone_masked: '138****1234',
      company_id: 1,
      company_name: '测试公司',
      department_id: null,
      department_name: null,
      position_id: null,
      position_name: null,
      roles: ['PURCHASER'],
      data_scope: 'SELF',
      gender: null,
      region: null,
      is_active: true,
      avatar_version: null,
    }
    vi.spyOn(profileApi, 'getMine').mockResolvedValue(ownProfile)
    vi.spyOn(organizationApi, 'listDepartments').mockResolvedValue({
      items: [{ id: 4, name: '研发部', parent_id: null, sort_order: 1 }],
    })
    vi.spyOn(organizationApi, 'listPeople').mockResolvedValue({
      total: 1,
      page: 1,
      page_size: 20,
      items: [{
        staff_id: 9,
        name: '同公司同事',
        department_id: 4,
        department_name: '研发部',
        position_id: 3,
        position_name: '工程师',
        avatar_available: false,
      }],
    })
    vi.spyOn(organizationApi, 'getPerson').mockResolvedValue({
      staff_id: 9,
      name: '同公司同事',
      department_id: 4,
      department_name: '研发部',
      position_id: 3,
      position_name: '工程师',
      avatar_available: false,
    })
    const wrapper = await buildLayout(['module:dashboard'])
    await wrapper.get('.app-header__user').trigger('click')
    await flushPromises()

    const profileItem = [...document.body.querySelectorAll('[role="menuitem"]')]
      .find((item) => item.textContent?.trim() === '个人资料')
    expect(profileItem).toBeTruthy()
    ;(profileItem as HTMLElement | undefined)?.click()
    await flushPromises()
    expect(profileApi.getMine).toHaveBeenCalledOnce()
    expect(useAuthStore().displayName).toBe('显示名称')

    const closeProfile = [...document.body.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === '关闭')
    closeProfile?.click()
    await flushPromises()
    await wrapper.get('.app-header__user').trigger('click')
    await flushPromises()
    const organizationItem = [...document.body.querySelectorAll('[role="menuitem"]')]
      .find((item) => item.textContent?.trim() === '组织人员')
    expect(organizationItem).toBeTruthy()
    ;(organizationItem as HTMLElement | undefined)?.click()
    await flushPromises()
    expect(organizationApi.listDepartments).toHaveBeenCalledOnce()
    expect(organizationApi.listPeople).toHaveBeenCalledOnce()
    expect(organizationApi.getPerson).toHaveBeenCalledWith(9)
    expect(document.body.textContent).toContain('同公司同事')
  })

  it('keeps logout available from the personal menu', async () => {
    const wrapper = await buildLayout(['module:dashboard'])
    await wrapper.get('.app-header__user').trigger('click')
    await flushPromises()
    const logoutItem = [...document.body.querySelectorAll('[role="menuitem"]')]
      .find((item) => item.textContent?.trim() === '退出登录')
    expect(logoutItem).toBeTruthy()
    ;(logoutItem as HTMLElement | undefined)?.click()
    await flushPromises()
    expect(useAuthStore().token).toBeNull()
    expect(window.localStorage.getItem('erp_access_token')).toBeNull()
  })
})
