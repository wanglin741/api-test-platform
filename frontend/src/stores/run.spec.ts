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
  run_id: 'r-1',
  project_id: 1,
  case_id: 7,
  case_version: 2,
  environment_id: 3,
  trigger_mode: 'manual',
  status: 'running',
  duration_ms: null,
  summary: null,
  created_at: '2026-01-01T00:00:00Z',
  started_at: null,
  finished_at: null,
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
    const client = mockClient({
      POST: vi.fn(async () => okResponse({ run_id: 'r-1', status: 'pending' })),
    })

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
      id: 1,
      step_index: 0,
      step_name: '登录',
      step_type: 'http',
      status: 'passed',
      duration_ms: 12,
      request: null,
      response: null,
      extracted_variables: {},
      assertions: [],
      assertion_total: 0,
      assertion_passed: 0,
      error_message: null,
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

  it('listRuns:游标分页拉取报告列表,加载期间置 listLoading', async () => {
    setActivePinia(createPinia())
    let resolveGet!: (value: unknown) => void
    const client = mockClient({
      GET: vi.fn(
        () =>
          new Promise((resolve) => {
            resolveGet = resolve
          }),
      ),
    })
    const store = useRunStore()

    const pending = store.listRuns(1, client, 'cursor-1')
    expect(store.listLoading).toBe(true)

    resolveGet(okResponse({ items: [RUN_RUNNING], next_cursor: 'cursor-2' }))
    await pending

    expect(store.runItems).toEqual([RUN_RUNNING])
    expect(store.nextCursor).toBe('cursor-2')
    expect(store.listLoading).toBe(false)
    const [path, opts] = (client.GET as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { params: { query: Record<string, string> } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/runs')
    expect(opts.params.query).toEqual({ limit: 20, cursor: 'cursor-1' })
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

  it('轮询:run 未变化时跳过 steps 重取', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async (path: unknown) =>
        okResponse(
          String(path).includes('/steps') ? { items: [], count: 0, truncated: false } : RUN_RUNNING,
        ),
      ),
    })
    const store = useRunStore()

    store.startPolling('r-1', client)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(2000)

    const stepCalls = (client.GET as ReturnType<typeof vi.fn>).mock.calls.filter((call) =>
      String(call[0]).includes('/steps'),
    )
    const runCalls = (client.GET as ReturnType<typeof vi.fn>).mock.calls.length - stepCalls.length
    // 3 轮 run 拉取,steps 只在第一轮(run 刚出现)拉一次
    expect(runCalls).toBe(3)
    expect(stepCalls).toHaveLength(1)
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

  it('cancel:POST cancel 后刷新;停轮等到终态(stopped)才发生', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      POST: vi.fn(async () => okResponse({ run_id: 'r-1', status: 'stopping' })),
      GET: vi.fn(async (path: unknown) =>
        okResponse(
          String(path).includes('/steps')
            ? { items: [], count: 0, truncated: false }
            : { ...RUN_RUNNING, status: 'stopping' },
        ),
      ),
    })
    const store = useRunStore()

    await store.cancel('r-1', client)

    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/runs/{run_id}/cancel',
    )
    expect(store.run?.status).toBe('stopping')
    expect(store.polling).toBe(false) // cancel 本身没启动轮询

    // 轮询中取消:stopping 继续轮,拿到终态 stopped 后停
    store.startPolling('r-1', client)
    await vi.advanceTimersByTimeAsync(0)
    expect(store.polling).toBe(true)
    ;(client.GET as ReturnType<typeof vi.fn>).mockImplementation(async (path: unknown) =>
      okResponse(
        String(path).includes('/steps')
          ? { items: [], count: 0, truncated: false }
          : { ...RUN_RUNNING, status: 'stopped' },
      ),
    )
    await vi.advanceTimersByTimeAsync(2000)
    expect(store.polling).toBe(false)
  })

  it('reset:清空单例 run/steps,避免上一条报告残留', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ GET: vi.fn(async () => okResponse(RUN_RUNNING)) })
    const store = useRunStore()
    await store.refresh('r-1', client)
    expect(store.run).not.toBeNull()

    store.reset()

    expect(store.run).toBeNull()
    expect(store.steps).toEqual([])
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

/** 可编程的假事件源:记录 URL 与监听器,测试里手动派发事件。 */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  readonly url: string
  closed = false
  listeners = new Map<string, EventListener>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(name: string, fn: EventListener): void {
    this.listeners.set(name, fn)
  }

  removeEventListener(name: string): void {
    this.listeners.delete(name)
  }

  close(): void {
    this.closed = true
  }

  listenerNames(): string[] {
    return [...this.listeners.keys()]
  }

  emit(name: string, data: unknown): void {
    this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data) }))
  }
}

describe('runStore.startStream', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    FakeEventSource.instances = []
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function streamClient() {
    return mockClient({
      GET: vi.fn(async (path: unknown) =>
        okResponse(
          String(path).includes('/steps') ? { items: [], count: 0, truncated: false } : RUN_RUNNING,
        ),
      ),
    })
  }

  it('startStream:先 refresh 打底再开流,注册 run.started/run.finished 监听', async () => {
    setActivePinia(createPinia())
    const client = streamClient()
    const store = useRunStore()

    await store.startStream('r-1', client, (url) => new FakeEventSource(url))

    expect(store.run?.status).toBe('running')
    expect(store.streaming).toBe(true)
    expect(store.polling).toBe(false)
    const source = FakeEventSource.instances[0]!
    expect(source.url).toBe('/api/v1/runs/r-1/events')
    expect([...source.listeners.keys()]).toEqual(
      expect.arrayContaining(['run.started', 'run.finished']),
    )
  })

  it('run.started:补状态与开始时间', async () => {
    setActivePinia(createPinia())
    const store = useRunStore()
    await store.startStream('r-1', streamClient(), (url) => new FakeEventSource(url))

    FakeEventSource.instances[0]!.emit('run.started', {
      total_steps: 3,
      started_at: '2026-01-01T00:00:05Z',
    })

    expect(store.run?.status).toBe('running')
    expect(store.run?.started_at).toBe('2026-01-01T00:00:05Z')
  })

  it('run.finished:停流并重拉权威行(数据库行替换事件合成行)', async () => {
    setActivePinia(createPinia())
    // 第 1 次 run GET 是打底(running),终态重拉返回数据库权威行
    const AUTHORITATIVE = {
      ...RUN_RUNNING,
      status: 'success',
      summary: { total: 2, passed: 2 },
    }
    let runFetches = 0
    const client = mockClient({
      GET: vi.fn(async (path: unknown) =>
        okResponse(
          String(path).includes('/steps')
            ? { items: [], count: 0, truncated: false }
            : ++runFetches === 1
              ? RUN_RUNNING
              : AUTHORITATIVE,
        ),
      ),
    })
    const store = useRunStore()
    await store.startStream('r-1', client, (url) => new FakeEventSource(url))

    FakeEventSource.instances[0]!.emit('run.finished', {
      status: 'success',
      summary: { total: 2, passed: 2 },
    })
    await vi.advanceTimersByTimeAsync(0)

    expect(store.run?.status).toBe('success')
    expect(store.run?.summary).toEqual(AUTHORITATIVE.summary)
    expect(store.streaming).toBe(false)
    expect(FakeEventSource.instances[0]!.closed).toBe(true)
    expect(runFetches).toBe(2) // 打底 1 次 + 终态重拉 1 次
  })

  it('执行期事件:运行中步骤、幂等 upsert、进度推进', async () => {
    setActivePinia(createPinia())
    const store = useRunStore()
    await store.startStream('r-1', streamClient(), (url) => new FakeEventSource(url))
    const source = FakeEventSource.instances[0]!

    source.emit('run.started', { total_steps: 3, started_at: '2026-01-01T00:00:05Z' })
    expect(store.progress).toEqual({ completed: 0, total: 3 })

    source.emit('step.started', { step_index: 0, step_name: '登录', step_type: 'http' })
    expect(store.runningStep).toEqual({ stepIndex: 0, stepName: '登录', stepType: 'http' })

    // 运行期合成行:摘要字段(明细终态重拉后由数据库行补齐)
    source.emit('step.finished', {
      step_index: 0,
      step_name: '登录',
      status: 'passed',
      duration_ms: 12,
      assertion_total: 2,
      assertion_passed: 2,
    })
    expect(store.steps).toHaveLength(1)
    expect(store.steps[0]).toMatchObject({
      step_index: 0,
      status: 'passed',
      assertion_total: 2,
    })
    expect(store.runningStep).toBeNull() // 步骤结束,不再有运行中的步骤

    // 重复投递同一步骤:幂等 upsert,不出现重复行
    source.emit('step.finished', {
      step_index: 0,
      step_name: '登录',
      status: 'passed',
      duration_ms: 13,
      assertion_total: 2,
      assertion_passed: 2,
    })
    expect(store.steps).toHaveLength(1)
    expect(store.steps[0]?.duration_ms).toBe(13)

    source.emit('run.progress', { completed: 1, total: 3 })
    expect(store.progress).toEqual({ completed: 1, total: 3 })
  })

  it('reset:清空运行期进度与运行中步骤', async () => {
    setActivePinia(createPinia())
    const store = useRunStore()
    await store.startStream('r-1', streamClient(), (url) => new FakeEventSource(url))
    const source = FakeEventSource.instances[0]!
    source.emit('run.started', { total_steps: 2, started_at: null })
    source.emit('step.started', { step_index: 0, step_name: 's', step_type: 'http' })

    store.reset()

    expect(store.progress).toBeNull()
    expect(store.runningStep).toBeNull()
  })

  it('打开已终态的执行:不开流也不轮询', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async (path: unknown) =>
        okResponse(
          String(path).includes('/steps')
            ? { items: [], count: 0, truncated: false }
            : { ...RUN_RUNNING, status: 'success' },
        ),
      ),
    })
    const store = useRunStore()

    await store.startStream('r-1', client, (url) => new FakeEventSource(url))

    expect(FakeEventSource.instances).toHaveLength(0)
    expect(store.polling).toBe(false)
    expect(store.streaming).toBe(false)
  })

  it('无原生 EventSource(默认工厂在 jsdom):直接降级轮询', async () => {
    setActivePinia(createPinia())
    const client = streamClient()
    const store = useRunStore()

    await store.startStream('r-1', client)

    expect(store.polling).toBe(true)
    expect(store.streaming).toBe(false)
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('stopStream:先摘回调再关闭,迟到事件不再改变状态', async () => {
    setActivePinia(createPinia())
    const store = useRunStore()
    await store.startStream('r-1', streamClient(), (url) => new FakeEventSource(url))
    const source = FakeEventSource.instances[0]!

    store.stopStream()

    expect(source.closed).toBe(true)
    expect(store.streaming).toBe(false)
    source.emit('run.finished', { status: 'success', summary: { total: 1 } })
    expect(store.run?.status).toBe('running') // 迟到事件被忽略
  })

  it('首刷 GET 失败:降级轮询自愈,不搁浅在空页', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => {
        throw new Error('boom')
      }),
    })
    const store = useRunStore()

    await store.startStream('r-1', client, (url) => new FakeEventSource(url))

    expect(store.polling).toBe(true)
    expect(store.streaming).toBe(false)
  })

  it('await 打底期间订阅被替换(切换报告):旧订阅不开流、不写状态', async () => {
    setActivePinia(createPinia())
    let resolveA!: (value: unknown) => void
    const pending = new Promise((resolve) => {
      resolveA = resolve
    })
    // 两个并发 GET 共享同一个挂起 promise:模拟打底尚未返回
    const client = mockClient({ GET: vi.fn(() => pending) })
    const store = useRunStore()

    const pendingA = store.startStream('r-a', client, (url) => new FakeEventSource(url))
    store.stopStream() // 视图卸载/切换,B 的 startStream 也会先 stop
    resolveA(okResponse(RUN_RUNNING))
    await pendingA

    expect(FakeEventSource.instances).toHaveLength(0)
    expect(store.streaming).toBe(false)
    expect(store.run).toBeNull() // 旧 run 的数据不落地
  })
})
