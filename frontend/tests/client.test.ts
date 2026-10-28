import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  api,
  ApiError,
  getAccessToken,
  setAccessToken,
  setUnauthorizedHandler,
} from '@/api/client'
import client from '@/api/client'

function responseAdapter<T>(data: T, status = 200): AxiosAdapter {
  return async (config: InternalAxiosRequestConfig) => ({
    data,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    headers: {},
    config,
  })
}

describe('api client', () => {
  beforeEach(() => {
    window.localStorage.clear()
    setUnauthorizedHandler(null)
  })

  afterEach(() => {
    setUnauthorizedHandler(null)
    window.localStorage.clear()
  })

  it('injects the access token and unwraps a successful Result', async () => {
    setAccessToken('test-token')
    let authorization = ''

    const result = await api.get<{ pong: boolean }>('/ping', {
      adapter: async (config) => {
        authorization = String(config.headers.Authorization || '')
        return responseAdapter(
          { code: '0', message: '成功', data: { pong: true } },
        )(config)
      },
    })

    expect(authorization).toBe('Bearer test-token')
    expect(result).toEqual({ pong: true })
  })

  it('rejects a non-zero business code as ApiError', async () => {
    const promise = api.get('/product/1', {
      adapter: responseAdapter({
        code: '40001',
        message: '商品不存在',
        data: null,
      }),
    })

    await expect(promise).rejects.toMatchObject({
      name: 'ApiError',
      code: '40001',
      message: '商品不存在',
    })
  })

  it('clears the session and invokes the unauthorized handler on 401', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    setAccessToken('expired-token')

    const promise = client.get('/secure', {
      adapter: async (config: InternalAxiosRequestConfig) => {
        throw new AxiosError(
          'Unauthorized',
          AxiosError.ERR_BAD_REQUEST,
          config,
          undefined,
          {
            data: { code: '40100', message: '登录已过期', data: null },
            status: 401,
            statusText: 'Unauthorized',
            headers: {},
            config,
          },
        )
      },
    })

    await expect(promise).rejects.toBeInstanceOf(ApiError)
    expect(getAccessToken()).toBeNull()
    expect(handler).toHaveBeenCalledOnce()
  })
})
