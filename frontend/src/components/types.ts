import type { Component } from 'vue'

export type StatusTone = 'primary' | 'success' | 'warning' | 'info' | 'danger'

export interface StatusMeta {
  tone: StatusTone
  icon: Component
  label?: string
}

export type DataTableFilterValue =
  | string
  | number
  | boolean
  | null
  | undefined

export interface DataTableQuery {
  page: number
  pageSize: number
  filters: Record<string, DataTableFilterValue>
}

export interface DataTableColumn<T = Record<string, unknown>> {
  type?: 'selection' | 'index'
  prop?: string
  label: string
  width?: string | number
  minWidth?: string | number
  align?: 'left' | 'center' | 'right'
  headerAlign?: 'left' | 'center' | 'right'
  fixed?: boolean | 'left' | 'right'
  sortable?: boolean | 'custom'
  showOverflowTooltip?: boolean
  formatter?: (
    row: T,
    value: unknown,
    index: number,
  ) => string | number
  slot?: string
}

export interface AuditTimelineItem {
  id?: string | number
  title: string
  description?: string
  timestamp?: string | number | Date | null
  operator?: string
  status?: string
  statusLabel?: string
  statusTone?: StatusTone
}

export interface MoneyFormatOptions {
  precision?: number
  symbol?: string
  showSymbol?: boolean
  fallback?: string
}
