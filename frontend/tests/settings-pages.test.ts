import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

const settingsMocks = vi.hoisted(() => ({
  companyApi: {
    createPosition: vi.fn(),
    getCompany: vi.fn(),
    listDepartments: vi.fn(),
    listIpRules: vi.fn(),
    listPositions: vi.fn(),
    listStaff: vi.fn(),
    updatePosition: vi.fn(),
    deletePosition: vi.fn(),
  },
  logisticsApi: {
    list: vi.fn(),
  },
  partyApi: {
    listCustomers: vi.fn(),
    listSuppliers: vi.fn(),
  },
  shopApi: {
    listPlatforms: vi.fn(),
  },
  assistantApi: {
    getAdminConfig: vi.fn(),
    getAdminUsage: vi.fn(),
    updateAdminConfig: vi.fn(),
  },
}))

vi.mock('@/api/company', () => ({
  companyApi: settingsMocks.companyApi,
}))
vi.mock('@/api/logistics', () => ({
  logisticsApi: settingsMocks.logisticsApi,
}))
vi.mock('@/api/party', () => ({
  partyApi: settingsMocks.partyApi,
}))
vi.mock('@/api/shop', () => ({
  shopApi: settingsMocks.shopApi,
}))
vi.mock('@/api/assistant', () => ({
  assistantApi: settingsMocks.assistantApi,
}))
vi.mock('@/api/productOptions', () => ({
  productOptionsApi: {
    listProducts: vi.fn(),
    listSkus: vi.fn(),
  },
}))

import CompanySettingsView from '@/views/settings/CompanySettingsView.vue'
import CustomerManagementView from '@/views/settings/CustomerManagementView.vue'
import LogisticsToolsView from '@/views/settings/LogisticsToolsView.vue'
import SupplierManagementView from '@/views/settings/SupplierManagementView.vue'
import ShopIntegrationView from '@/views/settings/ShopIntegrationView.vue'
import PositionPanel from '@/views/settings/company/PositionPanel.vue'
import { routes } from '@/router/routes'
import { useAuthStore } from '@/stores/auth'

const wrappers: VueWrapper[] = []

async function mountSettingsPage(component: Component, canManage = true) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.setPermissionSnapshot({
    staff_id: 1,
    roles: ['ADMIN'],
    permissions: canManage
      ? ['module:settings', 'settings:manage']
      : ['module:settings'],
    modules: ['settings'],
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes,
  })
  await router.push('/settings/company')
  await router.isReady()
  const wrapper = mount(component, {
    props: { canManage },
    attachTo: document.body,
    global: {
      plugins: [pinia, router, ElementPlus],
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('basic settings pages', () => {
  it('routes each settings resource to its real page component', () => {
    const settingsRoutes = routes
      .flatMap((route) => route.children || [])
      .filter((route) =>
        [
          'settings-company',
          'settings-customers',
          'settings-logistics',
          'settings-shops',
          'settings-suppliers',
        ].includes(String(route.name)),
      )

    expect(settingsRoutes).toHaveLength(5)
    expect(settingsRoutes.every((route) => route.component)).toBe(true)
  })

  it('renders the company settings tabs and loaded company data', async () => {
    settingsMocks.companyApi.getCompany.mockResolvedValue({
      id: 1,
      full_name: '示例电商有限公司',
      short_name: '示例电商',
      legal_person: '张三',
      phone: '13800000000',
      tax_no: '91310000TEST',
    })

    const wrapper = await mountSettingsPage(CompanySettingsView)

    expect(wrapper.text()).toContain('公司设置')
    expect(wrapper.text()).toContain('公司信息')
    expect(wrapper.text()).toContain('部门设置')
    expect(wrapper.text()).toContain('职务设置')
    expect(wrapper.text()).toContain('员工管理')
    expect(wrapper.text()).toContain('IP 权限')
    expect(wrapper.text()).toContain('AI 助手')
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe(
      '示例电商有限公司',
    )
  })

  it('shows AI governance only to settings managers and loads its admin panel', async () => {
    settingsMocks.companyApi.getCompany.mockResolvedValue({
      id: 1,
      full_name: '示例电商有限公司',
    })
    const readOnly = await mountSettingsPage(CompanySettingsView, false)
    expect(readOnly.text()).not.toContain('AI 助手')
    readOnly.unmount()

    settingsMocks.assistantApi.getAdminConfig.mockResolvedValue({
      enabled: true,
      provider: 'deepseek',
      model: 'deepseek-flash',
      api_key_configured: true,
      api_key_overrides_environment: true,
      requests_per_minute: 10,
      concurrent_requests_per_user: 1,
      daily_requests_per_user: null,
      monthly_budget: null,
      input_price_per_million: null,
      output_price_per_million: null,
      secret_store_ready: true,
      base_url: 'https://api.deepseek.com',
    })
    settingsMocks.assistantApi.getAdminUsage.mockResolvedValue({
      summary: {
        request_count: 0,
        known_prompt_tokens: 0,
        known_completion_tokens: 0,
        unknown_token_count: 0,
        known_cost: '0',
        unknown_cost_count: 0,
        monthly_budget: null,
        monthly_budget_committed: '0',
      },
      items: [],
    })
    const manager = await mountSettingsPage(CompanySettingsView, true)
    const aiTab = manager
      .findAll('.el-tabs__item')
      .find((item) => item.text().includes('AI 助手'))
    expect(aiTab).toBeTruthy()
    await aiTab?.trigger('click')
    await flushPromises()
    expect(manager.text()).toContain('服务配置与额度')
    expect(manager.text()).toContain('API Key 已设置')
    expect(settingsMocks.assistantApi.getAdminConfig).toHaveBeenCalledTimes(1)
    expect(settingsMocks.assistantApi.getAdminUsage).toHaveBeenCalledWith(30)
  })

  it('renders supplier and customer CRUD entry pages', async () => {
    settingsMocks.partyApi.listSuppliers.mockResolvedValue({
      items: [
        {
          id: 1,
          code: 'SUP-001',
          name: '上海供应商',
          is_active: true,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })
    settingsMocks.partyApi.listCustomers.mockResolvedValue({
      items: [
        {
          id: 2,
          code: 'CUS-001',
          name: '杭州客户',
          is_active: true,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })

    const supplier = await mountSettingsPage(SupplierManagementView)
    const customer = await mountSettingsPage(CustomerManagementView)

    expect(supplier.text()).toContain('供应商管理')
    expect(supplier.text()).toContain('上海供应商')
    expect(supplier.text()).toContain('价格')
    expect(customer.text()).toContain('客户管理')
    expect(customer.text()).toContain('杭州客户')
  })

  it('renders shop and logistics configuration pages', async () => {
    settingsMocks.shopApi.listPlatforms.mockResolvedValue([
      { id: 1, name: '淘宝', code: 'TAOBAO' },
    ])
    settingsMocks.logisticsApi.list.mockResolvedValue([
      {
        id: 1,
        name: '中通快递',
        company: '中通',
        is_default: true,
        is_enabled: true,
      },
    ])

    const shops = await mountSettingsPage(ShopIntegrationView)
    const logistics = await mountSettingsPage(LogisticsToolsView)

    expect(shops.text()).toContain('店铺对接')
    expect(shops.text()).toContain('淘宝')
    expect(logistics.text()).toContain('物流工具')
    expect(logistics.text()).toContain('中通快递')
  })

  it('hides write actions for read-only settings users', async () => {
    settingsMocks.partyApi.listSuppliers.mockResolvedValue({
      items: [
        {
          id: 1,
          code: 'SUP-001',
          name: '只读供应商',
          is_active: true,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })

    const wrapper = await mountSettingsPage(SupplierManagementView, false)

    expect(wrapper.text()).not.toContain('新增供应商')
    expect(wrapper.text()).toContain('只读')
    expect(wrapper.text()).not.toContain('AI 助手')
  })

  it('creates a position through the shared form dialog', async () => {
    settingsMocks.companyApi.listPositions.mockResolvedValue([])
    settingsMocks.companyApi.createPosition.mockResolvedValue({
      id: 1,
      name: '运营主管',
    })

    const wrapper = await mountSettingsPage(PositionPanel)
    const createButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '新增职务')
    expect(createButton).toBeTruthy()
    await createButton?.trigger('click')
    await flushPromises()

    await vi.waitFor(() => {
      expect(document.querySelector('.el-dialog__body input')).not.toBeNull()
    })
    const input = document.querySelector(
      '.el-dialog__body input',
    ) as HTMLInputElement
    input.value = '运营主管'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()

    const saveButton = Array.from(
      document.querySelectorAll('.el-dialog__footer button'),
    ).find((button) => button.textContent?.includes('保存'))
    expect(saveButton).toBeTruthy()
    saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await vi.waitFor(() => {
      expect(settingsMocks.companyApi.createPosition).toHaveBeenCalledWith({
        name: '运营主管',
      })
    })
  })
})
