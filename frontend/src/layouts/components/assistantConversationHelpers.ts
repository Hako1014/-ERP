import type { AssistantSource } from '@/api/assistant'
import { isRegisteredAssistantPath } from './assistantShell'

/** 后端不带偏移量的时间仍为 UTC，统一按上海时区展示。 */
export function assistantDate(value: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  return new Date(hasZone ? value : `${value}Z`)
}

export function assistantTime(value: string): string {
  const date = assistantDate(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

function dayNumber(date: Date): number {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date)
  const part = (type: string) => Number(parts.find((item) => item.type === type)?.value)
  return Date.UTC(part('year'), part('month') - 1, part('day')) / 86_400_000
}

export function conversationGroup(value: string, pinned = false, now = new Date()): string {
  if (pinned) return '置顶'
  const date = assistantDate(value)
  if (Number.isNaN(date.getTime())) return '更早'
  const age = dayNumber(now) - dayNumber(date)
  return age <= 0 ? '今天' : age === 1 ? '昨天' : age < 7 ? '最近七天' : '更早'
}

export function highlightedText(text: string, query: string): Array<{ text: string; matched: boolean }> {
  const result: Array<{ text: string; matched: boolean }> = []
  if (!query) return [{ text, matched: false }]
  const needle = query.toLocaleLowerCase()
  const value = text.toLocaleLowerCase()
  let cursor = 0
  let index = value.indexOf(needle)
  while (index >= 0 && result.length < 2000) {
    if (index > cursor) result.push({ text: text.slice(cursor, index), matched: false })
    result.push({ text: text.slice(index, index + query.length), matched: true })
    cursor = index + query.length
    index = value.indexOf(needle, cursor)
  }
  result.push({ text: text.slice(cursor), matched: false })
  return result
}

export function sourceAllowed(value: unknown, permissions: string[]): value is AssistantSource {
  if (!value || typeof value !== 'object') return false
  const source = value as AssistantSource
  if (typeof source.title !== 'string' || typeof source.type !== 'string') return false
  if (source.href !== undefined && (typeof source.href !== 'string' || !isRegisteredAssistantPath(source.href, permissions))) return false
  if (source.type === 'data') {
    return ['ERP 订单', 'ERP 采购订单', 'ERP 库存查询', 'ERP 我的审批', 'ERP 首页指标'].includes(source.title) &&
      typeof source.scope === 'string' && source.scope.length <= 180 &&
      typeof source.queried_at === 'string' && !Number.isNaN(Date.parse(source.queried_at)) &&
      (source.document_number === undefined || (typeof source.document_number === 'string' && source.document_number.length <= 64))
  }
  return typeof source.href === 'string'
}

export function sourceLabel(source: AssistantSource): string {
  return [source.title, source.document_number ? `单据编号 ${source.document_number}` : '',
    source.scope ? `范围：${source.scope}` : '', source.queried_at ? `查询时间：${assistantTime(source.queried_at)}` : ''].filter(Boolean).join(' · ')
}

export function isConversationId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}
