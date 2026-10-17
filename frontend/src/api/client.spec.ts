import { describe, expect, it, vi } from 'vitest'

import { ApiError, createApiClient } from './client'

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('createApiClient 错误拦截', () => {
  it('非 2xx:解析 {"error":{...}} → 通知 + 抛 ApiError', async () => {
    const onError = vi.fn()
    const fetchMock = vi.fn(async () =>
      jsonResponse(404, {
        error: { code: 'project_not_found', message: '项目 不存在', details: { project_id: 9 } },
      }),
    )
    const client = createApiClient({ baseUrl: 'http://test.local', onError, fetch: fetchMock })

    const exc = await client
      .GET('/api/v1/projects/{project_id}', { params: { path: { project_id: 9 } } })
      .then(
        () => null,
        (e: unknown) => e,
      )

    expect(exc).toBeInstanceOf(ApiError)
    const err = exc as ApiError
    expect(err.code).toBe('project_not_found')
    expect(err.status).toBe(404)
    expect(err.message).toBe('项目 不存在')
    expect(err.details).toEqual({ project_id: 9 })
    expect(onError).toHaveBeenCalledWith('项目 不存在', 'project_not_found')
  })

  it('2xx:返回 data,不触发 onError', async () => {
    const onError = vi.fn()
    const fetchMock = vi.fn(async () => jsonResponse(200, [{ id: 1, name: 'p' }]))
    const client = createApiClient({ baseUrl: 'http://test.local', onError, fetch: fetchMock })

    const { data } = await client.GET('/api/v1/projects')

    expect(data).toEqual([{ id: 1, name: 'p' }])
    expect(onError).not.toHaveBeenCalled()
  })

  it('非 JSON 错误体:退回默认文案', async () => {
    const fetchMock = vi.fn(async () => new Response('oops', { status: 502 }))
    const client = createApiClient({ baseUrl: 'http://test.local', fetch: fetchMock })

    const exc = await client.GET('/api/v1/projects').then(
      () => null,
      (e: unknown) => e,
    )

    expect((exc as ApiError).message).toContain('502')
  })
})
