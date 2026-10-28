import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Locator, type Page } from '@playwright/test'

test.use({ trace: 'off', screenshot: 'off', video: 'off' })

const appBaseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5273'
const apiBaseURL = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

const credentialsDirectory = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  'docs',
  '角色账号',
)

const roleAccounts = [
  { role: '系统管理员', file: '系统管理员.md', path: '/dashboard', canCreatePurchasePlan: true },
  { role: '分管副总', file: '分管副总.md', path: '/approval/mine', canCreatePurchasePlan: false },
  { role: '财务', file: '财务.md', path: '/finance/vouchers', canCreatePurchasePlan: false },
  { role: '采购员', file: '采购员.md', path: '/purchase/plans', canCreatePurchasePlan: true },
  { role: '仓库人员', file: '仓库人员.md', path: '/stock/query', canCreatePurchasePlan: false },
  { role: '商控/质检', file: '商控质检.md', path: '/purchase/orders', canCreatePurchasePlan: false },
  { role: '售后/客服', file: '售后客服.md', path: '/orders/returns', canCreatePurchasePlan: false },
] as const

interface Credentials {
  account: string
  password: string
}

function readCredentials(file: string, role: string): Credentials {
  const content = readFileSync(resolve(credentialsDirectory, file), 'utf8')
  const account = content.match(/^- 登录账号：`([^`]+)`$/m)?.[1]
  const password = content.match(/^- 登录密码：`([^`]+)`$/m)?.[1]
  if (!account || !password) {
    throw new Error(`本地角色账号文件缺少登录字段：${role}`)
  }
  return { account, password }
}

async function login(
  page: Page,
  credentials: Credentials,
  expectedPath: string,
): Promise<void> {
  await page.goto('/login')
  await page.locator('input[name="account"]').fill(credentials.account)
  await page.locator('input[name="password"]').fill(credentials.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(new URL(expectedPath, appBaseURL).toString())
  await expect(page.getByRole('button', { name: '打开 AI 助手' })).toBeVisible({
    timeout: 20_000,
  })
}

async function openAssistant(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: '打开 AI 助手' }).click()
  const panel = page.getByRole('dialog', { name: 'AI 助手' })
  await expect(panel).toBeVisible()
  await expect(panel.locator('.assistant-float__scene')).toContainText(' · ')
  await expect(panel.locator('.assistant-float__suggestion').first()).toBeVisible()
  return panel
}

for (const { role, file, path, canCreatePurchasePlan } of roleAccounts) {
  test(`${role}：助手入口、页面上下文、拖动位置恢复和小屏布局`, async ({ page }) => {
    const credentials = readCredentials(file, role)
    await login(page, credentials, path)
    const panel = await openAssistant(page)

    const draftEntry = panel.getByRole('button', { name: '快捷录入采购计划', exact: true })
    if (canCreatePurchasePlan) {
      await expect(draftEntry).toBeVisible()
    } else {
      await expect(draftEntry).toHaveCount(0)
    }

    const before = await panel.boundingBox()
    const header = await panel.locator('.assistant-float__header').boundingBox()
    expect(before).not.toBeNull()
    expect(header).not.toBeNull()

    await page.mouse.move(header!.x + 24, header!.y + header!.height / 2)
    await page.mouse.down()
    await page.mouse.move(header!.x - 66, header!.y + header!.height / 2 - 48, {
      steps: 5,
    })
    await page.mouse.up()

    const moved = await panel.boundingBox()
    expect(moved).not.toBeNull()
    expect(Math.abs(moved!.x - before!.x) + Math.abs(moved!.y - before!.y)).toBeGreaterThan(40)

    const savedPosition = await page.evaluate(() => {
      const key = Object.keys(localStorage).find((item) =>
        item.startsWith('erp_assistant_position_'),
      )
      return key ? JSON.parse(localStorage.getItem(key) || 'null') : null
    })
    expect(savedPosition).toEqual({ x: Math.round(moved!.x), y: Math.round(moved!.y) })

    await panel.getByRole('button', { name: '收起助手' }).click()
    const reopened = await openAssistant(page)
    const restored = await reopened.boundingBox()
    expect(restored).not.toBeNull()
    expect(Math.abs(restored!.x - moved!.x) + Math.abs(restored!.y - moved!.y)).toBeLessThanOrEqual(2)

    await page.setViewportSize({ width: 390, height: 844 })
    await expect(reopened).toHaveClass(/is-compact/)
    const mobileBounds = await reopened.boundingBox()
    expect(mobileBounds).not.toBeNull()
    expect(mobileBounds!.x).toBeGreaterThanOrEqual(0)
    expect(mobileBounds!.x + mobileBounds!.width).toBeLessThanOrEqual(390)
  })
}

test('真实 DeepSeek 流式、授权指标来源、停止、异常处理和账号切换隔离', async ({ page }) => {
  test.setTimeout(240_000)

  await login(page, readCredentials('系统管理员.md', '系统管理员'), '/dashboard')
  const panel = await openAssistant(page)
  let capturedContextPath: string | undefined
  page.on('request', (request) => {
    if (request.url().endsWith('/assistant/messages/stream')) {
      const body = request.postDataJSON() as {
        page_context?: { path?: string }
      } | null
      capturedContextPath = body?.page_context?.path
    }
  })

  const input = panel.getByRole('textbox', { name: '输入问题' })
  await input.fill('你好，请用一句简短的话介绍你能提供什么帮助，不要查询 ERP 数据。')
  await panel.getByRole('button', { name: '发送', exact: true }).click()
  const answers = panel.locator('.assistant-float__message.is-assistant p')
  await expect(answers.last()).toContainText(/\S+/, { timeout: 90_000 })
  await expect(panel.getByRole('button', { name: '发送', exact: true })).toHaveText('发送', {
    timeout: 90_000,
  })
  expect(capturedContextPath).toBe('/dashboard')

  let metricRequestId: string | undefined
  page.on('request', (request) => {
    if (request.url().endsWith('/assistant/messages/stream')) {
      metricRequestId = (request.postDataJSON() as { request_id?: string } | null)
        ?.request_id
    }
  })
  await input.fill(
    '请调用授权看板指标查询 today_order_count（今天订单数量），简短说明工具返回的结果。',
  )
  await panel.getByRole('button', { name: '发送', exact: true }).click()
  await expect(
    panel.locator('.assistant-float__source').filter({ hasText: 'ERP 首页指标' }),
  ).toBeVisible({ timeout: 90_000 })
  await expect(panel.getByRole('button', { name: '发送', exact: true })).toHaveText('发送', {
    timeout: 90_000,
  })
  expect(metricRequestId).toBeTruthy()

  const token = await page.evaluate(() => localStorage.getItem('erp_access_token'))
  expect(token).toBeTruthy()
  await expect
    .poll(
      async () => {
        const usageResponse = await page.request.get(
          new URL('/assistant/usage/me?days=1', apiBaseURL).toString(),
          { headers: { Authorization: `Bearer ${token}` } },
        )
        if (!usageResponse.ok()) return false
        const usageBody = await usageResponse.json()
        return (
          usageBody.code === '0' &&
          usageBody.data.items.some(
            (item: {
              request_id: string
              provider: string
              status: string
              tool_names: string[]
            }) =>
              item.request_id === metricRequestId &&
              item.provider === 'deepseek' &&
              item.status === 'success' &&
              item.tool_names.includes('authorized_metric'),
          )
        )
      },
      { timeout: 10_000, intervals: [100, 250, 500] },
    )
    .toBe(true)

  await input.fill(
    '请详细写一篇很长的中文说明，逐步解释 ERP 中首页、采购、库存、订单、审批和售后如何协同。',
  )
  await panel.getByRole('button', { name: '发送', exact: true }).click()
  const stopButton = panel.getByRole('button', { name: '停止', exact: true })
  await expect(stopButton).toBeVisible({ timeout: 20_000 })
  await expect(answers.last()).toContainText(/\S+/, { timeout: 60_000 })
  await stopButton.click()
  await expect(
    panel.getByText('已停止生成；本条未完成内容不会进入后续上下文。', { exact: true }),
  ).toBeVisible({ timeout: 20_000 })

  await page.route('**/assistant/messages/stream', (route) => route.abort())
  await input.fill('验证连接异常时界面可恢复。')
  await panel.getByRole('button', { name: '发送', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('连接 AI 服务失败', {
    timeout: 20_000,
  })
  await page.unroute('**/assistant/messages/stream')

  await page.locator('.app-header__user').click()
  await page.getByRole('menuitem', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login/)
  await login(page, readCredentials('采购员.md', '采购员'), '/purchase/plans')
  const switchedPanel = await openAssistant(page)
  await expect(switchedPanel.locator('.assistant-float__message')).toHaveCount(0)
  await expect(
    switchedPanel.getByText('还没有对话。可以从下面的推荐问题开始。', { exact: true }),
  ).toBeVisible()

  const switchedToken = await page.evaluate(() => localStorage.getItem('erp_access_token'))
  const switchedProfile = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('erp_auth_profile') || '{}') as { staff_id?: number },
  )
  const capabilitiesResponse = await page.request.get(
    new URL('/assistant/capabilities', apiBaseURL).toString(),
    { headers: { Authorization: `Bearer ${switchedToken}` } },
  )
  const capabilitiesBody = await capabilitiesResponse.json()
  expect(capabilitiesResponse.ok()).toBeTruthy()
  expect(capabilitiesBody.data.staff_id).toBe(switchedProfile.staff_id)
})
