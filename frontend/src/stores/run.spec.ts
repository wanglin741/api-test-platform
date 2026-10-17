import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useRunStore } from './run'

function mockClient(handlers: Record<string, (...args: unknown[]) => unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const RUN_RUNNING = {
  run_id: 'r-1', project_id: 1, case_id: 7, case_version: 2, environment_id: 3,
  trigger_mode: 'manual', status: 'running', duration_ms: null, summary: null,
  created_at: '2026-01-01T00:00:00Z', started_at: null, finished_at: null,
}

describe('runStore', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('trigger:POST 后返回 run_id', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ POST: vi.fn(async () => okResponse({ run_id: 'r-1', status: 'pending' })) })

    const runId = await useRunStore().trigger(1, 7, 3, client)

    expect(runId).toBe('r-1')
    const [path, opts] = (client.POST as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { body: unknown },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/runs')
    expect(opts.body).toEqual({ case_id: 7, environment_id: 3 })
  })

  it('refresh:拉取 run + steps', async () => {
    setActivePinia(createPinia())
    const step = {
      id: 1, step_index: 0, step_name: '登录', step_type: 'http', status: 'passed',
      duration_ms: 12, request: null, response: null, extracted_variables: {},
      assertions: [], assertion_total: 0, assertion_passed: 0, error_message: null,
    }
    const client = mockClient({
      GET: vi.fn(async (path: unknown) =>
        okResponse(
          String(path).includes('/steps')
            ? { items: [step], count: 1, truncated: false }
            : RUN_RUNNING,
        ),
      ),
    })
    const store = useRunStore()

    await store.refresh('r-1', client)

    expect(store.run?.status).toBe('running')
    expect(store.steps).toHaveLength(1)
    expect(store.stepsTruncated).toBe(false)
  })

  it('轮询:非终态每 2s 刷新,到终态自动停', async () => {
    setActivePinia(createPinia())
    // refresh 每次拉 run + steps;第 3 次 GET(第 2 轮的 run)拿到终态
    const client = mockClient({
      GET: vi
        .fn()
        .mockResolvedValue(okResponse({ ...RUN_RUNNING, status: 'success' }))
        .mockResolvedValueOnce(okResponse(RUN_RUNNING))
        .mockResolvedValueOnce(okResponse(RUN_RUNNING))
        .mockResolvedValueOnce(okResponse({ ...RUN_RUNNING, status: 'success' })),
    })

    const store = useRunStore()
    store.startPolling('r-1', client)
    await vi.advanceTimersByTimeAsync(0)

    expect(store.run?.status).toBe('running')
    expect(store.polling).toBe(true)

    await vi.advanceTimersByTimeAsync(2000)

    expect(store.run?.status).toBe('success')
    expect(store.polling).toBe(false)
    // 立即 1 次 + 2s 后 1 次拿终态,之后不再轮
    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(4)
    const callsAfter = (client.GET as ReturnType<typeof vi.fn>).mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls.length).toBe(callsAfter)
  })

  it('stopPolling:手动停止后不再刷新', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse(RUN_RUNNING)),
    })
    const store = useRunStore()

    store.startPolling('r-1', client)
    await vi.advanceTimersByTimeAsync(0)
    store.stopPolling()
    const calls = (client.GET as ReturnType<typeof vi.fn>).mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)

    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls.length).toBe(calls)
  })

  it('cancel:POST cancel 后刷新 run', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      POST: vi.fn(async () => okResponse({ run_id: 'r-1', status: 'stopping' })),
      GET: vi.fn(async () => okResponse({ ...RUN_RUNNING, status: 'stopping' })),
    })
    const store = useRunStore()

    await store.cancel('r-1', client)

    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/runs/{run_id}/cancel',
    )
    expect(store.run?.status).toBe('stopping')
  })

  it('lastEnvId:按项目记住上次环境', () => {
    setActivePinia(createPinia())
    const store = useRunStore()

    expect(store.lastEnvId(1)).toBeNull()
    store.rememberEnv(1, 42)
    expect(store.lastEnvId(1)).toBe(42)
    expect(store.lastEnvId(2)).toBeNull()
  })
})
