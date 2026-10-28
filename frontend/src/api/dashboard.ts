import { api } from '@/api/client'
import type {
  DashboardMetric,
  DimensionSalesItem,
  SalesTrendItem,
  TrendPeriod,
} from '@/components/charts/types'

export interface DashboardOrder {
  id: number
  order_no: string
  customer?: string | null
  shop_id: number
  shop_name?: string | null
  amount: string | number
  status: string
  status_label: string
  order_time?: string | null
  created_at?: string | null
}

export interface HotProduct {
  product_id: number
  product_code: string
  product_name: string
  sales_qty: string | number
  price: string | number
  previous_sales_qty: string | number
  change_rate: string | number | null
}

export interface DashboardOverview {
  as_of: string
  timezone: string
  metrics: DashboardMetric[]
  sales_trend: {
    period: TrendPeriod
    start_date: string
    end_date: string
    items: SalesTrendItem[]
  }
  category_sales: DimensionSalesItem[]
  brand_sales: DimensionSalesItem[]
  recent_orders: DashboardOrder[]
  hot_products: HotProduct[]
}

export const dashboardApi = {
  overview(trendPeriod: TrendPeriod = 'day'): Promise<DashboardOverview> {
    return api.get<DashboardOverview>('/dashboard/overview', {
      params: { trend_period: trendPeriod },
    })
  },
}
