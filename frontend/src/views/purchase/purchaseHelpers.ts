import type { StatusTone } from '@/components/types'

export const PURCHASE_STATUS_LABELS: Record<string, string> = {
  PENDING: '待审核',
  APPROVED: '已审核',
  REJECTED: '已驳回',
  IN_PROGRESS: '进行中',
  ABORTED: '已中止',
  FINISHED: '已完成',
  TERMINATED: '已终止',
  RECEIVING: '收货中',
  RECEIVED: '已收货',
  CLOSED: '已关闭',
  FINANCE_APPROVED: '财务已审核',
  NONE: '未核销',
  PARTIAL: '部分核销',
  FULL: '已核销',
  NOT_CREATED: '未生成',
  PENDING_OUTBOUND: '待出库',
  RECORDED: '已退款',
}

export const AUDIT_STATUS_LABELS: Record<string, string> = {
  PENDING: '待审核',
  APPROVED: '已审核',
  REJECTED: '已驳回',
}

export const ARRIVAL_STATUS_LABELS: Record<string, string> = {
  PENDING: '未到货',
  PARTIAL: '部分到货',
  FULL: '全部到货',
}

export const INVOICE_TYPE_OPTIONS = [
  { value: 'SPECIAL', label: '专用发票', taxRate: '13' },
  { value: 'AGRICULTURAL', label: '农副产品发票', taxRate: '9' },
  { value: 'NORMAL', label: '普通发票', taxRate: '0' },
  { value: 'RED_SPECIAL', label: '红字专用发票', taxRate: '13' },
  { value: 'RED_AGRICULTURAL', label: '红字农副产品发票', taxRate: '9' },
  { value: 'RED_NORMAL', label: '红字普通发票', taxRate: '0' },
]

export const INVOICE_WRITE_OFF_OPTIONS = [
  { value: 'NONE', label: '未核销' },
  { value: 'PARTIAL', label: '部分核销' },
  { value: 'FULL', label: '已核销' },
]

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

export function statusTone(status?: string | null): StatusTone {
  const code = String(status || '').toUpperCase()
  if (['APPROVED', 'FINISHED', 'RECEIVED', 'FULL', 'RECORDED'].includes(code)) {
    return 'success'
  }
  if (['REJECTED'].includes(code)) {
    return 'danger'
  }
  if (
    ['PENDING', 'PARTIAL', 'IN_PROGRESS', 'RECEIVING', 'FINANCE_APPROVED', 'PENDING_OUTBOUND'].includes(
      code,
    )
  ) {
    return 'warning'
  }
  if (['ABORTED', 'TERMINATED', 'CLOSED'].includes(code)) {
    return 'info'
  }
  return 'primary'
}

export function invoiceTypeLabel(code?: string | null): string {
  return (
    INVOICE_TYPE_OPTIONS.find((item) => item.value === code)?.label ||
    code ||
    '--'
  )
}

export function invoiceDefaultTaxRate(code?: string | null): string {
  return (
    INVOICE_TYPE_OPTIONS.find((item) => item.value === code)?.taxRate || '0'
  )
}

export function toDateTimeInput(value?: string | null): string {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 19)
}

export function toIsoDateTime(value?: string | null): string | null {
  if (!value) {
    return null
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toISOString()
}

export function reportSummaryLabel(key: string): string {
  const labels: Record<string, string> = {
    supplier_count: '供应商数',
    order_count: '订单数',
    total_qty: '采购数量',
    total_amount: '采购金额',
    arrived_qty: '到货数量',
    in_transit_qty: '在途数量',
    stock_in_count: '入库单数',
    qty: '入库数量',
    amount: '入库金额',
    invoice_count: '发票数',
    tax_amount: '税额',
    net_amount: '不含税金额',
    write_off_amount: '已核销金额',
    sku_count: '规格数',
    current_qty: '当前库存',
  }
  return labels[key] || key
}

