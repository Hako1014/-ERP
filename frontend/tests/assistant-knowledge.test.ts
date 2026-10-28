import { execFileSync } from 'node:child_process'
import { basename, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import assistantRouteManifest from '@/generated/assistantRoutes.json'
import { flattenMenuItems, menuTree } from '@/router/menu'
import { routes } from '@/router/routes'
import {
  isRegisteredAssistantPath,
  resolveAllowedPageFilters,
  resolvePageLabel,
} from '@/layouts/components/assistantShell'

const workingDirectory = process.cwd()
const frontendRoot = basename(workingDirectory).toLowerCase() === 'frontend'
  ? workingDirectory
  : resolve(workingDirectory, 'frontend')

describe('AI 助手共享页面知识索引', () => {
  it('覆盖 menuTree 全部 path/title/permission 且没有重复路径', () => {
    const generated = new Map(
      assistantRouteManifest.routes.map((route) => [route.path, route]),
    )
    const menuEntries = flattenMenuItems(menuTree)

    for (const item of menuEntries) {
      expect(generated.get(item.path)).toMatchObject({
        title: item.title,
        permission: item.permission,
      })
    }
    expect(new Set(generated.keys()).size).toBe(generated.size)
    expect(resolvePageLabel('/orders/print-workbench')).toBe('订单打印')
    expect(resolvePageLabel('/orders/scan-shipment')).toBe('扫描发货')
  })

  it('也覆盖 Vue Router 中所有实际注册且受权限保护的路由', () => {
    const generated = new Map(
      assistantRouteManifest.routes.map((route) => [route.path, route]),
    )
    const registeredRoutes = createRouter({
      history: createMemoryHistory(),
      routes,
    }).getRoutes().filter((route) => typeof route.meta.permission === 'string')

    for (const route of registeredRoutes) {
      expect(generated.get(route.path)).toMatchObject({
        title: route.meta.title,
        permission: route.meta.permission,
      })
    }
    expect(registeredRoutes.length).toBeGreaterThan(50)
  })

  it('权限过滤站内导航，未知路由和越权模块不生成可点击链接', () => {
    const purchaserPermissions = ['module:dashboard', 'module:purchase']
    expect(isRegisteredAssistantPath('/purchase/plans', purchaserPermissions)).toBe(true)
    expect(isRegisteredAssistantPath('/finance/vouchers', purchaserPermissions)).toBe(false)
    expect(isRegisteredAssistantPath('//evil.example', ['*'])).toBe(false)
  })

  it('页面上下文只携带无敏感信息的白名单筛选条件', () => {
    expect(resolveAllowedPageFilters({
      status: 'draft',
      date_from: '2026-10-01',
      customer_id: 'secret-1',
      selected_document_id: '42',
      date_to: ['2026-10-31'],
      keyword: '客户名称',
    })).toEqual({ status: 'draft', date_from: '2026-10-01' })
  })

  it('菜单、业务路由和用户手册变更后必须重新生成共享索引', () => {
    const output = execFileSync(
      process.execPath,
      ['scripts/generate-assistant-knowledge.mjs', '--check'],
      { cwd: frontendRoot, encoding: 'utf8' },
    )
    expect(output).toContain(assistantRouteManifest.version)
    expect(assistantRouteManifest.routes.length).toBeGreaterThan(50)
    expect(output).toContain(`覆盖 ${assistantRouteManifest.routes.length} 条路由`)
  })
})
