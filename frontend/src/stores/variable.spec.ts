import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useVariableStore } from './variable'

function mockClient(handlers: Record<string, (...args: unknown[]) => unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

describe('variableStore', () => {
  it('loadProjectVars:取项目级变量(丢弃 id 保留编辑所需字段)', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () =>
        okResponse([{ id: 9, name: 'timeout', value: '30', is_secret: false, description: null }]),
      ),
    })
    const store = useVariableStore()

    await store.loadProjectVars(1, client)

    expect(store.projectVars).toEqual([{ name: 'timeout', value: '30', is_secret: false, description: null }])
    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/variables',
    )
  })

  it('loadEnvVars:取环境级变量', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse([{ id: 3, name: 'password', value: 's3cret', is_secret: true, description: null }])),
    })
    const store = useVariableStore()

    await store.loadEnvVars(2, client)

    expect(store.envVars).toEqual([{ name: 'password', value: 's3cret', is_secret: true, description: null }])
    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/environments/{environment_id}/variables',
    )
  })

  it('saveProjectVars:PUT 整体替换', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      PUT: vi.fn(async () => okResponse(undefined)),
    })
    const store = useVariableStore()
    const items = [{ name: 'a', value: '1', is_secret: false, description: null }]

    await store.saveProjectVars(1, items, client)

    const [path, opts] = (client.PUT as ReturnType<typeof vi.fn>).mock.calls[0] as [string, { body: unknown }]
    expect(path).toBe('/api/v1/projects/{project_id}/variables')
    expect(opts.body).toEqual({ items })
  })

  it('saveEnvVars:PUT 到环境作用域', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ PUT: vi.fn(async () => okResponse(undefined)) })
    const store = useVariableStore()
    const items = [{ name: 'pwd', value: 'x', is_secret: true, description: null }]

    await store.saveEnvVars(7, items, client)

    const [path] = (client.PUT as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(path).toBe('/api/v1/environments/{environment_id}/variables')
  })
})
