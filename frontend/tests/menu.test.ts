import { describe, expect, it } from 'vitest'

import { filterMenuTree, flattenMenuItems, menuTree } from '@/router/menu'

describe('menu permission filtering', () => {
  it('keeps only modules granted to the purchaser role', () => {
    const visible = filterMenuTree(menuTree, [
      'module:dashboard',
      'module:purchase',
      'module:product',
      'module:approval',
      'purchase:create',
    ])
    const titles = visible.map((item) => item.title)

    expect(titles).toEqual(['首页看板', 'AI 助手', '快捷录入', '采购管理', '商品管理', '协同办公'])
    expect(titles).not.toContain('财务管理')
    expect(titles).not.toContain('库存管理')
  })

  it('allows administrator wildcard access', () => {
    const visible = filterMenuTree(menuTree, ['*'])
    expect(visible).toHaveLength(10)
    expect(flattenMenuItems(visible).length).toBeGreaterThan(40)
  })

  it('shows the assistant entry to each role with the shared dashboard permission', () => {
    const rolePermissions = [
      ['module:dashboard'],
      ['module:dashboard', 'module:purchase'],
      ['module:dashboard', 'module:order'],
      ['module:dashboard', 'module:stock'],
      ['module:dashboard', 'module:finance'],
      ['module:dashboard', 'module:product'],
    ]

    for (const permissions of rolePermissions) {
      const visible = flattenMenuItems(filterMenuTree(menuTree, permissions))
      expect(visible[0]?.name).toBe('dashboard')
      expect(visible[1]?.name).toBe('assistant')
      expect(visible[2]?.name).toBe('quick-entry')
    }
  })

  it('requires finance read permission for the procurement payment entry', () => {
    const purchaser = flattenMenuItems(filterMenuTree(menuTree, ['module:purchase']))
    expect(purchaser.map((item) => item.name)).not.toContain('purchase-payments')
    const finance = flattenMenuItems(filterMenuTree(menuTree, ['module:purchase', 'module:finance']))
    expect(finance.map((item) => item.name)).toContain('purchase-payments')
  })

  it('separates personal approvals from flow configuration', () => {
    const personal = flattenMenuItems(filterMenuTree(menuTree, ['module:approval']))
    expect(personal.map((item) => item.name)).toContain('approval-mine')
    expect(personal.map((item) => item.name)).not.toContain('approval-flows')
    const configured = flattenMenuItems(filterMenuTree(menuTree, ['module:approval', 'approval:configure']))
    expect(configured.map((item) => item.name)).toContain('approval-flows')
  })

  it('hides parent modules when all children are unavailable', () => {
    const visible = filterMenuTree(menuTree, ['module:finance'])
    expect(visible.map((item) => item.title)).toEqual(['财务管理'])
  })
})
