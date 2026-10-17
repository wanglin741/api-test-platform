import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useCaseStore } from './case'

function mockClient(handlers: Record<string, (...args: unknown[]) => unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const CASE_A = {
  id: 1,
  project_id: 1,
  name: '登录链路',
  description: null,
  priority: 'P0',
  tags: [],
  current_version: 2,
  created_by: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

describe('caseStore', () => {
  it('load:分页拉取用例列表', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse({ items: [CASE_A], total: 1, page: 2, page_size: 20 })),
    })
    const store = useCaseStore()

    await store.load(1, client, 2)

    expect(store.items).toEqual([CASE_A])
    expect(store.total).toBe(1)
    expect(store.page).toBe(2)
    const [path, opts] = (client.GET as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { params: { query: Record<string, number> } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/cases')
    expect(opts.params.query).toEqual({ page: 2, page_size: 20 })
  })

  it('loadOne:返回用例详情(含 dsl)', async () => {
    setActivePinia(createPinia())
    const detail = { ...CASE_A, dsl: { dsl_version: 1, steps: [] } }
    const client = mockClient({ GET: vi.fn(async () => okResponse(detail)) })
    const store = useCaseStore()

    const got = await store.loadOne(1, client)

    expect(got?.dsl).toEqual({ dsl_version: 1, steps: [] })
    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/cases/{case_id}',
    )
  })

  it('save:PATCH 提交 dsl,返回新版本详情', async () => {
    setActivePinia(createPinia())
    const updated = { ...CASE_A, current_version: 3, dsl: { dsl_version: 1, steps: [] } }
    const client = mockClient({ PATCH: vi.fn(async () => okResponse(updated)) })
    const store = useCaseStore()

    const saved = await store.save(1, { dsl: { dsl_version: 1, steps: [] } }, client)

    expect(saved?.current_version).toBe(3)
    const [path, opts] = (client.PATCH as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { body: unknown },
    ]
    expect(path).toBe('/api/v1/cases/{case_id}')
    expect(opts.body).toEqual({ dsl: { dsl_version: 1, steps: [] } })
  })

  it('create:POST 后重载列表', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse({ items: [CASE_A], total: 1, page: 1, page_size: 20 })),
      POST: vi.fn(async () => okResponse({ ...CASE_A, dsl: {} })),
    })
    const store = useCaseStore()

    const created = await store.create(1, { name: '新用例', dsl: {} }, client)

    expect(created?.id).toBe(1)
    expect(store.items).toHaveLength(1)
    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/cases',
    )
  })
})
