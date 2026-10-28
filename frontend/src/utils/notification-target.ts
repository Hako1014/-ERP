import type { RouteLocationRaw } from 'vue-router'

import type { NotificationItem } from '@/api/notification'

const APPROVAL_DOCUMENT_TYPES = new Set([
  'purchase_plan',
  'purchase_order',
  'purchase_prepay',
  'purchase_return',
  'purchase_invoice',
  'stock_in',
  'stock_out',
  'transfer',
  'transfer_out',
  'transfer_in',
  'stock_check',
  'location_adjust',
  'order',
  'expense',
  'income',
  'payment',
  'receipt',
])

/** 将后端类型化目标映射到已知受保护路由，不接受通知正文中的 URL。 */
export function resolveNotificationTarget(
  item: Pick<NotificationItem, 'event_code' | 'target'>,
): RouteLocationRaw | null {
  const id = item.target.id
  if (!Number.isSafeInteger(id) || id < 1) return null

  if (
    item.event_code.startsWith('approval.') &&
    APPROVAL_DOCUMENT_TYPES.has(item.target.type)
  ) {
    return {
      name: 'approval-mine',
      query: {
        notificationDocType: item.target.type,
        notificationDocId: String(id),
      },
    }
  }

  if (
    item.event_code === 'purchase_order.arrival_progress_changed' &&
    item.target.type === 'purchase_order'
  ) {
    return {
      name: 'purchase-orders',
      query: { notificationTargetId: String(id) },
    }
  }

  return null
}
