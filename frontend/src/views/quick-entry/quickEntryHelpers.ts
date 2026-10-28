import type { QuickEntryDraftData } from '@/api/quickEntry'

export const MAX_QUICK_ENTRY_TEXT = 4000
export const MAX_QUICK_ENTRY_ROWS = 100

const HEADER_ALIASES: Record<string, keyof QuickEntryDraftData['items'][number]> = {
  product: 'product_name',
  productname: 'product_name',
  name: 'product_name',
  商品: 'product_name',
  商品名称: 'product_name',
  货品: 'product_name',
  sku: 'sku_name',
  spec: 'sku_name',
  specification: 'sku_name',
  规格: 'sku_name',
  规格名称: 'sku_name',
  sku名称: 'sku_name',
  unit: 'unit_name',
  单位: 'unit_name',
  计量单位: 'unit_name',
  quantity: 'quantity',
  qty: 'quantity',
  数量: 'quantity',
  计划数量: 'quantity',
  采购数量: 'quantity',
  price: 'purchase_price',
  unitprice: 'purchase_price',
  purchaseprice: 'purchase_price',
  单价: 'purchase_price',
  采购价: 'purchase_price',
}

export type TableColumnField = keyof QuickEntryDraftData['items'][number] | 'supplier_name' | 'plan_date' | 'remark' | 'ignore'

const TABLE_HEADER_FIELDS: Record<string, TableColumnField> = {
  ...HEADER_ALIASES,
  supplier: 'supplier_name',
  suppliername: 'supplier_name',
  suppliercode: 'supplier_name',
  供应商: 'supplier_name',
  供应商名称: 'supplier_name',
  供应商编码: 'supplier_name',
  date: 'plan_date',
  plandate: 'plan_date',
  purchasedate: 'plan_date',
  采购日期: 'plan_date',
  计划日期: 'plan_date',
  日期: 'plan_date',
  remark: 'remark',
  remarks: 'remark',
  备注: 'remark',
}

const NATURAL_LABELS = [
  '供应商', '采购日期', '计划日期', '日期', '备注',
  '商品名称', '商品', 'SKU名称', 'SKU', '规格', '单位',
  '计划数量', '采购数量', '数量', '采购价', '单价',
]

function emptyData(): QuickEntryDraftData {
  return { supplier_name: '', plan_date: '', remark: '', items: [], errors: [], missing: [] }
}

function normalizeHeader(header: string): string {
  return header.trim().toLowerCase().replace(/[\s_\-（）()]/g, '')
}

function splitTableHeader(input: string): { headers: string[]; delimiter: string } {
  const firstLine = input.split(/\r?\n/).find((line) => line.trim()) || ''
  const delimiter = firstLine.includes('\t') ? '\t' : ','
  return { headers: parseCsvLine(firstLine, delimiter), delimiter }
}

export function getPurchasePlanTableHeaders(input: string): string[] {
  return splitTableHeader(input).headers
}

export function getDefaultPurchasePlanColumnMapping(headers: string[]): Record<number, TableColumnField> {
  return Object.fromEntries(headers.map((header, index) => [
    index,
    TABLE_HEADER_FIELDS[normalizeHeader(header)] || 'ignore',
  ]))
}

function parseCsvLine(line: string, delimiter: string): string[] {
  const cells: string[] = []
  let cell = ''
  let quoted = false
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index]
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        cell += '"'
        index += 1
      } else {
        quoted = !quoted
      }
    } else if (char === delimiter && !quoted) {
      cells.push(cell.trim())
      cell = ''
    } else {
      cell += char
    }
  }
  cells.push(cell.trim())
  return cells
}

function updateMissing(data: QuickEntryDraftData): void {
  const missing: string[] = []
  if (!data.supplier_name.trim()) missing.push('供应商')
  if (!data.plan_date.trim()) missing.push('采购日期')
  if (!data.items.length) missing.push('至少一条商品明细')
  data.items.forEach((item, index) => {
    if (!item.product_name.trim()) missing.push(`第 ${index + 1} 行商品`)
    if (!item.quantity.trim() || !Number.isFinite(Number(item.quantity)) || Number(item.quantity) <= 0) {
      missing.push(`第 ${index + 1} 行有效数量`)
    }
  })
  data.missing = missing
}

export function parsePurchasePlanTable(
  input: string,
  columnMapping: Record<number, TableColumnField> = {},
): QuickEntryDraftData {
  const data = emptyData()
  if (input.length > MAX_QUICK_ENTRY_TEXT) {
    data.errors.push({ message: `粘贴内容不能超过 ${MAX_QUICK_ENTRY_TEXT} 个字符` })
    return data
  }
  const lines = input.split(/\r?\n/).filter((line) => line.trim())
  if (lines.length < 2) {
    data.errors.push({ message: '请粘贴包含表头和至少一行明细的表格' })
    return data
  }
  if (lines.length - 1 > MAX_QUICK_ENTRY_ROWS) {
    data.errors.push({ message: `最多支持 ${MAX_QUICK_ENTRY_ROWS} 行明细` })
    return data
  }
  const { headers: headerLabels, delimiter } = splitTableHeader(lines[0])
  const headers = headerLabels.map(normalizeHeader)
  const fieldByColumn = headers.map((header, index) => columnMapping[index] ?? TABLE_HEADER_FIELDS[header] ?? 'ignore')
  if (!fieldByColumn.includes('product_name') || !fieldByColumn.includes('quantity')) {
    data.errors.push({ message: '表头必须包含“商品名称”和“数量”列' })
    return data
  }
  for (const [lineIndex, line] of lines.slice(1).entries()) {
    const cells = parseCsvLine(line, delimiter)
    const row: QuickEntryDraftData['items'][number] = {
      product_name: '', sku_name: '', unit_name: '', quantity: '', purchase_price: '',
    }
    cells.forEach((value, column) => {
      const field = fieldByColumn[column]
      if (field === 'supplier_name' && value) {
        if (!data.supplier_name) data.supplier_name = value
        else if (data.supplier_name !== value) data.errors.push({ row: lineIndex + 2, message: '同一张草稿出现多个供应商，请拆分录入' })
      }
      if (field === 'plan_date' && value) {
        if (!data.plan_date) data.plan_date = value
        else if (data.plan_date !== value) data.errors.push({ row: lineIndex + 2, message: '同一张草稿出现多个采购日期，请拆分录入' })
      }
      if (field === 'remark' && value && !data.remark) data.remark = value
      if (field !== 'ignore' && field !== 'supplier_name' && field !== 'plan_date' && field !== 'remark') {
        row[field] = value
      }
    })
    if (!row.product_name && !row.quantity) continue
    data.items.push(row)
    if (!row.product_name) data.errors.push({ row: lineIndex + 2, message: '缺少商品名称' })
    if (!row.quantity || !Number.isFinite(Number(row.quantity)) || Number(row.quantity) <= 0) {
      data.errors.push({ row: lineIndex + 2, message: '数量必须为大于 0 的数字' })
    }
  }
  updateMissing(data)
  return data
}

export function parsePurchasePlanNatural(input: string): QuickEntryDraftData {
  const data = emptyData()
  if (input.length > MAX_QUICK_ENTRY_TEXT) {
    data.errors.push({ message: `输入内容不能超过 ${MAX_QUICK_ENTRY_TEXT} 个字符` })
    return data
  }
  const labels = NATURAL_LABELS.map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  const expression = new RegExp(`(?:^|[，,；;\\n\\s])(${labels})\\s*[:：=]\\s*([^，,；;\\n]+)`, 'g')
  const item: QuickEntryDraftData['items'][number] = {
    product_name: '', sku_name: '', unit_name: '', quantity: '', purchase_price: '',
  }
  const entries = [...input.matchAll(expression)]
  for (const match of entries) {
    const label = match[1]
    const value = match[2].trim().replace(/[。.]$/, '')
    if (!value) continue
    if (label === '供应商') data.supplier_name = value
    else if (['采购日期', '计划日期', '日期'].includes(label)) data.plan_date = value
    else if (label === '备注') data.remark = value
    else if (['商品', '商品名称'].includes(label)) {
      if (item.product_name) {
        data.items.push({ ...item })
        item.product_name = ''
        item.sku_name = ''
        item.unit_name = ''
        item.quantity = ''
        item.purchase_price = ''
      }
      item.product_name = value
    } else if (['SKU', 'SKU名称', '规格'].includes(label)) item.sku_name = value
    else if (label === '单位') item.unit_name = value
    else if (['计划数量', '采购数量', '数量'].includes(label)) item.quantity = value
    else if (['采购价', '单价'].includes(label)) item.purchase_price = value
  }
  if (item.product_name || item.quantity || item.sku_name) data.items.push(item)
  if (!entries.length) data.errors.push({ message: '未识别到带标签的字段，请按“供应商：… 商品：… 数量：…”格式输入' })
  data.items.forEach((row, index) => {
    if (!row.product_name) data.errors.push({ row: index + 1, message: '缺少商品名称' })
    if (!row.quantity || !Number.isFinite(Number(row.quantity)) || Number(row.quantity) <= 0) {
      data.errors.push({ row: index + 1, message: '数量必须为大于 0 的数字' })
    }
  })
  updateMissing(data)
  return data
}

export function validatePurchasePlanDraft(data: QuickEntryDraftData): string[] {
  updateMissing(data)
  const errors = [...data.errors.map((item) => item.message), ...data.missing]
  if (data.plan_date) {
    const parsedDate = new Date(`${data.plan_date}T00:00:00Z`)
    if (!/^\d{4}-\d{2}-\d{2}$/.test(data.plan_date) || Number.isNaN(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== data.plan_date) {
      errors.push('采购日期格式应为有效的 YYYY-MM-DD')
    }
  }
  data.items.forEach((item, index) => {
    if (item.purchase_price && (!Number.isFinite(Number(item.purchase_price)) || Number(item.purchase_price) < 0)) {
      errors.push(`第 ${index + 1} 行采购价必须为非负数字`)
    }
  })
  return [...new Set(errors)]
}
