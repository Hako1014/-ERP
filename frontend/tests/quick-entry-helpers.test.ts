import { describe, expect, it } from 'vitest'

import {
  getDefaultPurchasePlanColumnMapping,
  parsePurchasePlanNatural,
  parsePurchasePlanTable,
  validatePurchasePlanDraft,
} from '@/views/quick-entry/quickEntryHelpers'

describe('non-AI quick entry parsing', () => {
  it('parses explicitly labeled purchase-plan fields without guessing', () => {
    const draft = parsePurchasePlanNatural(
      '供应商：华东供应商，采购日期：2026-10-15，商品：蓝色收纳箱，规格：L，数量：24，单价：36.50，备注：急需',
    )

    expect(draft.supplier_name).toBe('华东供应商')
    expect(draft.plan_date).toBe('2026-10-15')
    expect(draft.items).toHaveLength(1)
    expect(draft.items[0]).toMatchObject({
      product_name: '蓝色收纳箱', sku_name: 'L', quantity: '24', purchase_price: '36.50',
    })
    expect(draft.remark).toBe('急需')
    expect(draft.missing).toEqual([])
  })

  it('leaves unlabeled or missing values unresolved', () => {
    const draft = parsePurchasePlanNatural('想买一些蓝色收纳箱')
    expect(draft.items).toEqual([])
    expect(draft.missing).toContain('供应商')
    expect(draft.missing).toContain('采购日期')
  })

  it('maps pasted tabular headers and reports invalid rows', () => {
    const draft = parsePurchasePlanTable('商品名称\t规格\t数量\t采购价\n收纳箱\tL\t2\t36.5\n纸箱\tM\t0\t')
    expect(draft.items).toHaveLength(2)
    expect(draft.items[0]).toMatchObject({ product_name: '收纳箱', sku_name: 'L', quantity: '2' })
    expect(draft.errors).toContainEqual({ row: 3, message: '数量必须为大于 0 的数字' })
  })

  it('supports explicit mapping for unfamiliar headers and summary columns', () => {
    const input = '货品描述\t件数\t供货方\t下单日\n收纳箱\t3\t华东供应商\t2026-10-15'
    const headers = ['货品描述', '件数', '供货方', '下单日']
    const mapping = {
      ...getDefaultPurchasePlanColumnMapping(headers),
      0: 'product_name' as const,
      1: 'quantity' as const,
      2: 'supplier_name' as const,
      3: 'plan_date' as const,
    }
    const draft = parsePurchasePlanTable(input, mapping)

    expect(draft.items[0]).toMatchObject({ product_name: '收纳箱', quantity: '3' })
    expect(draft.supplier_name).toBe('华东供应商')
    expect(draft.plan_date).toBe('2026-10-15')
    expect(draft.errors).toEqual([])
  })

  it('validates date and price explicitly before import', () => {
    const draft = parsePurchasePlanTable('商品名称,数量,采购价\n收纳箱,2,-1')
    draft.supplier_name = '供应商'
    draft.plan_date = '15/10/2026'
    expect(validatePurchasePlanDraft(draft)).toContain('采购日期格式应为有效的 YYYY-MM-DD')
    expect(validatePurchasePlanDraft(draft)).toContain('第 1 行采购价必须为非负数字')
  })
})
