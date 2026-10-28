export type TrendPeriod = 'day' | 'week' | 'month'

export type MetricUnit = 'CNY' | 'ORDER' | 'SKU' | 'CUSTOMER' | string

export interface DashboardMetric {
  key: string
  label: string
  value: string | number
  previous_value?: string | number | null
  change_rate?: string | number | null
  unit: MetricUnit
  description?: string
  comparison_label?: string
}

export interface SalesTrendItem {
  period: string
  start_date: string
  end_date: string
  amount: string | number
  order_count: number
}

export interface DimensionSalesItem {
  id?: string | number | null
  name: string
  amount: string | number
  qty?: string | number
  ratio: string | number
}
