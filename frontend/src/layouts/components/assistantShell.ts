import assistantRouteManifest from '@/generated/assistantRoutes.json'
import { shallowRef } from 'vue'

const assistantRoutes = assistantRouteManifest.routes

export const PANEL_WIDTH = 380
export const PANEL_HEIGHT = 560
export const PANEL_MARGIN = 16
export const MIN_PANEL_SIZE = 240
export const COMPACT_MAX_WIDTH = 640

const POSITION_KEY_PREFIX = 'erp_assistant_position_'

export interface AssistantSelectedDocument {
  doc_type: 'order' | 'purchase_order'
  doc_id: number
}

const selectedDocumentContext = shallowRef<{
  staffId: number
  document: AssistantSelectedDocument
} | null>(null)

/** 当前详情选择仅留内存；发送时供服务端复核授权，不写入 localStorage。 */
export function setAssistantSelectedDocument(
  staffId: number,
  document: AssistantSelectedDocument | null,
): void {
  if (!document) {
    if (selectedDocumentContext.value?.staffId === staffId) {
      selectedDocumentContext.value = null
    }
    return
  }
  if (
    !Number.isInteger(staffId) ||
    staffId <= 0 ||
    !Number.isInteger(document.doc_id) ||
    document.doc_id <= 0
  ) {
    return
  }
  selectedDocumentContext.value = { staffId, document: { ...document } }
}

export function clearAssistantSelectedDocument(): void {
  selectedDocumentContext.value = null
}

export function getAssistantSelectedDocument(
  staffId: number,
): AssistantSelectedDocument | undefined {
  if (selectedDocumentContext.value?.staffId !== staffId) {
    return undefined
  }
  return { ...selectedDocumentContext.value.document }
}

export interface PanelSize {
  width: number
  height: number
}

export interface PanelPosition {
  x: number
  y: number
}

export function isCompactViewport(viewportWidth: number): boolean {
  return viewportWidth <= COMPACT_MAX_WIDTH
}

/** 展开尺寸：默认 380×560，视口不足时按边距收缩。 */
export function panelSize(viewportWidth: number, viewportHeight: number): PanelSize {
  return {
    width: Math.max(
      MIN_PANEL_SIZE,
      Math.min(PANEL_WIDTH, viewportWidth - PANEL_MARGIN * 2),
    ),
    height: Math.max(
      MIN_PANEL_SIZE,
      Math.min(PANEL_HEIGHT, viewportHeight - PANEL_MARGIN * 2),
    ),
  }
}

/**
 * 把浮窗约束在视口内。
 * 上下限取 [0, 视口 - 面板]，面板比视口还大时归零，
 * 因此标题栏与收起按钮始终可见可点。
 */
export function clampPosition(
  position: PanelPosition,
  size: PanelSize,
  viewportWidth: number,
  viewportHeight: number,
): PanelPosition {
  const maxX = Math.max(0, viewportWidth - size.width)
  const maxY = Math.max(0, viewportHeight - size.height)
  return {
    x: Math.min(Math.max(0, Math.round(position.x)), maxX),
    y: Math.min(Math.max(0, Math.round(position.y)), maxY),
  }
}

export function defaultPosition(
  size: PanelSize,
  viewportWidth: number,
  viewportHeight: number,
): PanelPosition {
  return clampPosition(
    {
      x: viewportWidth - size.width - PANEL_MARGIN,
      y: viewportHeight - size.height - PANEL_MARGIN,
    },
    size,
    viewportWidth,
    viewportHeight,
  )
}

/** 位置按用户隔离存储；只存坐标，不存聊天内容。 */
export function positionStorageKey(staffId: number): string {
  return `${POSITION_KEY_PREFIX}${staffId}`
}

function isFinitePosition(value: unknown): value is PanelPosition {
  if (!value || typeof value !== 'object') {
    return false
  }
  const candidate = value as Partial<PanelPosition>
  return (
    typeof candidate.x === 'number' &&
    typeof candidate.y === 'number' &&
    Number.isFinite(candidate.x) &&
    Number.isFinite(candidate.y)
  )
}

export function readStoredPosition(staffId: number | null | undefined): PanelPosition | null {
  if (!staffId || typeof window === 'undefined') {
    return null
  }
  try {
    const raw = window.localStorage.getItem(positionStorageKey(staffId))
    if (!raw) {
      return null
    }
    const parsed: unknown = JSON.parse(raw)
    return isFinitePosition(parsed) ? { x: parsed.x, y: parsed.y } : null
  } catch {
    return null
  }
}

export function writeStoredPosition(staffId: number, position: PanelPosition): void {
  if (!staffId || typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(
      positionStorageKey(staffId),
      JSON.stringify({ x: position.x, y: position.y }),
    )
  } catch {
    // 存储不可用时忽略：位置只是偏好，不影响功能。
  }
}

export function clearStoredPosition(staffId: number | null | undefined): void {
  if (!staffId || typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.removeItem(positionStorageKey(staffId))
  } catch {
    // 同上。
  }
}

export const ROLE_LABELS: Record<string, string> = {
  ADMIN: '管理员',
  VP: '副总',
  FINANCE: '财务',
  PURCHASER: '采购',
  WAREHOUSE: '仓库',
  QUALITY: '质检',
  AFTER_SALE: '售后',
}

export function resolveRoleLabel(roles: string[]): string {
  const labels = roles.map((role) => ROLE_LABELS[role]).filter(Boolean)
  return labels.length > 0 ? labels.join(' / ') : '当前账号'
}

/** 用菜单树把路由路径映射成中文页面名，避免助手说的页面与系统实际菜单漂移。 */
export function resolvePageLabel(path: string): string {
  const normalized = path.replace(/\/+$/, '') || '/'
  const match = assistantRoutes.find((item) => item.path === normalized)
  return match?.title ?? normalized
}

export function isRegisteredAssistantPath(
  path: string,
  permissions: string[],
): boolean {
  const route = assistantRoutes.find((item) => item.path === path)
  return Boolean(
    route &&
      (permissions.includes('*') || permissions.includes(route.permission)),
  )
}

export function resolveAssistantRouteModule(
  path: string,
  permissions: string[],
): string | undefined {
  const route = assistantRoutes.find((item) => item.path === path)
  return route && (permissions.includes('*') || permissions.includes(route.permission))
    ? route.module ?? undefined
    : undefined
}

export function resolveAllowedPageFilters(
  query: Record<string, unknown>,
): Partial<Record<'status' | 'date_from' | 'date_to', string>> {
  const filters: Partial<Record<'status' | 'date_from' | 'date_to', string>> = {}
  for (const key of ['status', 'date_from', 'date_to'] as const) {
    const value = query[key]
    if (typeof value === 'string' && value.length > 0 && value.length <= 32) {
      filters[key] = value
    }
  }
  return filters
}

export interface AssistantSuggestion {
  code: string
  label: string
  prompt: string
}

const MAX_SUGGESTIONS = 6

interface ActionSuggestion extends AssistantSuggestion {
  module: string
  permission: string
}

const ACTION_SUGGESTIONS: ActionSuggestion[] = [
  {
    code: 'action.purchase.create',
    label: '采购制单操作指引',
    prompt: '采购制单需要核对哪些信息？',
    module: 'purchase',
    permission: 'purchase:create',
  },
  {
    code: 'action.purchase.approve',
    label: '采购审批操作指引',
    prompt: '采购审批需要核对哪些信息？',
    module: 'purchase',
    permission: 'purchase:approve',
  },
  {
    code: 'action.product.manage',
    label: '商品维护操作指引',
    prompt: '商品资料维护有哪些注意事项？',
    module: 'product',
    permission: 'product:manage',
  },
  {
    code: 'action.stock.manage',
    label: '库存作业操作指引',
    prompt: '库存作业有哪些操作注意事项？',
    module: 'stock',
    permission: 'stock:manage',
  },
  {
    code: 'action.order.audit',
    label: '订单审核操作指引',
    prompt: '订单审核需要核对哪些信息？',
    module: 'order',
    permission: 'order:audit',
  },
  {
    code: 'action.order.ship',
    label: '订单发货操作指引',
    prompt: '订单发货前需要核对哪些信息？',
    module: 'order',
    permission: 'order:ship',
  },
  {
    code: 'action.approval.approve',
    label: '审批操作指引',
    prompt: '审批时需要核对哪些信息？',
    module: 'approval',
    permission: 'approval:approve',
  },
  {
    code: 'action.finance.manage',
    label: '财务操作指引',
    prompt: '财务单据处理有哪些操作注意事项？',
    module: 'finance',
    permission: 'finance:manage',
  },
  {
    code: 'action.after_sale.manage',
    label: '售后处理操作指引',
    prompt: '售后处理流程有哪些注意事项？',
    module: 'order',
    permission: 'after_sale:manage',
  },
]

export interface AssistantSuggestionOptions {
  readQueryEnabled?: boolean
  currentModule?: string
}

/**
 * 推荐入口：按可见模块与动作权限过滤。
 * 只做展示过滤，真正的授权仍在服务端。
 */
export function buildSuggestions(
  modules: string[],
  permissions: string[],
  options: AssistantSuggestionOptions = {},
): AssistantSuggestion[] {
  const visibleModules = new Set(modules)
  const allowedPermissions = new Set(permissions)
  const canAccessModule = (moduleName: string) =>
    visibleModules.has(moduleName) &&
    (allowedPermissions.has(`module:${moduleName}`) || allowedPermissions.has('*'))
  const activeModule = options.currentModule ?? modules[0]
  const suggestions: AssistantSuggestion[] = []

  if (activeModule && canAccessModule(activeModule)) {
    suggestions.push({
      code: 'guide.current_page',
      label: '本页怎么操作',
      prompt: '这个页面怎么操作？',
    })
  }

  if (options.readQueryEnabled && activeModule && canAccessModule(activeModule)) {
    suggestions.push({
      code: 'query.current_page',
      label: '查询本页相关业务',
      prompt: '帮我查一下这个页面的业务数据',
    })
  }

  for (const item of ACTION_SUGGESTIONS) {
    if (
      !canAccessModule(item.module) ||
      (!allowedPermissions.has(item.permission) && !allowedPermissions.has('*'))
    ) {
      continue
    }
    suggestions.push(item)
  }

  for (const item of assistantRoutes) {
    if (suggestions.length >= MAX_SUGGESTIONS) {
      break
    }
    if (!item.is_leaf || item.name === 'assistant') {
      continue
    }
    const moduleName = item.module
    if (
      !moduleName ||
      !canAccessModule(moduleName) ||
      (!allowedPermissions.has(item.permission) && !allowedPermissions.has('*'))
    ) {
      continue
    }
    suggestions.push({
      code: `guide.${item.name}`,
      label: item.title,
      prompt: `${item.title}怎么用？`,
    })
  }

  return suggestions.slice(0, MAX_SUGGESTIONS)
}

export async function copyText(text: string): Promise<boolean> {
  if (!text) {
    return false
  }
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
