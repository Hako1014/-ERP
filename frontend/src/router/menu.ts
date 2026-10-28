import type { Component } from 'vue'
import {
  Box,
  ChatDotRound,
  DataBoard,
  Document,
  DocumentAdd,
  Goods,
  OfficeBuilding,
  Setting,
  ShoppingCart,
  Tickets,
} from '@element-plus/icons-vue'

export interface MenuItem {
  name: string
  title: string
  path: string
  permission: string
  icon?: Component
  children?: MenuItem[]
}

export const menuTree: MenuItem[] = [
  {
    name: 'dashboard',
    title: '首页看板',
    path: '/dashboard',
    permission: 'module:dashboard',
    icon: DataBoard,
  },
  {
    name: 'assistant',
    title: 'AI 助手',
    path: '/assistant',
    permission: 'module:dashboard',
    icon: ChatDotRound,
  },
  {
    name: 'quick-entry',
    title: '快捷录入',
    path: '/quick-entry',
    permission: 'module:dashboard',
    icon: DocumentAdd,
  },
  {
    name: 'purchase',
    title: '采购管理',
    path: '/purchase',
    permission: 'module:purchase',
    icon: ShoppingCart,
    children: [
      { name: 'purchase-plans', title: '采购计划', path: '/purchase/plans', permission: 'module:purchase' },
      { name: 'purchase-orders', title: '采购订单', path: '/purchase/orders', permission: 'module:purchase' },
      { name: 'purchase-prepays', title: '采购预付款', path: '/purchase/prepays', permission: 'module:purchase' },
      { name: 'purchase-returns', title: '采购退货单', path: '/purchase/returns', permission: 'module:purchase' },
      { name: 'purchase-invoices', title: '采购发票', path: '/purchase/invoices', permission: 'module:purchase' },
      { name: 'purchase-payments', title: '采购付款单', path: '/purchase/payments', permission: 'module:finance' },
      { name: 'purchase-incomes', title: '收入单管理', path: '/purchase/incomes', permission: 'module:purchase' },
      { name: 'purchase-reports', title: '采购报表', path: '/purchase/reports', permission: 'module:purchase' },
    ],
  },
  {
    name: 'products',
    title: '商品管理',
    path: '/products',
    permission: 'module:product',
    icon: Goods,
    children: [
      { name: 'product-list', title: '商品资料', path: '/products/list', permission: 'module:product' },
      { name: 'product-combos', title: '组合商品列表', path: '/products/combos', permission: 'module:product' },
    ],
  },
  {
    name: 'orders',
    title: '订单管理',
    path: '/orders',
    permission: 'module:order',
    icon: Tickets,
    children: [
      { name: 'order-new', title: '新订单', path: '/orders/new', permission: 'module:order' },
      { name: 'order-pending', title: '待处理订单', path: '/orders/pending', permission: 'module:order' },
      { name: 'order-print', title: '待打印订单', path: '/orders/print', permission: 'module:order' },
      { name: 'order-ship', title: '待发货订单', path: '/orders/ship', permission: 'module:order' },
      { name: 'order-shipped', title: '已发货订单', path: '/orders/shipped', permission: 'module:order' },
      { name: 'order-done', title: '已完成订单', path: '/orders/done', permission: 'module:order' },
      { name: 'order-returns', title: '直营退货订单', path: '/orders/returns', permission: 'module:order' },
      { name: 'order-waves', title: '波次管理', path: '/orders/waves', permission: 'module:order' },
      { name: 'order-process', title: '订单处理', path: '/orders/process', permission: 'module:order' },
      { name: 'order-settings', title: '订单设置', path: '/orders/settings', permission: 'module:order' },
    ],
  },
  {
    name: 'stock',
    title: '库存管理',
    path: '/stock',
    permission: 'module:stock',
    icon: Box,
    children: [
      { name: 'stock-warehouses', title: '仓库管理', path: '/stock/warehouses', permission: 'module:stock' },
      { name: 'stock-locations', title: '库位管理', path: '/stock/locations', permission: 'module:stock' },
      { name: 'stock-purchase-out', title: '采购出库', path: '/stock/purchase-out', permission: 'module:stock' },
      { name: 'stock-transfers', title: '调拨管理', path: '/stock/transfers', permission: 'module:stock' },
      { name: 'stock-flows', title: '出入库查询', path: '/stock/flows', permission: 'module:stock' },
      { name: 'stock-query', title: '库存查询', path: '/stock/query', permission: 'module:stock' },
      { name: 'stock-adjustments', title: '库存调整单', path: '/stock/adjustments', permission: 'module:stock' },
      { name: 'stock-inbound', title: '采购入库', path: '/stock/inbound', permission: 'module:stock' },
      { name: 'stock-check', title: '库存盘点', path: '/stock/check', permission: 'module:stock' },
      { name: 'stock-warnings', title: '库存预警', path: '/stock/warnings', permission: 'module:stock' },
      { name: 'stock-shop', title: '店铺库存设置', path: '/stock/shop-stock', permission: 'module:stock' },
      { name: 'stock-settlement', title: '库存结存', path: '/stock/settlement', permission: 'module:stock' },
      { name: 'stock-options', title: '库存功能选项', path: '/stock/options', permission: 'module:stock' },
      { name: 'stock-costs', title: '库存成本', path: '/stock/costs', permission: 'module:stock' },
    ],
  },
  {
    name: 'approval',
    title: '协同办公',
    path: '/approval',
    permission: 'module:approval',
    icon: Document,
    children: [
      { name: 'approval-flows', title: '审批管理', path: '/approval/flows', permission: 'approval:configure' },
      { name: 'approval-mine', title: '我的审批', path: '/approval/mine', permission: 'module:approval' },
    ],
  },
  {
    name: 'finance',
    title: '财务管理',
    path: '/finance',
    permission: 'module:finance',
    icon: OfficeBuilding,
    children: [
      { name: 'finance-subjects', title: '会计科目', path: '/finance/subjects', permission: 'module:finance' },
      { name: 'finance-project-categories', title: '项目核算类别', path: '/finance/project-categories', permission: 'module:finance' },
      { name: 'finance-vouchers', title: '凭证管理', path: '/finance/vouchers', permission: 'module:finance' },
      { name: 'finance-payments', title: '付款单', path: '/finance/payments', permission: 'module:finance' },
      { name: 'finance-receipts', title: '收款单', path: '/finance/receipts', permission: 'module:finance' },
      { name: 'finance-expenses', title: '费用单', path: '/finance/expenses', permission: 'module:finance' },
      { name: 'finance-settlements', title: '结算单', path: '/finance/settlements', permission: 'module:finance' },
      { name: 'finance-incomes', title: '收入单', path: '/finance/incomes', permission: 'module:finance' },
      { name: 'finance-supplier-balances', title: '供应商账户余额', path: '/finance/supplier-balances', permission: 'module:finance' },
      { name: 'finance-invoices', title: '发票管理', path: '/finance/invoices', permission: 'module:finance' },
    ],
  },
  {
    name: 'settings',
    title: '基础设置',
    path: '/settings',
    permission: 'module:settings',
    icon: Setting,
    children: [
      { name: 'settings-company', title: '公司设置', path: '/settings/company', permission: 'module:settings' },
      { name: 'settings-product', title: '商品设置', path: '/settings/product', permission: 'module:settings' },
      { name: 'settings-logistics', title: '物流工具', path: '/settings/logistics', permission: 'module:settings' },
      { name: 'settings-shops', title: '店铺对接', path: '/settings/shops', permission: 'module:settings' },
      { name: 'settings-suppliers', title: '供应商管理', path: '/settings/suppliers', permission: 'module:settings' },
      { name: 'settings-customers', title: '客户管理', path: '/settings/customers', permission: 'module:settings' },
    ],
  },
]

function canAccess(permission: string, permissions: Set<string>): boolean {
  return permissions.has('*') || permissions.has(permission)
}

export function filterMenuTree(
  items: MenuItem[],
  permissionCodes: string[],
): MenuItem[] {
  const permissions = new Set(permissionCodes)
  return items.flatMap((item) => {
    if (item.children?.length) {
      const children = filterMenuTree(item.children, permissionCodes)
      if (!children.length || !canAccess(item.permission, permissions)) {
        return []
      }
      return [{ ...item, children }]
    }
    return canAccess(item.permission, permissions) ? [{ ...item }] : []
  })
}

export function flattenMenuItems(items: MenuItem[]): MenuItem[] {
  return items.flatMap((item) => [
    item,
    ...(item.children ? flattenMenuItems(item.children) : []),
  ])
}
