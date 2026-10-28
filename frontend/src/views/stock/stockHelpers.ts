import type { StatusTone } from '@/components/types'

export const STOCK_AUDIT_LABELS: Record<string, string> = {
  PENDING: '待审核',
  APPROVED: '已审核',
  REJECTED: '已驳回',
}

export const STOCK_DOC_STATUS_LABELS: Record<string, string> = {
  PENDING: '待处理',
  APPROVED: '已审核',
  REJECTED: '已驳回',
  RECEIVED: '已收货',
  TERMINATED: '已作废',
  FINISHED: '已完成',
  PURCHASE_IN: '采购入库',
  TRANSFER_IN: '调拨入库',
  TRANSFER_OUT: '调拨出库',
  STOCK_CHECK: '库存盘点',
}

export const OFF_SHELF_STATUS_LABELS: Record<string, string> = {
  NONE: '待下架',
  PENDING: '待下架',
  DONE: '已下架',
}

export const WAREHOUSE_TYPE_LABELS: Record<string, string> = {
  FINISHED: '成品仓',
  DEFECTIVE: '次品仓',
}

export const LOCK_TYPE_LABELS: Record<string, string> = {
  NONE: '未锁定',
  MANUAL: '手动锁定',
  CHECK: '盘点锁定',
}

export const STOCK_IN_TYPE_LABELS: Record<string, string> = {
  PURCHASE: '采购入库',
  MANUAL: '手动入库',
  AFTER_SALE: '售后退货入库',
  TRANSFER: '调拨入库',
  CHECK: '盘盈入库',
  PUSH: '推送入库',
  ADJUST: '调整入库',
}

export const STOCK_FLOW_BIZ_LABELS: Record<string, string> = {
  PURCHASE_IN: '采购入库',
  MANUAL_IN: '手动入库',
  CHECK_IN: '盘盈入库',
  CHECK_OUT: '盘亏出库',
  PURCHASE_OUT: '采购出库',
  TRANSFER_OUT: '调拨出库',
  TRANSFER_IN: '调拨入库',
  SALE_OUT: '销售出库',
  LOCATION_ADJUST: '库位调整',
}

export function labelOf(
  mapping: Record<string, string>,
  code?: string | null,
  fallback = '--',
): string {
  if (!code) {
    return fallback
  }
  return mapping[code] || code
}

export function stockStatusTone(status?: string | null): StatusTone {
  const code = String(status || '').toUpperCase()
  if (['APPROVED', 'FINISHED', 'DONE', 'RECEIVED'].includes(code)) {
    return 'success'
  }
  if (['REJECTED', 'TERMINATED'].includes(code)) {
    return 'danger'
  }
  if (['PENDING', 'CHECK', 'MANUAL'].includes(code)) {
    return 'warning'
  }
  if (['NONE', 'REJECTED'].includes(code)) {
    return 'info'
  }
  return 'primary'
}

export function directionLabel(direction?: string | null): string {
  return direction === 'IN' ? '入库' : direction === 'OUT' ? '出库' : '--'
}

export function directionTone(direction?: string | null): StatusTone {
  return direction === 'IN' ? 'success' : 'warning'
}

export function quantity(value: unknown, fallback = '0'): string {
  if (value === null || value === undefined || value === '') {
    return fallback
  }
  return String(value)
}

export function numberValue(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function isPositiveQuantity(value: unknown): boolean {
  return numberValue(value) > 0
}

export function formatDate(value?: string | null): string {
  if (!value) {
    return '--'
  }
  return value.slice(0, 10)
}

export function todayText(): string {
  const date = new Date()
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

export function currentMonthLastDay(): string {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth() + 1, 0)
    .toISOString()
    .slice(0, 10)
}

export function canEditStockDocument(record: {
  status: string
  audit_status: string
}): boolean {
  return (
    ['PENDING', 'REJECTED'].includes(record.status) &&
    ['PENDING', 'REJECTED'].includes(record.audit_status)
  )
}

export function approvalState(
  record: { approval?: { state?: { status?: string } } } | null | undefined,
): string {
  return String(record?.approval?.state?.status || '')
}

export function canSubmitApproval(
  record: {
    status: string
    audit_status: string
    approval?: { state?: { status?: string } }
  },
): boolean {
  return (
    ['PENDING', 'REJECTED'].includes(record.status) &&
    ['NOT_SUBMITTED', 'REJECTED'].includes(approvalState(record))
  )
}

export function canAuditApproval(record: {
  status: string
  audit_status: string
}): boolean {
  return (
    ['PENDING', 'APPROVED'].includes(record.status) &&
    record.audit_status === 'PENDING'
  )
}
