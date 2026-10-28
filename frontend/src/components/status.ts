import {
  CircleCheckFilled,
  CircleCloseFilled,
  Clock,
  EditPen,
  InfoFilled,
  Loading,
  RemoveFilled,
  WarningFilled,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'

import type { StatusMeta, StatusTone } from '@/components/types'

interface StatusDefinition {
  tone: StatusTone
  icon: Component
  label?: string
}

const SUCCESS_STATUSES: Array<[string, string]> = [
  ['APPROVED', '已审核'],
  ['COMPLETED', '已完成'],
  ['CONFIRMED', '已确认'],
  ['DONE', '已完成'],
  ['FULL', '已核销'],
  ['ISSUED', '已开票'],
  ['NORMAL', '正常'],
  ['PAID', '已付款'],
  ['RECEIVED', '已接收'],
  ['SUCCESS', '成功'],
  ['SYNCED', '已同步'],
]

const WARNING_STATUSES: Array<[string, string]> = [
  ['PARTIAL', '部分完成'],
  ['PENDING', '待处理'],
  ['UNPAID', '未付款'],
  ['WARNING', '需关注'],
]

const PRIMARY_STATUSES: Array<[string, string]> = [
  ['CHECKING', '核对中'],
  ['NEW', '新建'],
  ['PROCESSING', '处理中'],
  ['RECEIVING', '收货中'],
  ['SCANNING', '扫描中'],
  ['SUBMITTED', '已提交'],
]

const DANGER_STATUSES: Array<[string, string]> = [
  ['ERROR', '异常'],
  ['FAILED', '失败'],
  ['REJECTED', '已驳回'],
]

const INFO_STATUSES: Array<[string, string]> = [
  ['CANCELLED', '已取消'],
  ['CANCELED', '已取消'],
  ['DELETED', '已删除'],
  ['DISABLED', '已禁用'],
  ['DRAFT', '草稿'],
  ['INVALID', '已失效'],
  ['SKIPPED', '已跳过'],
  ['TERMINATED', '已终止'],
]

const STATUS_DEFINITIONS: Record<string, StatusDefinition> =
  Object.fromEntries([
    ...SUCCESS_STATUSES.map(([status, label]) => [
      status,
      { tone: 'success', icon: CircleCheckFilled, label },
    ]),
    ...WARNING_STATUSES.map(([status, label]) => [
      status,
      { tone: 'warning', icon: WarningFilled, label },
    ]),
    ...PRIMARY_STATUSES.map(([status, label]) => [
      status,
      { tone: 'primary', icon: Loading, label },
    ]),
    ...DANGER_STATUSES.map(([status, label]) => [
      status,
      { tone: 'danger', icon: CircleCloseFilled, label },
    ]),
    ...INFO_STATUSES.map(([status, label]) => [
      status,
      { tone: 'info', icon: RemoveFilled, label },
    ]),
    ['EDIT', { tone: 'info', icon: EditPen, label: '编辑中' }],
    ['SHIPPED', { tone: 'success', icon: CircleCheckFilled, label: '已发货' }],
  ])

const FALLBACK_STATUS: StatusMeta = {
  tone: 'info',
  icon: InfoFilled,
}

export function resolveStatusMeta(
  status?: string,
  tone?: StatusTone,
  icon?: Component,
): StatusMeta {
  const normalized = String(status || '').trim().toUpperCase()
  const definition = STATUS_DEFINITIONS[normalized] || FALLBACK_STATUS

  return {
    tone: tone || definition.tone,
    icon: icon || definition.icon,
    label: definition.label,
  }
}

export function statusLabel(
  status?: string,
  label?: string,
  tone?: StatusTone,
  icon?: Component,
): string {
  const meta = resolveStatusMeta(status, tone, icon)
  return label || meta.label || String(status || '--')
}
