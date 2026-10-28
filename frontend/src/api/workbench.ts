import { api } from '@/api/client'

export interface AnnouncementSummary {
  id: number
  title: string
  preview: string
  published_at: string | null
  expires_at: string | null
}

export interface AnnouncementPage {
  total: number
  page: number
  page_size: number
  items: AnnouncementSummary[]
}

export interface AnnouncementDetail extends AnnouncementSummary {
  content: string
  starts_at: string | null
}

export interface WorkbenchTodo {
  doc_type: string
  doc_type_label: string
  doc_id: number
  doc_no: string | null
  title: string
  current_step: number | null
  submitted_at: string | null
  target: {
    path: string
    query: Record<string, string>
  }
}

export interface WorkbenchTodoPage {
  available: boolean
  total: number
  page: number
  page_size: number
  items: WorkbenchTodo[]
}

export interface WorkbenchHelp {
  title: string
  route_key: string | null
  guidance: string
  faq: string
  sources: Array<{ type: string; title: string; href: string; scope: string }>
  assistant_available: boolean
  assistant_path: string | null
}

export const workbenchApi = {
  listAnnouncements(pageSize = 10, signal?: AbortSignal): Promise<AnnouncementPage> {
    return api.get<AnnouncementPage>('/announcements', { params: { page_size: pageSize }, signal })
  },
  getAnnouncement(id: number, signal?: AbortSignal): Promise<AnnouncementDetail> {
    return api.get<AnnouncementDetail>(`/announcements/${id}`, { signal })
  },
  listTodos(pageSize = 10, signal?: AbortSignal): Promise<WorkbenchTodoPage> {
    return api.get<WorkbenchTodoPage>('/workbench/todos', { params: { page_size: pageSize }, signal })
  },
  getHelp(routeKey?: string, signal?: AbortSignal): Promise<WorkbenchHelp> {
    return api.get<WorkbenchHelp>('/workbench/help', {
      params: routeKey ? { route_key: routeKey } : undefined,
      signal,
    })
  },
}
