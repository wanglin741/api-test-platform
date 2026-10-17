import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useEnvironmentStore } from './environment'

function mockClient(handlers: Record<string, (...args: unknown[]) => unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const ENV_A = {
  id: 1,
  project_id: 1,
  name: 'dev',
  description: null,
  base_url: 'http://dev',
  default_headers: {},
  auth: null,
  is_default: false,
  sort_order: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}
const ENV_B = {
  id: 2,
  project_id: 1,
  name: 'staging',
  description: null,
  base_url: 'http://staging',
  default_headers: {},
  auth: null,
  is_default: true,
  sort_order: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

describe('environmentStore', () => {
  it('load:按项目拉取,自动选中第一个环境', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse([ENV_A, ENV_B])),
    })
    const store = useEnvironmentStore()

    await store.load(1, client)

    expect(store.items).toHaveLength(2)
    expect(store.selectedId).toBe(1)
  })

  it('load:当前选中环境不在列表(如已删)时回落到第一个', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ GET: vi.fn(async () => okResponse([ENV_A])) })
    const store = useEnvironmentStore()
    store.selectedId = 99

    await store.load(1, client)

    expect(store.selectedId).toBe(1)
  })

  it('create:POST 后重载并选中新环境', async () => {
    setActivePinia(createPinia())
    let envs = [ENV_A]
    const client = mockClient({
      GET: vi.fn(async () => okResponse(envs)),
      POST: vi.fn(async () => {
        envs = [...envs, ENV_B]
        return okResponse(ENV_B)
      }),
    })
    const store = useEnvironmentStore()
    await store.load(1, client)

    const created = await store.create(
      1,
      { name: 'staging', is_default: false, sort_order: 0 },
      client,
    )

    expect(created?.id).toBe(2)
    expect(store.items).toHaveLength(2)
    expect(store.selectedId).toBe(2)
    // POST 落在项目作用域端点
    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/environments',
    )
  })

  it('remove:DELETE 后重载,选中回落', async () => {
    setActivePinia(createPinia())
    let envs = [ENV_A, ENV_B]
    const client = mockClient({
      GET: vi.fn(async () => okResponse(envs)),
      DELETE: vi.fn(async () => {
        envs = [ENV_A]
        return okResponse(undefined)
      }),
    })
    const store = useEnvironmentStore()
    await store.load(1, client)
    store.selectedId = 2

    await store.remove(1, 2, client)

    expect(store.items).toHaveLength(1)
    expect(store.selectedId).toBe(1)
  })
})
