import { api } from '@/api/client'

export type ChargeType = 'BY_COUNT' | 'BY_WEIGHT'

export interface Logistics {
  id: number
  name: string
  company?: string | null
  contact?: string | null
  phone?: string | null
  address?: string | null
  is_default: boolean
  is_enabled: boolean
}

export interface FreightTemplate {
  id: number
  name: string
  logistics_id: number
  logistics_name?: string | null
  charge_type: ChargeType | string
  first_count: number
  continue_count: number
  first_weight?: string | number | null
  continue_weight?: string | number | null
  base_fee: string | number
  continue_fee: string | number
  deduct_weight?: string | number | null
  operation_fee: string | number
  consumable_fee: string | number
  ewaybill_fee: string | number
  tax_fee: string | number
  tax_point?: string | number | null
}

export interface AreaRule {
  id: number
  logistics_id: number
  logistics_name?: string | null
  province?: string | null
  city?: string | null
  match_weight_min?: string | number | null
  match_weight_max?: string | number | null
  freight_template_id?: number | null
  freight_template_name?: string | null
}

export interface PrintTemplate {
  id: number
  name: string
  template_type: string
  logistics_id?: number | null
  content?: string | null
  paper_width?: string | number | null
  paper_height?: string | number | null
  is_default: boolean
}

export interface FreightCalculation {
  template_id: number
  template_name: string
  charge_type: ChargeType | string
  quantity_label: string
  quantity: string | number | null
  charge_weight?: string | number | null
  additional_units: number
  base_fee: string | number
  additional_fee: string | number
  freight_fee: string | number
  operation_fee: string | number
  consumable_fee: string | number
  ewaybill_fee: string | number
  tax_point?: string | number | null
  percentage_tax: string | number
  fixed_tax_fee: string | number
  tax_amount: string | number
  total_fee: string | number
}

export type LogisticsPayload = Partial<
  Pick<
    Logistics,
    'name' | 'company' | 'contact' | 'phone' | 'address' | 'is_default' | 'is_enabled'
  >
>

export type FreightTemplatePayload = Partial<
  Pick<
    FreightTemplate,
    | 'name'
    | 'logistics_id'
    | 'charge_type'
    | 'first_count'
    | 'continue_count'
    | 'first_weight'
    | 'continue_weight'
    | 'base_fee'
    | 'continue_fee'
    | 'deduct_weight'
    | 'operation_fee'
    | 'consumable_fee'
    | 'ewaybill_fee'
    | 'tax_fee'
    | 'tax_point'
  >
>

export type AreaRulePayload = Partial<
  Pick<
    AreaRule,
    | 'logistics_id'
    | 'province'
    | 'city'
    | 'match_weight_min'
    | 'match_weight_max'
    | 'freight_template_id'
  >
>

export type PrintTemplatePayload = Partial<
  Pick<
    PrintTemplate,
    | 'name'
    | 'template_type'
    | 'logistics_id'
    | 'content'
    | 'paper_width'
    | 'paper_height'
    | 'is_default'
  >
>

export const logisticsApi = {
  list(keyword?: string, isEnabled?: boolean): Promise<Logistics[]> {
    return api.get<Logistics[]>('/logistics', {
      params: { keyword, is_enabled: isEnabled },
    })
  },
  create(payload: LogisticsPayload): Promise<Logistics> {
    return api.post<Logistics>('/logistics', payload)
  },
  update(id: number, payload: LogisticsPayload): Promise<Logistics> {
    return api.put<Logistics>(`/logistics/${id}`, payload)
  },
  delete(id: number): Promise<void> {
    return api.delete<void>(`/logistics/${id}`)
  },
  enable(id: number): Promise<Logistics> {
    return api.post<Logistics>(`/logistics/${id}/enable`)
  },
  disable(id: number): Promise<Logistics> {
    return api.post<Logistics>(`/logistics/${id}/disable`)
  },
  setDefault(id: number): Promise<Logistics> {
    return api.post<Logistics>(`/logistics/${id}/default`)
  },
  listFreightTemplates(logisticsId?: number): Promise<FreightTemplate[]> {
    return api.get<FreightTemplate[]>('/freight-templates', {
      params: { logistics_id: logisticsId },
    })
  },
  createFreightTemplate(
    payload: FreightTemplatePayload,
  ): Promise<FreightTemplate> {
    return api.post<FreightTemplate>('/freight-templates', payload)
  },
  updateFreightTemplate(
    id: number,
    payload: FreightTemplatePayload,
  ): Promise<FreightTemplate> {
    return api.put<FreightTemplate>(`/freight-templates/${id}`, payload)
  },
  deleteFreightTemplate(id: number): Promise<void> {
    return api.delete<void>(`/freight-templates/${id}`)
  },
  calculateFreight(
    id: number,
    query: { count?: number; weight?: string | number },
  ): Promise<FreightCalculation> {
    return api.get<FreightCalculation>(
      `/freight-templates/${id}/calculate`,
      { params: query },
    )
  },
  listAreaRules(logisticsId?: number): Promise<AreaRule[]> {
    return api.get<AreaRule[]>('/area-rules', {
      params: { logistics_id: logisticsId },
    })
  },
  createAreaRule(payload: AreaRulePayload): Promise<AreaRule> {
    return api.post<AreaRule>('/area-rules', payload)
  },
  updateAreaRule(id: number, payload: AreaRulePayload): Promise<AreaRule> {
    return api.put<AreaRule>(`/area-rules/${id}`, payload)
  },
  deleteAreaRule(id: number): Promise<void> {
    return api.delete<void>(`/area-rules/${id}`)
  },
  listPrintTemplates(
    logisticsId?: number,
    templateType?: string,
  ): Promise<PrintTemplate[]> {
    return api.get<PrintTemplate[]>('/print-templates', {
      params: { logistics_id: logisticsId, template_type: templateType },
    })
  },
  createPrintTemplate(
    payload: PrintTemplatePayload,
  ): Promise<PrintTemplate> {
    return api.post<PrintTemplate>('/print-templates', payload)
  },
  updatePrintTemplate(
    id: number,
    payload: PrintTemplatePayload,
  ): Promise<PrintTemplate> {
    return api.put<PrintTemplate>(`/print-templates/${id}`, payload)
  },
  deletePrintTemplate(id: number): Promise<void> {
    return api.delete<void>(`/print-templates/${id}`)
  },
}
