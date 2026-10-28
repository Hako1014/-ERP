import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

test.use({ trace: 'off', screenshot: 'off', video: 'off' })

const appBaseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5273'
const apiBaseURL = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

interface PlanList {
  total: number
  items: Array<Record<string, unknown>>
}

const credentialsPath = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  'docs',
  '角色账号',
  '采购员.md',
)

function credentials(): { account: string; password: string } {
  const content = readFileSync(credentialsPath, 'utf8')
  const account = content.match(/^- 登录账号：`([^`]+)`$/m)?.[1]
  const password = content.match(/^- 登录密码：`([^`]+)`$/m)?.[1]
  if (!account || !password) throw new Error('采购员本地测试账号文件缺少登录字段')
  return { account, password }
}

async function loginAsPurchaser(page: Page): Promise<void> {
  await page.goto('/login')
  await page.locator('input[name="account"]').fill(credentials().account)
  await page.locator('input[name="password"]').fill(credentials().password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(new URL('/purchase/plans', appBaseURL).toString())
  await expect(page.getByRole('button', { name: '打开 AI 助手' })).toBeVisible({
    timeout: 20_000,
  })
}

async function openQuickEntry(page: Page) {
  const assistantPanel = page.getByRole('dialog', { name: 'AI 助手' })
  if (!(await assistantPanel.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: '打开 AI 助手' }).click()
  }
  await assistantPanel.getByRole('button', { name: '快捷录入采购计划' }).click()
  const entry = page.getByRole('dialog', { name: /AI 快捷录入.*采购计划/ })
  await expect(entry).toBeVisible()
  return { assistantPanel, entry }
}

async function planList(page: Page, token: string): Promise<PlanList> {
  const response = await page.request.get(
    new URL('/purchase-plans?page=1&page_size=200', apiBaseURL).toString(),
    { headers: { Authorization: `Bearer ${token}` } },
  )
  expect(response.ok()).toBeTruthy()
  const envelope = await response.json()
  expect(envelope.code).toBe('0')
  return envelope.data as PlanList
}

test('TASK-088：表格消歧、手工编辑、表单确认保存、DeepSeek 自然语言与幂等重放', async ({ page }) => {
  test.setTimeout(240_000)

  await loginAsPurchaser(page)
  const token = await page.evaluate(() => localStorage.getItem('erp_access_token'))
  expect(token).toBeTruthy()
  const initialPlans = await planList(page, token!)

  const { entry } = await openQuickEntry(page)
  await entry.getByText('粘贴表格', { exact: true }).click()
  const input = entry.getByRole('textbox', { name: '采购计划原始内容' })
  await input.fill(
    [
      '供应商编码\t供应商名称\t商品名称\t单位\t数量\t采购价',
      'TASK088-SUP-001\tTASK-088 测试供应商\tTASK-088 同名测试商品\t个\t2\t3.45',
    ].join('\n'),
  )
  await entry.getByRole('button', { name: '解析为临时草稿' }).click()
  await expect(entry.locator('.assistant-draft-entry__preview')).toBeVisible()
  await expect(entry.locator('.assistant-draft-entry__preview')).toContainText('需选择')
  await expect(entry.getByText('计划日期待补', { exact: true })).toBeVisible()
  await expect(entry.getByRole('button', { name: '填入采购计划表单' })).toHaveCount(0)

  // Cancelling an incomplete draft must not create a purchase plan.
  await entry.getByRole('button', { name: '取消草稿' }).click()
  const beforeNaturalParse = await planList(page, token!)
  expect(beforeNaturalParse.total).toBe(initialPlans.total)

  const marker = process.env.TASK088_TEST_MARKER || `TASK-088-E2E-${Date.now()}`
  await input.fill(
    [
      '供应商编码\t供应商名称\t计划日期\t商品名称\t单位\t数量\t采购价',
      `TASK088-SUP-001\tTASK-088 测试供应商\t2026-11-10\tTASK-088 同名测试商品\t个\t2\t3.45`,
    ].join('\n'),
  )
  await entry.getByRole('button', { name: '解析为临时草稿' }).click()
  await expect(entry.locator('.assistant-draft-entry__preview')).toBeVisible()
  const productSelect = entry
    .locator('.assistant-draft-entry__preview .el-table__body-wrapper .el-select')
    .first()
  await productSelect.click()
  await page.getByRole('option', { name: /TASK088-P-001/ }).click()
  await expect(entry.getByRole('button', { name: '填入采购计划表单' })).toBeEnabled({
    timeout: 20_000,
  })

  console.log(`TASK088_TEST_MARKER=${marker}`)

  await entry.getByRole('button', { name: '填入采购计划表单' }).click()
  const form = page.getByRole('dialog', { name: '新建采购计划' })
  await expect(form).toBeVisible()
  await expect(form).toContainText('此表单已由 AI 临时草稿预填')
  await expect(form).toContainText('TASK-088 同名测试商品')
  await expect(form.getByRole('button', { name: '生成计划', exact: true })).toBeVisible()
  await form
    .locator('.el-form-item')
    .filter({ hasText: '备注' })
    .locator('textarea')
    .fill(marker)

  const createResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith('/purchase-plans') &&
    response.request().method() === 'POST',
  )
  await form.getByRole('button', { name: '生成计划', exact: true }).click()
  const createResponse = await createResponsePromise
  expect(createResponse.ok()).toBeTruthy()
  const request = createResponse.request()
  const requestHeaders = request.headers()
  const requestBody = request.postDataJSON()
  const idempotencyKey = requestHeaders['idempotency-key']
  const assistantDraftId = requestHeaders['x-assistant-draft-id']
  expect(idempotencyKey).toBeTruthy()
  expect(assistantDraftId).toBeTruthy()

  const createdEnvelope = await createResponse.json()
  expect(createdEnvelope.code).toBe('0')
  const created = createdEnvelope.data
  expect(created.plan_no).toMatch(/^CGJH\d{8}-\d+$/)
  expect(created.status).toBe('IN_PROGRESS')
  expect(created.audit_status).toBe('PENDING')
  expect(created.remark).toBe(marker)
  expect(created.items).toHaveLength(1)
  expect(Number(created.items[0].planned_qty)).toBe(2)
  expect(Number(created.items[0].purchase_price)).toBe(3.45)
  expect(Number(created.items[0].amount)).toBe(6.9)

  const replay = await page.request.post(
    new URL('/purchase-plans', apiBaseURL).toString(),
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'Idempotency-Key': idempotencyKey,
        'X-Assistant-Draft-Id': assistantDraftId,
      },
      data: requestBody,
    },
  )
  expect(replay.ok()).toBeTruthy()
  const replayEnvelope = await replay.json()
  expect(replayEnvelope.code).toBe('0')
  expect(replayEnvelope.data.id).toBe(created.id)
  expect(replayEnvelope.data.plan_no).toBe(created.plan_no)

  const persistedList = await planList(page, token!)
  expect(persistedList.total).toBe(initialPlans.total + 1)
  expect(persistedList.items).toHaveLength(initialPlans.total + 1)

  const detailDrawer = page.getByRole('dialog', { name: '采购计划详情' })
  if (await detailDrawer.isVisible().catch(() => false)) {
    await detailDrawer.getByRole('button', { name: 'Close this dialog' }).click()
    await expect(detailDrawer).toBeHidden()
  }

  // Verify a genuine provider parse is also draft-only until the user confirms.
  const { entry: naturalEntry } = await openQuickEntry(page)
  const cancelExistingDraft = naturalEntry.getByRole('button', { name: '取消草稿' })
  await expect(cancelExistingDraft).toBeVisible({ timeout: 20_000 })
  await cancelExistingDraft.click()
  await naturalEntry.getByText('自然语言', { exact: true }).click()
  let naturalParseRequestId: string | undefined
  page.on('request', (observed) => {
    if (observed.url().endsWith('/assistant/drafts/parse')) {
      naturalParseRequestId = observed.postDataJSON()?.request_id
    }
  })
  await naturalEntry.getByRole('textbox', { name: '采购计划原始内容' }).fill(
    '请创建采购计划草稿：供应商编码 TASK088-SUP-001，计划日期 2026-11-11，' +
      '商品编码 TASK088-P-001，采购 3 个，采购价 4.20 元。只录入采购计划。',
  )
  await naturalEntry.getByRole('button', { name: '解析为临时草稿' }).click()
  await expect(naturalEntry.locator('.assistant-draft-entry__preview')).toBeVisible({
    timeout: 90_000,
  })
  await expect(naturalEntry.getByRole('button', { name: '填入采购计划表单' })).toBeEnabled({
    timeout: 90_000,
  })
  expect(naturalParseRequestId).toBeTruthy()
  const usageResponse = await page.request.get(
    new URL('/assistant/usage/me?days=1', apiBaseURL).toString(),
    { headers: { Authorization: `Bearer ${token}` } },
  )
  expect(usageResponse.ok()).toBeTruthy()
  const usageEnvelope = await usageResponse.json()
  expect(usageEnvelope.data.items).toContainEqual(
    expect.objectContaining({
      request_id: naturalParseRequestId,
      provider: 'deepseek',
      status: 'completed',
    }),
  )
  expect(await planList(page, token!)).toMatchObject({ total: initialPlans.total + 1 })
  console.log(`TASK088_PLAN_NO=${created.plan_no}`)
  await naturalEntry.getByRole('button', { name: '取消草稿' }).click()
  expect(await planList(page, token!)).toMatchObject({ total: initialPlans.total + 1 })
})
