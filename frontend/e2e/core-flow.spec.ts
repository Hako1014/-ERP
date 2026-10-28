import { expect, test, type APIRequestContext } from '@playwright/test'

const apiBase = 'http://127.0.0.1:8000'

async function callApi(
  request: APIRequestContext,
  token: string,
  method: 'GET' | 'POST',
  path: string,
  data?: object,
) {
  const response = await request.fetch(`${apiBase}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    data,
  })
  const body = await response.json()
  expect(response.ok(), `${path}: ${JSON.stringify(body)}`).toBeTruthy()
  expect(body.code, `${path}: ${JSON.stringify(body)}`).toBe('0')
  return body.data
}

test('真实 MySQL：登录、采购计划、订单审批与扫描发货', async ({ page, request }) => {
  const account = process.env.E2E_ACCOUNT
  const password = process.env.E2E_PASSWORD
  if (!account || !password) {
    throw new Error('需设置 E2E_ACCOUNT 和 E2E_PASSWORD')
  }
  const marker = `TASK069-${Date.now()}`

  await page.goto('/login')
  await page.locator('input[name="account"]').fill(account!)
  await page.locator('input[name="password"]').fill(password!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/dashboard/)
  const token = await page.evaluate(() => localStorage.getItem('erp_access_token'))
  expect(token).toBeTruthy()

  const suppliers = await callApi(request, token!, 'GET', '/suppliers?page=1&page_size=20')
  expect(suppliers.items.length).toBeGreaterThan(0)
  const supplier = suppliers.items[0]
  const needs = await callApi(request, token!, 'GET', `/purchase-plans/needs?supplier_id=${supplier.id}`)
  expect(needs.items.length).toBeGreaterThan(0)
  const need = needs.items[0]
  const plan = await callApi(request, token!, 'POST', '/purchase-plans', {
    supplier_id: supplier.id,
    plan_date: new Date().toISOString().slice(0, 19),
    remark: marker,
    items: [{ product_id: need.product_id, sku_id: need.sku_id, planned_qty: '1', purchase_price: '20' }],
  })
  await page.goto('/purchase/plans')
  await page.getByPlaceholder('计划编号或供应商').fill(plan.plan_no)
  await page.getByRole('button', { name: '查询' }).click()
  await expect(page.getByText(plan.plan_no, { exact: true })).toBeVisible()

  const logisticsRows = await callApi(request, token!, 'GET', '/logistics?is_enabled=true')
  const logistics = logisticsRows[0] ?? await callApi(request, token!, 'POST', '/logistics', {
    name: `${marker}-物流`, company: 'E2E', is_default: true, is_enabled: true,
  })
  const approvalFlows = await callApi(request, token!, 'GET', '/approval/flows')
  if (!approvalFlows.some((flow: { doc_type: string; approvers: unknown[] }) => flow.doc_type === 'order' && flow.approvers.length)) {
    const login = await callApi(request, token!, 'GET', '/auth/me/permissions')
    await callApi(request, token!, 'POST', '/approval/flows', {
      name: `${marker}-审批`, doc_type: 'order',
      approvers: [{ step: 1, name: '订单审核', approver_ids: [login.staff_id] }],
    })
  }

  const order = await callApi(request, token!, 'POST', '/orders', {
    order_no: marker,
    shop_id: 1,
    warehouse_id: 1,
    logistics_id: logistics.id,
    buyer_nick: marker,
    receiver_name: 'E2E Receiver',
    receiver_phone: '13800000000',
    receiver_province: 'Zhejiang',
    receiver_city: 'Hangzhou',
    receiver_address: marker,
    order_time: new Date().toISOString().slice(0, 19),
    pay_time: new Date().toISOString().slice(0, 19),
    pay_status: 'PAID',
    paid_amount: '20.00',
    items: [{ product_id: 1, sku_id: 1, qty: '1.000', price: '20.00', is_gift: false }],
  })
  await page.goto('/orders/new')
  await page.getByPlaceholder('订单号、买家、收货人或运单号').fill(marker)
  await page.getByRole('button', { name: '查询' }).click()
  await expect(page.getByText(marker, { exact: true }).first()).toBeVisible()

  const approved = await callApi(request, token!, 'POST', `/orders/${order.id}/approve`, { comment: marker })
  expect(approved.audit_status).toBe('APPROVED')
  await page.goto('/orders/scan-shipment')
  await page.getByPlaceholder('扫描后按回车连续操作').fill(marker)
  const scanResponse = page.waitForResponse((response) => response.url().endsWith('/shipments/scan'))
  await page.getByRole('button', { name: '扫描发货' }).click()
  const scanPayload = await (await scanResponse).json()
  expect(scanPayload.code, JSON.stringify(scanPayload)).toBe('0')
  await expect(page.getByText('订单已发完')).toBeVisible()
  const shipped = await callApi(request, token!, 'GET', `/orders/${order.id}`)
  expect(shipped.status).toBe('SHIPPED')
})
