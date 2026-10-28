import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import DataTable from '@/components/DataTable.vue'
import type {
  DataTableColumn,
  DataTableFilterValue,
} from '@/components/types'

interface Row {
  id: number
  name: string
}

interface DataTableVm {
  page: number
  pageSize: number
  filters: Record<string, DataTableFilterValue>
  setFilters: (filters: Record<string, DataTableFilterValue>) => void
  resetFilters: () => void
}

const columns: DataTableColumn<Row>[] = [
  { prop: 'name', label: '商品名称', minWidth: 160 },
]

async function createTableRouter(path = '/products') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/products',
        name: 'products',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  return router
}

function mountTable(
  router: ReturnType<typeof createTableRouter> extends Promise<infer T>
    ? T
    : never,
) {
  return mount(DataTable<Row>, {
    props: {
      rows: [],
      columns,
      total: 0,
      filterKeys: ['keyword', 'status'],
      syncQuery: true,
    },
    global: {
      plugins: [ElementPlus, router],
    },
  })
}

describe('DataTable query synchronization', () => {
  it('restores pagination and filters from a shareable URL', async () => {
    const router = await createTableRouter(
      '/products?page=2&page_size=50&keyword=手机&status=PENDING',
    )
    const wrapper = mountTable(router)
    await flushPromises()

    const vm = wrapper.vm as unknown as DataTableVm
    expect(vm.page).toBe(2)
    expect(vm.pageSize).toBe(50)
    expect(vm.filters).toEqual({
      keyword: '手机',
      status: 'PENDING',
    })
  })

  it('writes filter changes back to the URL and restores them after remount', async () => {
    const router = await createTableRouter()
    const wrapper = mountTable(router)
    await flushPromises()

    const vm = wrapper.vm as unknown as DataTableVm
    vm.setFilters({ keyword: '电脑', status: 'PENDING' })

    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.keyword).toBe('电脑')
    })
    expect(router.currentRoute.value.query.status).toBe('PENDING')

    wrapper.unmount()
    const restored = mountTable(router)
    await flushPromises()

    expect((restored.vm as unknown as DataTableVm).filters).toEqual({
      keyword: '电脑',
      status: 'PENDING',
    })
  })

  it('removes filter query parameters and resets the page', async () => {
    const router = await createTableRouter(
      '/products?page=3&page_size=50&keyword=手机',
    )
    const wrapper = mountTable(router)
    await flushPromises()

    const vm = wrapper.vm as unknown as DataTableVm
    vm.resetFilters()

    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.keyword).toBeUndefined()
    })
    expect(router.currentRoute.value.query.page).toBe('1')
    expect(router.currentRoute.value.query.page_size).toBe('50')
  })

  it('renders an empty state with a next-step action', async () => {
    const router = await createTableRouter('/products?keyword=手机')
    const wrapper = mountTable(router)
    await flushPromises()

    const action = wrapper.find('.empty-state button')
    expect(action.exists()).toBe(true)
    expect(action.text()).toBe('重置筛选')

    await action.trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.keyword).toBeUndefined()
    })
    expect(wrapper.emitted('empty-action')).toHaveLength(1)
  })
})
