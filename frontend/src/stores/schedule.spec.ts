import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useScheduleStore } from './schedule'

function mockClient(handlers: Record<string, unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const SCHEDULE_A = {
  id: 1,
  project_id: 1,
  name: '每晚全量',
  plan_id: 10,
  cron_expression: '0 2 * * *',
  timezone: 'Asia/Shanghai',
  enabled: true,
  environment_id: null,
  next_run_at: '2026-10-22T18:00:00Z',
  last_run_at: null,
  last_run_id: null,
  last_skip_at: null,
  last_skip_reason: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}
const SCHEDULE_B = { ...SCHEDULE_A, id: 2, name: '早间冒烟', plan_id: 11, enabled: false }

describe('scheduleStore', () => {
  it('load:按项目拉列表,可带 plan_id 过滤', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ GET: vi.fn(async () => okResponse([SCHEDULE_A])) })
    const store = useScheduleStore()

    await store.load(1, 10, client)

    expect(store.items).toHaveLength(1)
    const [path, opts] = (client.GET as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { params: { path: { project_id: number }; query?: { plan_id?: number } } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/schedules')
    expect(opts.params.path.project_id).toBe(1)
    expect(opts.params.query?.plan_id).toBe(10)
  })

  it('create:POST 后重载列表', async () => {
    setActivePinia(createPinia())
    let schedules: unknown[] = []
    const client = mockClient({
      GET: vi.fn(async () => okResponse(schedules)),
      POST: vi.fn(async () => {
        schedules = [SCHEDULE_A]
        return okResponse(SCHEDULE_A)
      }),
    })
    const store = useScheduleStore()

    const created = await store.create(
      1,
      { name: '每晚全量', plan_id: 10, cron_expression: '0 2 * * *', timezone: 'Asia/Shanghai' },
      client,
    )

    expect(created?.id).toBe(1)
    expect(store.items).toHaveLength(1)
    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/schedules',
    )
    // 重载必须带 plan_id:不带会拿项目级全量列表回填到计划卡片
    const [reloadPath, reloadOpts] = (client.GET as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { params: { query?: { plan_id?: number } } },
    ]
    expect(reloadPath).toBe('/api/v1/projects/{project_id}/schedules')
    expect(reloadOpts.params.query?.plan_id).toBe(10)
  })

  it('update:PATCH 后同步列表行', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      PATCH: vi.fn(async () => okResponse({ ...SCHEDULE_A, name: '改名', enabled: false })),
    })
    const store = useScheduleStore()
    store.items = [SCHEDULE_A]

    await store.update(1, { name: '改名' }, client)

    expect(store.items[0]?.name).toBe('改名')
    expect(store.items[0]?.enabled).toBe(false)
  })

  it('remove:DELETE 后重载列表', async () => {
    setActivePinia(createPinia())
    let schedules: unknown[] = [SCHEDULE_A, SCHEDULE_B]
    const client = mockClient({
      GET: vi.fn(async () => okResponse(schedules)),
      DELETE: vi.fn(async () => {
        schedules = [SCHEDULE_B]
        return okResponse(undefined)
      }),
    })
    const store = useScheduleStore()
    await store.load(1, null, client)

    await store.remove(1, 1, client)

    expect(store.items).toHaveLength(1)
    expect(store.items[0]?.id).toBe(2)
    // 按被删行所属计划重载(首次 load 无过滤,重载带 plan_id=10)
    const reload = (client.GET as ReturnType<typeof vi.fn>).mock.calls[1] as [
      string,
      { params: { query?: { plan_id?: number } } },
    ]
    expect(reload[1].params.query?.plan_id).toBe(10)
  })

  it('enable/disable:POST 后用响应行替换列表行', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      POST: vi.fn(async (path: unknown, opts?: { params?: { path?: { schedule_id?: number } } }) => {
        const id = opts?.params?.path?.schedule_id
        return okResponse({ ...SCHEDULE_B, id, enabled: String(path).endsWith('enable') })
      }),
    })
    const store = useScheduleStore()
    store.items = [SCHEDULE_A, SCHEDULE_B]

    await store.setEnabled(2, false, client)
    expect(store.items[1]?.enabled).toBe(false)
    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/schedules/{schedule_id}/disable',
    )

    await store.setEnabled(2, true, client)
    expect(store.items[1]?.enabled).toBe(true)
    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[1]?.[0]).toBe(
      '/api/v1/schedules/{schedule_id}/enable',
    )
  })

  it('代际守卫:慢的旧 load 响应不覆盖新的', async () => {
    setActivePinia(createPinia())
    // 第一次请求挂起,第二次立即返回:旧响应后到,不得覆盖
    let releaseFirst: (() => void) | undefined
    const firstGate = new Promise<void>((resolve) => (releaseFirst = resolve))
    let call = 0
    const client = mockClient({
      GET: vi.fn(async () => {
        call += 1
        if (call === 1) {
          await firstGate
          return okResponse([SCHEDULE_A, SCHEDULE_B])
        }
        return okResponse([])
      }),
    })
    const store = useScheduleStore()

    const stale = store.load(1, null, client)
    await store.load(1, null, client)
    releaseFirst?.()
    await stale

    expect(store.items).toHaveLength(0)
  })

  it('reset:清空列表', () => {
    setActivePinia(createPinia())
    const store = useScheduleStore()
    store.items = [SCHEDULE_A]

    store.reset()

    expect(store.items).toHaveLength(0)
  })
})
