import type { Component } from 'vue'
import type { RouteRecordRaw } from 'vue-router'

import AdminLayout from '@/layouts/AdminLayout.vue'
import ForbiddenView from '@/views/common/ForbiddenView.vue'
import NotFoundView from '@/views/common/NotFoundView.vue'
import PlaceholderView from '@/views/common/PlaceholderView.vue'
import ForgotPasswordView from '@/views/login/ForgotPasswordView.vue'
import LoginView from '@/views/login/LoginView.vue'
import { menuTree, type MenuItem } from '@/router/menu'

type AsyncView = () => Promise<{ default: Component }>

const routeComponents: Partial<Record<string, Component | AsyncView>> = {
  dashboard: () => import('@/views/dashboard/DashboardView.vue'),
  assistant: () => import('@/views/assistant/AssistantWorkspaceView.vue'),
  'quick-entry': () => import('@/views/quick-entry/QuickEntryView.vue'),
  'approval-flows': () => import('@/views/approval/ApprovalFlowsView.vue'),
  'approval-mine': () => import('@/views/approval/MyApprovalView.vue'),
  'finance-subjects': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-project-categories': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-vouchers': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-payments': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-receipts': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-expenses': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-settlements': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-incomes': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-supplier-balances': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'finance-invoices': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'order-done': () => import('@/views/order/OrderListView.vue'),
  'order-new': () => import('@/views/order/OrderListView.vue'),
  'order-pending': () => import('@/views/order/OrderListView.vue'),
  'order-process': () => import('@/views/order/OrderListView.vue'),
  'order-returns': () => import('@/views/order/AfterSaleView.vue'),
  'order-settings': () => import('@/views/order/OrderSettingsView.vue'),
  'order-ship': () => import('@/views/order/OrderListView.vue'),
  'order-shipped': () => import('@/views/order/OrderListView.vue'),
  'order-waves': () => import('@/views/order/WaveManagementView.vue'),
  'purchase-invoices': () =>
    import('@/views/purchase/PurchaseInvoiceListView.vue'),
  'purchase-payments': () => import('@/views/finance/FinanceWorkspaceView.vue'),
  'purchase-orders': () =>
    import('@/views/purchase/PurchaseOrderListView.vue'),
  'purchase-plans': () => import('@/views/purchase/PurchasePlanListView.vue'),
  'purchase-prepays': () =>
    import('@/views/purchase/PurchasePrepayListView.vue'),
  'purchase-reports': () => import('@/views/purchase/PurchaseReportView.vue'),
  'purchase-returns': () =>
    import('@/views/purchase/PurchaseReturnListView.vue'),
  'product-combos': () => import('@/views/product/ComboListView.vue'),
  'product-list': () => import('@/views/product/ProductListView.vue'),
  'settings-company': () => import('@/views/settings/CompanySettingsView.vue'),
  'settings-customers': () =>
    import('@/views/settings/CustomerManagementView.vue'),
  'settings-logistics': () => import('@/views/settings/LogisticsToolsView.vue'),
  'settings-product': () => import('@/views/product/ProductSettingsView.vue'),
  'settings-shops': () => import('@/views/settings/ShopIntegrationView.vue'),
  'settings-suppliers': () =>
    import('@/views/settings/SupplierManagementView.vue'),
  'stock-adjustments': () =>
    import('@/views/stock/StockAdjustmentView.vue'),
  'stock-check': () => import('@/views/stock/StockCheckView.vue'),
  'stock-costs': () => import('@/views/stock/StockCostView.vue'),
  'stock-flows': () => import('@/views/stock/StockFlowView.vue'),
  'stock-inbound': () => import('@/views/stock/StockInboundView.vue'),
  'stock-locations': () =>
    import('@/views/stock/LocationManagementView.vue'),
  'stock-options': () => import('@/views/stock/StockOptionsView.vue'),
  'stock-purchase-out': () =>
    import('@/views/stock/StockOutboundView.vue'),
  'stock-query': () => import('@/views/stock/StockQueryView.vue'),
  'stock-settlement': () =>
    import('@/views/stock/StockSettlementView.vue'),
  'stock-shop': () => import('@/views/stock/ShopStockView.vue'),
  'stock-transfers': () => import('@/views/stock/StockTransferView.vue'),
  'stock-warnings': () => import('@/views/stock/StockWarningView.vue'),
  'stock-warehouses': () =>
    import('@/views/stock/WarehouseManagementView.vue'),
}

function leafRoute(item: MenuItem, module?: MenuItem): RouteRecordRaw {
  return {
    path: item.path.replace(/^\//, ''),
    name: item.name,
    component: routeComponents[item.name] || PlaceholderView,
    meta: {
      title: item.title,
      permission: item.permission,
      parentTitle: module?.title,
      parentPath: module?.path,
    },
  }
}

function menuRoutes(): RouteRecordRaw[] {
  return menuTree.flatMap((item) => {
    if (!item.children?.length) {
      return [leafRoute(item)]
    }
    const children = item.children.map((child) => leafRoute(child, item))
    const firstChild = item.children[0]
    return [
      {
        path: item.path.replace(/^\//, ''),
        name: item.name,
        redirect: { name: firstChild.name },
        meta: {
          title: item.title,
          permission: item.permission,
        },
      },
      ...children,
    ]
  })
}

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: {
      title: '登录',
      public: true,
    },
  },
  {
    path: '/forgot-password',
    name: 'forgot-password',
    component: ForgotPasswordView,
    meta: {
      title: '忘记密码',
      public: true,
    },
  },
  {
    path: '/',
    component: AdminLayout,
    redirect: '/dashboard',
    children: [
      ...menuRoutes(),
      {
        path: 'notifications',
        name: 'notifications',
        component: () => import('@/views/notifications/NotificationCenterView.vue'),
        meta: { title: '消息中心' },
      },
      {
        path: 'orders/print-workbench',
        name: 'order-print-workbench',
        component: () =>
          import('@/views/order/OrderPrintView.vue'),
        meta: {
          title: '订单打印',
          permission: 'module:order',
          parentTitle: '订单管理',
          parentPath: '/orders',
        },
      },
      {
        path: 'orders/scan-shipment',
        name: 'order-scan-shipment',
        component: () =>
          import('@/views/order/ScanShipmentView.vue'),
        meta: {
          title: '扫描发货',
          permission: 'module:order',
          parentTitle: '订单管理',
          parentPath: '/orders',
        },
      },
      {
        path: '403',
        name: 'forbidden',
        component: ForbiddenView,
        meta: {
          title: '无权访问',
        },
      },
      {
        path: ':pathMatch(.*)*',
        name: 'not-found',
        component: NotFoundView,
        meta: { title: '页面不存在' },
      },
    ],
  },
]
