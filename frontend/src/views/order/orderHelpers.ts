import type { StatusTone } from '@/components/types'
import type { OrderRecord } from '@/api/order'

export const ORDER_STATUS_LABELS: Record<string, string> = {
  NEW: '新订单',
  PENDING: '待处理',
  SHIPPED: '已发货',
  DONE: '已完成',
  CANCELLED: '已取消',
  DELETED: '已删除',
}

export const ORDER_AUDIT_LABELS: Record<string, string> = {
  PENDING: '待审核',
  APPROVED: '已审核',
  REJECTED: '已驳回',
}

export const ORDER_PAY_LABELS: Record<string, string> = {
  UNPAID: '未付款',
  PAID: '已付款',
  PARTIAL: '部分付款',
  REFUNDED: '已退款',
}

export const AFTER_SALE_STATUS_LABELS: Record<string, string> = {
  PENDING: '待提交',
  SUBMITTED: '待确认',
  CONFIRMED: '已确认',
  CANCELLED: '已取消',
}

export const SHIPMENT_STATUS_LABELS: Record<string, string> = {
  SCANNING: '扫描中',
  SHIPPED: '已发货',
  CANCELLED: '已取消',
}

export const PLATFORM_SYNC_LABELS: Record<string, string> = {
  PENDING: '待回传',
  SYNCED: '已回传',
  FAILED: '回传失败',
  SKIPPED: '无需回传',
}

export const PRINT_TYPE_LABELS: Record<string, string> = {
  THERMAL: '热敏面单',
  NORMAL: '普通面单',
}

export const PRINT_DOCUMENT_LABELS: Record<string, string> = {
  ALL: '全部单据',
  WAYBILL: '快递面单',
  PICK_LIST: '拣货单',
  DELIVERY_LIST: '送货单',
  SHIPPING_SLIP: '发货单',
}

export const WAVE_SORT_LABELS: Record<string, string> = {
  DEFAULT: '默认顺序',
  LOCATION: '按库位',
  SUPPLIER: '按供应商',
}

export const LOAD_NO_TYPE_LABELS: Record<string, string> = {
  BY_EXPRESS: '按快递类型',
  BY_WAREHOUSE: '按仓库',
  SINGLE: '统一装载号',
}

export interface OrderPageConfig {
  title: string
  description: string
  status?: string
  auditStatus?: string
  showCreate?: boolean
  showPrint?: boolean
  showShipment?: boolean
}

const PAGE_CONFIGS: Record<string, OrderPageConfig> = {
  'order-new': {
    title: '新订单',
    description: '查看尚未进入待处理状态的订单，并完成录入与提交审核。',
    status: 'NEW',
    showCreate: true,
  },
  'order-pending': {
    title: '待处理订单',
    description: '查看审核通过、等待波次处理和生成作业信息的订单。',
    status: 'PENDING',
    auditStatus: 'APPROVED',
  },
  'order-print': {
    title: '待打印订单',
    description: '筛选已审核订单并生成面单、拣货单和送货单。',
    status: 'PENDING',
    auditStatus: 'APPROVED',
    showPrint: true,
  },
  'order-ship': {
    title: '待发货订单',
    description: '处理已审核订单的物流信息和扫描发货。',
    status: 'PENDING',
    auditStatus: 'APPROVED',
    showShipment: true,
  },
  'order-shipped': {
    title: '已发货订单',
    description: '查看已完成包裹出库的订单及平台回传状态。',
    status: 'SHIPPED',
  },
  'order-done': {
    title: '已完成订单',
    description: '查看已结束履约流程的历史订单。',
    status: 'DONE',
  },
  'order-process': {
    title: '订单处理',
    description: '集中处理已审核订单的发货仓库、物流和履约状态。',
    status: 'PENDING',
    auditStatus: 'APPROVED',
    showPrint: true,
    showShipment: true,
  },
}

export function orderPageConfig(routeName: unknown): OrderPageConfig {
  return (
    PAGE_CONFIGS[String(routeName || '')] || {
      title: '订单管理',
      description: '统一查询和处理订单履约数据。',
    }
  )
}

export function canEditOrder(order: OrderRecord): boolean {
  return (
    ['NEW', 'PENDING'].includes(order.status) &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(
      String(order.approval?.state.status || ''),
    )
  )
}

export function canSubmitOrder(order: OrderRecord): boolean {
  return (
    order.status === 'NEW' &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(
      String(order.approval?.state.status || ''),
    )
  )
}

export function canAuditOrder(order: OrderRecord): boolean {
  return (
    order.status === 'NEW' &&
    ['NOT_SUBMITTED', 'PENDING'].includes(
      String(order.approval?.state.status || 'PENDING'),
    )
  )
}

export function canUnauditOrder(order: OrderRecord): boolean {
  return (
    order.status === 'PENDING' &&
    order.audit_status === 'APPROVED' &&
    order.approval?.state.status === 'APPROVED'
  )
}

export function canSetFulfillment(order: OrderRecord): boolean {
  return ['NEW', 'PENDING'].includes(order.status)
}

export function statusTone(status: string): StatusTone {
  const normalized = String(status || '').toUpperCase()
  if (['DONE', 'SHIPPED', 'APPROVED', 'CONFIRMED', 'SYNCED'].includes(normalized)) {
    return 'success'
  }
  if (['PENDING', 'SUBMITTED', 'SCANNING'].includes(normalized)) {
    return 'warning'
  }
  if (['REJECTED', 'CANCELLED', 'FAILED', 'DELETED'].includes(normalized)) {
    return 'danger'
  }
  return 'info'
}

export function labelOf(
  map: Record<string, string>,
  value?: string | null,
): string {
  if (!value) {
    return '--'
  }
  return map[value] || value
}
