import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
} from 'axios'

export const ACCESS_TOKEN_KEY = 'erp_access_token'

interface ApiEnvelope<T> {
  code: string
  message: string
  data: T
}

export class ApiError extends Error {
  readonly code: string
  readonly status?: number

  constructor(message: string, code = '50000', status?: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

const client: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 15_000,
})

let unauthorizedHandler: (() => void) | null = null
const authenticatedStreams = new Set<AbortController>()

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export function getAccessToken(): string | null {
  return window.localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function trackAuthenticatedStream(controller: AbortController): () => void {
  authenticatedStreams.add(controller)
  const remove = () => authenticatedStreams.delete(controller)
  controller.signal.addEventListener('abort', remove, { once: true })
  return remove
}

export function stopAuthenticatedStreams(): void {
  for (const controller of authenticatedStreams) controller.abort()
  authenticatedStreams.clear()
}

export function setAccessToken(token: string | null): void {
  const previousToken = window.localStorage.getItem(ACCESS_TOKEN_KEY)
  if (previousToken !== token) stopAuthenticatedStreams()
  if (token) {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, token)
  } else {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY)
  }
}

export function handleUnauthorizedResponse(): void {
  setAccessToken(null)
  unauthorizedHandler?.()
}

client.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

client.interceptors.response.use(
  (response) => {
    const payload = response.data as ApiEnvelope<unknown>
    if (
      payload &&
      typeof payload === 'object' &&
      'code' in payload &&
      typeof payload.code === 'string'
    ) {
      if (payload.code !== '0') {
        return Promise.reject(
          new ApiError(payload.message || '请求失败', payload.code, response.status),
        )
      }
      return payload.data
    }
    return response.data
  },
  (error: AxiosError<ApiEnvelope<unknown>>) => {
    const status = error.response?.status
    const payload = error.response?.data
    const code = payload?.code || String(status || '50000')
    if (status === 401 || code === '40100') {
      handleUnauthorizedResponse()
    }
    return Promise.reject(
      new ApiError(payload?.message || error.message || '网络请求失败', code, status),
    )
  },
)

function request<T>(config: AxiosRequestConfig): Promise<T> {
  return client.request<unknown, unknown>(config).then((data) => data as T)
}

export const api = {
  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return request<T>({ ...config, method: 'GET', url })
  },
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return request<T>({ ...config, method: 'POST', url, data })
  },
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return request<T>({ ...config, method: 'PUT', url, data })
  },
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return request<T>({ ...config, method: 'PATCH', url, data })
  },
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return request<T>({ ...config, method: 'DELETE', url })
  },
}

export default client
