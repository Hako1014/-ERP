import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AssistantPurchasePlanDraft } from '@/api/assistant'
import { purchasePlanApi } from '@/api/purchase'
import PurchasePlanFormDialog from '@/views/purchase/components/PurchasePlanFormDialog.vue'

const needs = vi.spyOn(purchasePlanApi, 'needs').mockResolvedValue({
  total: 1,
  page: 1,
  page_size: 200,
  items_count: 1,
  sales_window_days: 20,
  items: [{
    product_id: 1,
    product_name: '商品一',
    product_code: 'P-001',
    sku_id: null,
    supplier_id: 1,
    purchase_price: '10.00',
    sales_20d: '0',
    current_stock: '0',
    in_transit_stock: '0',
    in_progress_qty: '0',
    suggested_qty: '0',
  }],
})

function draft(overrides: Partial<AssistantPurchasePlanDraft> = {}): AssistantPurchasePlanDraft {
  return {
    draft_id: 'draft-one',
    version: 1,
    doc_type: 'purchase_plan',
    source_kind: 'natural',
    status: 'ready',
    ready: true,
    supplier: { status: 'recognized', value: { id: 1, name: '供应商一' } },
    plan_date: { status: 'recognized', value: '2026-10-11T09:00:00' },
    remark: { status: 'user_edited', value: '先到先用' },
    items: [{
      row: 1,
      product: { status: 'recognized', value: { id: 1, code: 'P-001', name: '商品一' } },
      sku: { status: 'optional', value: null },
      unit: { status: 'recognized', value: { id: 1, name: '个' } },
      quantity: { status: 'recognized', value: '3' },
      purchase_price: { status: 'optional', value: '12.50' },
      calculation: { unit_price: '12.50', amount: '37.50', source: '用户输入' },
    }],
    errors: [],
    missing: [],
    edited_fields: [],
    diff: [],
    estimated_total: '37.50',
    estimate_complete: true,
    expires_at: '2026-10-11T17:00:00+08:00',
    payload: {
      supplier_id: 1,
      plan_date: '2026-10-11T09:00:00',
      remark: '先到先用',
      items: [{ product_id: 1, sku_id: null, planned_qty: '3', purchase_price: '12.50' }],
    },
    ...overrides,
  } as AssistantPurchasePlanDraft
}

interface ExposedForm {
  open(supplier?: number): void
  applyAssistantDraft(value: AssistantPurchasePlanDraft, mode: 'merge' | 'replace'): Promise<void>
  getFormSnapshot(): {
    supplier_id: number | null
    plan_date: string
    remark: string
    items: Array<{ product_id: number; sku_id: number | null; planned_qty: string; purchase_price: string }>
  }
}

afterEach(() => vi.clearAllMocks())

describe('采购计划 AI 草稿表单适配', () => {
  it('替换模式预填草稿字段与明细，合并模式保留已有值并补入新商品', async () => {
    const wrapper = mount(PurchasePlanFormDialog, {
      props: { supplierOptions: [] },
      global: { plugins: [ElementPlus] },
    })
    const form = wrapper.vm as unknown as ExposedForm
    form.open()
    await form.applyAssistantDraft(draft(), 'replace')

    const second = draft({
      draft_id: 'draft-two',
      remark: { status: 'user_edited', value: '新备注' },
      payload: {
        supplier_id: 2,
        plan_date: '2026-10-12T09:00:00',
        remark: '新备注',
        items: [
          { product_id: 1, sku_id: null, planned_qty: '9', purchase_price: '20.00' },
          { product_id: 2, sku_id: null, planned_qty: '5', purchase_price: null },
        ],
      },
    })
    await form.applyAssistantDraft(second, 'merge')

    expect(form.getFormSnapshot()).toEqual({
      supplier_id: 1,
      plan_date: '2026-10-11T09:00:00',
      remark: '先到先用',
      items: [
        { product_id: 1, sku_id: null, planned_qty: '3', purchase_price: '12.50' },
        { product_id: 2, sku_id: null, planned_qty: '5', purchase_price: '' },
      ],
    })
    expect(needs).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
