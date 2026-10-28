import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useAssistantPurchasePlanDraftStore } from '@/stores/assistantPurchasePlanDraft'
import type { AssistantPurchasePlanDraft } from '@/api/assistant'

const sampleDraft = {
  draft_id: 'draft-1',
  version: 3,
  doc_type: 'purchase_plan',
  source_kind: 'natural',
  status: 'ready',
  ready: true,
  supplier: { status: 'recognized', value: { id: 7, name: '供应商' } },
  plan_date: { status: 'recognized', value: '2026-10-10T10:00:00' },
  remark: { status: 'optional', value: null },
  items: [],
  errors: [],
  missing: [],
  edited_fields: [],
  diff: [],
  estimated_total: '0.00',
  estimate_complete: true,
  expires_at: '2026-10-10T18:00:00+08:00',
  payload: {
    supplier_id: 7,
    plan_date: '2026-10-10T10:00:00',
    remark: null,
    items: [{ product_id: 9, sku_id: null, planned_qty: '2', purchase_price: null }],
  },
} as AssistantPurchasePlanDraft

describe('assistant purchase-plan one-shot draft slot', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('delivers a draft once to the matching login identity and never to another account', () => {
    const store = useAssistantPurchasePlanDraftStore()
    store.queue('staff-1\u0000token-a', sampleDraft)

    expect(store.consume('staff-2\u0000token-b')).toBeNull()
    expect(store.consume('staff-1\u0000token-a')).toBeNull()

    store.queue('staff-1\u0000token-a', sampleDraft)
    expect(store.consume('staff-1\u0000token-a')?.payload?.supplier_id).toBe(7)
    expect(store.consume('staff-1\u0000token-a')).toBeNull()
  })
})
