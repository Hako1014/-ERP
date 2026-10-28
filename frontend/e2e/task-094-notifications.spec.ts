import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

test.use({ trace: 'off', screenshot: 'off', video: 'off' })

const manifestPath = process.env.TASK094_MANIFEST
const password = process.env.TASK094_PASSWORD
const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'
const backend = resolve(process.cwd(), '..', 'backend')
const data = manifestPath ? JSON.parse(readFileSync(manifestPath, 'utf8')) : null

async function call(request: APIRequestContext, token: string, path: string, method = 'GET', body?: object) {
  const response = await request.fetch(`${api}${path}`, {
    method, headers: { Authorization: `Bearer ${token}` }, data: body,
  })
  expect(response.ok(), `${method} ${path} HTTP ${response.status()}`).toBeTruthy()
  const envelope = await response.json()
  expect(envelope.code).toBe('0')
  return envelope.data
}

async function login(page: Page, role: string) {
  await page.goto('/login')
  await page.locator('input[name="account"]').fill(data.staff[role].account)
  await page.locator('input[name="password"]').fill(password!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login$/)
  await page.goto('/notifications')
  await expect(page.getByRole('heading', { name: '消息中心', exact: true })).toBeVisible()
}

function control(action: string) {
  return JSON.parse(execFileSync(resolve(backend, '.venv/Scripts/python.exe'),
    ['scripts/task094_controls.py', manifestPath!, action],
    { cwd: backend, encoding: 'utf8', env: { ...process.env, PYTHONPATH: backend } }).trim())
}

test('TASK-094：真实 MySQL/Redis/worker/beat、审批、消息、多标签页、离线与账号隔离', async ({ browser, request }) => {
  test.setTimeout(240_000)
  if (!data || !password || !data.marker.startsWith('TASK094-')) throw new Error('必须指定 TASK094_MANIFEST 与本地测试密码')
  const tokens: Record<string, string> = {}
  for (const role of Object.keys(data.staff)) {
    const response = await request.post(`${api}/auth/login`, { data: { account: data.staff[role].account, password } })
    expect(response.ok()).toBeTruthy()
    tokens[role] = (await response.json()).data.token
    const items = (await call(request, tokens[role], '/notifications')).items
    if (['OTHER', 'AFTER_SALE', 'WAREHOUSE'].includes(role)) expect(items).toHaveLength(0)
    for (const item of items) expect(item.body).toContain('TASK094-')
  }

  const context = await browser.newContext()
  const page = await context.newPage()
  const tab = await context.newPage()
  try {
    await login(page, 'VP')
    await tab.goto('/notifications')
    await expect(tab.getByRole('heading', { name: '消息中心', exact: true })).toBeVisible()
    const approvalState = await call(request, tokens.PURCHASER, `/approval/records/purchase_plan/${data.plans.approve.id}`)
    if (approvalState.state.status === 'NOT_SUBMITTED') {
      await call(request, tokens.PURCHASER, `/approval/records/purchase_plan/${data.plans.approve.id}/submit`, 'POST', { doc_no: data.plans.approve.no })
    }
    const row = page.locator('.notification-row').filter({ hasText: data.plans.approve.no }).filter({ hasText: '有新的待审批单据' }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    const otherRow = tab.locator('.notification-row').filter({ hasText: data.plans.approve.no }).filter({ hasText: '有新的待审批单据' }).first()
    await expect(otherRow).toBeVisible({ timeout: 20_000 })
    const initial = (await call(request, tokens.VP, '/notifications')).items.find((item: any) => item.event_code === 'approval.task_assigned' && item.target.id === data.plans.approve.id)
    expect(initial.read_state).toBe('UNREAD')
    expect(initial.business_state).toBe('OPEN')
    // Toast 不是送达依据；收件箱、未读和业务状态分别断言。
    await row.click()
    await page.getByRole('button', { name: '标记已读', exact: true }).click()
    await expect.poll(async () => (await call(request, tokens.VP, `/notifications/${initial.id}`)).read_state).toBe('READ')
    await expect(otherRow).toHaveAttribute('aria-label', /已读$/)
    const toastCount = await page.locator('.el-notification').filter({ hasText: '有新的待审批单据' }).filter({ hasText: data.plans.approve.no }).count()
      + await tab.locator('.el-notification').filter({ hasText: '有新的待审批单据' }).filter({ hasText: data.plans.approve.no }).count()
    expect(toastCount).toBeLessThanOrEqual(1)
    for (const role of ['PURCHASER', 'OTHER', 'AFTER_SALE', 'WAREHOUSE']) {
      const headers = { Authorization: `Bearer ${tokens[role]}` }
      expect((await request.get(`${api}/notifications/${initial.id}`, { headers })).status()).toBe(404)
      expect((await request.post(`${api}/notifications/${initial.id}/read`, { headers })).status()).toBe(404)
      expect((await request.get(`${api}/notifications?cursor=${encodeURIComponent(initial.cursor)}`, { headers })).status()).toBe(400)
    }
    const stored = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('erp_notification_toasts')))
    for (const [, value] of stored) expect(value).not.toContain(data.plans.approve.no)

    await call(request, tokens.VP, `/approval/records/purchase_plan/${data.plans.approve.id}/approve`, 'POST', { comment: 'TASK094 初审' })
    await expect.poll(async () => (await call(request, tokens.FINANCE, '/notifications')).items.some((item: any) => item.target.id === data.plans.approve.id), { timeout: 30_000 }).toBe(true)
    await call(request, tokens.FINANCE, `/approval/records/purchase_plan/${data.plans.approve.id}/approve`, 'POST', { comment: 'TASK094 复审' })
    await expect.poll(async () => (await call(request, tokens.PURCHASER, '/notifications')).items.some((item: any) => item.event_code === 'approval.approved' && item.target.id === data.plans.approve.id), { timeout: 30_000 }).toBe(true)
    expect((await call(request, tokens.VP, '/notifications')).items.some((item: any) => item.target.id === data.plans.approve.id)).toBe(false)

    await call(request, tokens.PURCHASER, `/approval/records/purchase_plan/${data.plans.reject.id}/submit`, 'POST', { doc_no: data.plans.reject.no })
    await call(request, tokens.VP, `/approval/records/purchase_plan/${data.plans.reject.id}/reject`, 'POST', { comment: 'TASK094 驳回' })
    await expect.poll(async () => (await call(request, tokens.PURCHASER, '/notifications')).items.some((item: any) => item.event_code === 'approval.rejected' && item.target.id === data.plans.reject.id), { timeout: 30_000 }).toBe(true)

    // 页面离线期间依靠持久化 Inbox，恢复后不依赖 Redis 是否仍保存实时信号。
    await page.getByRole('button', { name: 'Close this dialog' }).click().catch(() => {})
    await context.setOffline(true)
    await call(request, tokens.PURCHASER, `/approval/records/purchase_plan/${data.plans.offline.id}/submit`, 'POST', { doc_no: data.plans.offline.no })
    await expect.poll(async () => (await call(request, tokens.VP, '/notifications')).items.some((item: any) => item.target.id === data.plans.offline.id), { timeout: 30_000 }).toBe(true)
    await context.setOffline(false)
    await page.reload()
    await expect(page.locator('.notification-row').filter({ hasText: data.plans.offline.no }).first()).toBeVisible({ timeout: 30_000 })
    await expect.poll(async () => (await call(request, tokens.VP, '/notifications')).items.some((item: any) => item.event_code === 'approval.timeout' && item.target.id === data.plans.offline.id), { timeout: 30_000 }).toBe(true)

    // 已审批入库夹具产生真实到货事件；不冒充完整入库业务链。
    expect(control('arrival').arrival_enqueued).toBe(true)
    await expect.poll(async () => (await call(request, tokens.PURCHASER, '/notifications')).items.some((item: any) => item.event_code === 'purchase_order.arrival_progress_changed'), { timeout: 30_000 }).toBe(true)
    expect((await call(request, tokens.OTHER, '/notifications/unread-count')).count).toBe(0)
    expect((await call(request, tokens.WAREHOUSE, '/notifications/unread-count')).count).toBe(0)
    expect((await call(request, tokens.AFTER_SALE, '/notifications/unread-count')).count).toBe(0)

    control('disable-vp')
    expect((await call(request, tokens.VP, '/notifications')).items).toHaveLength(0)
    expect((await request.get(`${api}/notifications/${initial.id}`, { headers: { Authorization: `Bearer ${tokens.VP}` } })).status()).toBe(404)
    control('restore-vp')

    // 显式退出并切换到无关角色，旧正文/未读不能留在页面。
    await page.locator('.app-header__user').click()
    await page.getByText('退出登录', { exact: true }).click()
    await expect(page).toHaveURL(/\/login$/)
    await login(page, 'AFTER_SALE')
    await expect(page.locator('.notification-row')).toHaveCount(0)
    await expect(page.locator('.el-notification')).toHaveCount(0)
    expect(control('report').unique_recipients).toBe(true)
    console.log('TASK094 浏览器闭环通过；凭据与令牌未写入测试报告。')
  } finally {
    await context.setOffline(false)
    control('restore-vp')
    await context.close()
  }
})
