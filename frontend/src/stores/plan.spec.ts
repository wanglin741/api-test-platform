import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { usePlanStore } from './plan'

function mockClient(handlers: Record<string, unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const PLAN_A = {
  id: 1,
  project_id: 1,
  name: '每晚回归',
  description: null,
  environment_id: null,
  execute_mode: 'serial',
  concurrency: 1,
  stop_on_fail: false,
  item_count: 2,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}
const PLAN_B = { ...PLAN_A, id: 2, name: '冒烟' }

const ITEM_LOGIN = { id: 11, item_type: 'case', case_id: 101, case_name: '登录', sort_order: 0 }
const ITEM_ORDER = { id: 12, item_type: 'case', case_id: 102, case_name: '下单', sort_order: 1 }

describe('planStore', () => {
  it('load:按项目拉取计划列表', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ GET: vi.fn(async () => okResponse([PLAN_A, PLAN_B])) })
    const store = usePlanStore()

    await store.load(1, client)

    expect(store.items).toHaveLength(2)
    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/plans',
    )
  })

  it('create:POST 后重载列表', async () => {
    setActivePinia(createPinia())
    let plans: unknown[] = []
    const client = mockClient({
      GET: vi.fn(async () => okResponse(plans)),
      POST: vi.fn(async () => {
        plans = [PLAN_A]
        return okResponse(PLAN_A)
      }),
    })
    const store = usePlanStore()

    const created = await store.create(
      1,
      { name: '每晚回归', execute_mode: 'serial', concurrency: 1, stop_on_fail: false },
      client,
    )

    expect(created?.id).toBe(1)
    expect(store.items).toHaveLength(1)
    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/plans',
    )
  })

  it('update:PATCH 后同步列表行与详情行', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      PATCH: vi.fn(async () => okResponse({ ...PLAN_A, name: '回归 v2', concurrency: 4 })),
    })
    const store = usePlanStore()
    store.items = [PLAN_A]
    store.current = PLAN_A

    await store.update(1, { name: '回归 v2' }, client)

    expect(store.items[0]?.name).toBe('回归 v2')
    expect(store.items[0]?.concurrency).toBe(4)
    expect(store.current?.name).toBe('回归 v2')
  })

  it('remove:DELETE 后重载并清详情上下文', async () => {
    setActivePinia(createPinia())
    let plans: unknown[] = [PLAN_A, PLAN_B]
    const client = mockClient({
      GET: vi.fn(async (_path: unknown, opts?: { params?: { path?: { plan_id?: number } } }) => {
        const id = opts?.params?.path?.plan_id
        if (id !== undefined) return okResponse(id === 1 ? PLAN_A : PLAN_B)
        return okResponse(plans)
      }),
      DELETE: vi.fn(async () => {
        plans = [PLAN_A]
        return okResponse(undefined)
      }),
    })
    const store = usePlanStore()
    await store.load(1, client)
    await store.loadDetail(2, client)
    expect(store.current?.id).toBe(2)

    await store.remove(1, 2, client)

    expect(store.items).toHaveLength(1)
    expect(store.current).toBeNull()
    expect(store.planItems).toHaveLength(0)
  })

  it('loadDetail:计划与清单并发取,整组替换', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async (path: unknown) => {
        if (String(path).endsWith('/items')) return okResponse([ITEM_LOGIN, ITEM_ORDER])
        return okResponse(PLAN_A)
      }),
    })
    const store = usePlanStore()

    const plan = await store.loadDetail(1, client)

    expect(plan?.id).toBe(1)
    expect(store.current?.id).toBe(1)
    expect(store.planItems.map((i) => i.case_name)).toEqual(['登录', '下单'])
  })

  it('saveItems:PUT caseIds 数组顺序,响应替换清单并同步 item_count', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      PUT: vi.fn(async () => okResponse([ITEM_ORDER, ITEM_LOGIN])),
    })
    const store = usePlanStore()
    store.current = PLAN_A
    store.items = [PLAN_A]

    await store.saveItems(1, [102, 101], client)

    expect(store.planItems.map((i) => i.case_id)).toEqual([102, 101])
    expect(store.items[0]?.item_count).toBe(2)
    const body = (client.PUT as ReturnType<typeof vi.fn>).mock.calls[0]?.[1] as {
      body: { items: { case_id: number }[] }
    }
    expect(body.body.items).toEqual([{ case_id: 102 }, { case_id: 101 }])
  })
})
