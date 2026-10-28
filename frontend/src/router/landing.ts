import { flattenMenuItems, menuTree } from '@/router/menu'

interface LandingDefinition {
  path: string
  permission: string
}

const ROLE_LANDING_PAGES: Record<string, LandingDefinition> = {
  ADMIN: { path: '/dashboard', permission: 'module:dashboard' },
  VP: { path: '/approval/mine', permission: 'module:approval' },
  FINANCE: { path: '/finance/vouchers', permission: 'module:finance' },
  PURCHASER: { path: '/purchase/plans', permission: 'module:purchase' },
  WAREHOUSE: { path: '/stock/query', permission: 'module:stock' },
  QUALITY: { path: '/purchase/orders', permission: 'module:purchase' },
  AFTER_SALE: { path: '/orders/returns', permission: 'module:order' },
}

export function resolveLandingPath(
  roles: string[],
  hasPermission: (permission: string) => boolean,
): string {
  for (const role of roles) {
    const landing = ROLE_LANDING_PAGES[role]
    if (landing && hasPermission(landing.permission)) {
      return landing.path
    }
  }

  const firstAccessiblePage = flattenMenuItems(menuTree).find(
    (item) => !item.children?.length && hasPermission(item.permission),
  )
  return firstAccessiblePage?.path || '/403'
}
