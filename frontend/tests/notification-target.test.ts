import { describe, expect, it } from 'vitest'

import type { NotificationItem } from '@/api/notification'
import { routes } from '@/router/routes'
import { resolveNotificationTarget } from '@/utils/notification-target'

function makeItem(
  eventCode: string,
  type: string,
  id = 42,
): Pick<NotificationItem, 'event_code' | 'target'> {
  return { event_code: eventCode, target: { type, id } }
}

describe('notification target routing', () => {
  it('opens approval documents through the guarded approval workspace', () => {
    expect(resolveNotificationTarget(makeItem('approval.task_assigned', 'stock_in'))).toEqual({
      name: 'approval-mine',
      query: { notificationDocType: 'stock_in', notificationDocId: '42' },
    })
  })

  it('opens purchase arrival messages through the guarded purchase order page', () => {
    expect(
      resolveNotificationTarget(
        makeItem('purchase_order.arrival_progress_changed', 'purchase_order', 91),
      ),
    ).toEqual({
      name: 'purchase-orders',
      query: { notificationTargetId: '91' },
    })
  })

  it('rejects unknown event types, target types, and invalid IDs', () => {
    expect(resolveNotificationTarget(makeItem('redirect.external', 'purchase_order'))).toBeNull()
    expect(resolveNotificationTarget(makeItem('approval.task_assigned', 'external_url'))).toBeNull()
    expect(
      resolveNotificationTarget(
        makeItem('approval.task_assigned', 'purchase_plan', Number.MAX_SAFE_INTEGER + 1),
      ),
    ).toBeNull()
  })

  it('registers the message center for every authenticated role without adding a module permission', () => {
    const appShell = routes.find((route) => route.path === '/')
    const center = appShell?.children?.find((route) => route.name === 'notifications')
    expect(center?.meta?.permission).toBeUndefined()
  })
})
