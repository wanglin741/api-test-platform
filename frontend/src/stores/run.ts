import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'

import type { ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type RunOut = components['schemas']['RunOut']
type StepOut = components['schemas']['StepOut']

/** 与后端 RUN_TERMINAL_STATUSES 一致;轮询见到这些状态就停。 */
export const TERMINAL_STATUSES = new Set(['success', 'failed', 'error', 'stopped'])

const PAGE_SIZE = 20

function lastEnvKey(projectId: number): string {
  return `apitest.last-env-${projectId}`
}

/** 执行触发 + 报告详情(run + 步骤明细),含 2s 轮询与取消(前端设计 §6)。 */
export const useRunStore = defineStore('run', () => {
  const run = ref<RunOut | null>(null)
  const steps = ref<StepOut[]>([])
  const stepsTruncated = ref(false)
  const polling = ref(false)

  // 报告列表页:游标分页(API设计 §分页,只支持下一页)
  const runItems = ref<RunOut[]>([])
  const nextCursor = ref<string | null>(null)
  const listLoading = ref(false)

  async function listRuns(
    projectId: number,
    client: ApiClient = api,
    cursor: string | null = null,
    limit = PAGE_SIZE,
  ): Promise<void> {
    listLoading.value = true
    try {
      // 先清旧行:请求失败或切换项目时,不在新上下文里显示上一次的列表
      runItems.value = []
      nextCursor.value = null
      const query: Record<string, unknown> = { limit }
      if (cursor !== null) query.cursor = cursor
      const { data } = await client.GET('/api/v1/projects/{project_id}/runs', {
        params: { path: { project_id: projectId }, query },
      })
      runItems.value = data?.items ?? []
      nextCursor.value = data?.next_cursor ?? null
    } finally {
      listLoading.value = false
    }
  }

  async function trigger(
    projectId: number,
    caseId: number,
    environmentId: number,
    client: ApiClient = api,
  ): Promise<string | null> {
    const { data } = await client.POST('/api/v1/projects/{project_id}/runs', {
      params: { path: { project_id: projectId } },
      body: { case_id: caseId, environment_id: environmentId },
    })
    return data?.run_id ?? null
  }

  async function fetchRun(runId: string, client: ApiClient = api) {
    const { data } = await client.GET('/api/v1/runs/{run_id}', {
      params: { path: { run_id: runId } },
    })
    return data ?? null
  }

  async function fetchSteps(runId: string, client: ApiClient = api) {
    const { data } = await client.GET('/api/v1/runs/{run_id}/steps', {
      params: { path: { run_id: runId } },
    })
    return { items: data?.items ?? [], truncated: data?.truncated ?? false }
  }

  async function loadRun(runId: string, client: ApiClient = api): Promise<void> {
    run.value = await fetchRun(runId, client)
  }

  async function loadSteps(runId: string, client: ApiClient = api): Promise<void> {
    const result = await fetchSteps(runId, client)
    steps.value = result.items
    stepsTruncated.value = result.truncated
  }

  async function refresh(runId: string, client: ApiClient = api): Promise<void> {
    await Promise.all([loadRun(runId, client), loadSteps(runId, client)])
  }

  /** 报告详情是单例:进入新 run 前清掉旧报告,避免渲染残留/误取消。 */
  function reset(): void {
    run.value = null
    steps.value = []
    stepsTruncated.value = false
    progress.value = null
    runningStep.value = null
  }

  let timer: ReturnType<typeof setInterval> | null = null
  let lastRunJson: string | null = null

  async function pollOnce(runId: string, client: ApiClient): Promise<void> {
    // 单次轮询失败(网络抖动)不终止轮询:吞掉,下一跳自愈
    try {
      await loadRun(runId, client)
      // run 未变化时跳过 steps 重取(前端设计 §5:合并轮询,变更才重取)
      const current = run.value === null ? null : JSON.stringify(run.value)
      if (current !== lastRunJson) {
        lastRunJson = current
        await loadSteps(runId, client)
      }
      if (run.value !== null && TERMINAL_STATUSES.has(run.value.status)) stopPolling()
    } catch {
      return
    }
  }

  /** 立即刷新一次,非终态则每 intervalMs 再刷;终态自动停。 */
  function startPolling(runId: string, client: ApiClient = api, intervalMs = 2000): void {
    stopPolling()
    lastRunJson = null
    polling.value = true
    void pollOnce(runId, client)
    timer = setInterval(() => void pollOnce(runId, client), intervalMs)
  }

  function stopPolling(): void {
    if (timer !== null) clearInterval(timer)
    timer = null
    polling.value = false
  }

  async function cancel(runId: string, client: ApiClient = api): Promise<void> {
    await client.POST('/api/v1/runs/{run_id}/cancel', { params: { path: { run_id: runId } } })
    await refresh(runId, client)
  }

  // ── SSE 实时进度(架构设计 §6):事件流为主,轮询降级 ────────────────
  const streaming = ref(false)
  // 运行期状态:进度(已完成/总数)与正在执行的步骤 —— 只由事件驱动,
  // 统计卡仍只由汇总数据驱动(不从步骤事件合成)
  const progress = ref<{ completed: number; total: number } | null>(null)
  const runningStep = ref<{ stepIndex: number; stepName: string; stepType: string } | null>(null)
  let source: StreamSource | null = null
  let streamHandlers: Array<[string, EventListener]> = []
  // 当前订阅的 run(null = 无活跃订阅)。await 恢复点与事件回调都校验它:
  // 切换/关闭报告后,旧的 await 恢复与迟到事件不得写进新报告的状态
  let streamRunId: string | null = null

  /** 事件源只需监听/关闭这一子集:原生 EventSource 天然满足,
   * 测试注入假事件源(完整 EventSource 接口在 jsdom 里没有)。 */
  interface StreamSource {
    addEventListener(type: string, listener: EventListener): void
    removeEventListener(type: string, listener: EventListener): void
    close(): void
  }
  type EventSourceFactory = (url: string) => StreamSource

  function defaultEventSourceFactory(url: string): EventSource {
    return new EventSource(url)
  }

  function handleRunStarted(payload: { started_at: string | null; total_steps: number }): void {
    if (run.value === null) return
    run.value = { ...run.value, status: 'running', started_at: payload.started_at }
    progress.value = { completed: 0, total: payload.total_steps }
  }

  /** step.finished 按 step_index 幂等 upsert:快照路径下可能重复投递,无害。
   * 运行期合成行只有摘要字段(请求/响应明细终态重拉后由数据库行补齐)。 */
  function upsertStep(payload: {
    step_index: number
    step_name: string
    status: string
    duration_ms: number | null
    assertion_total: number
    assertion_passed: number
  }): void {
    const row: StepOut = {
      // 负数哨兵 id:行 key 是 step_index,id 只需不与真实行冲突
      id: -(payload.step_index + 1),
      step_index: payload.step_index,
      step_name: payload.step_name,
      step_type: runningStep.value?.stepType ?? 'http',
      status: payload.status,
      duration_ms: payload.duration_ms ?? 0,
      request: null,
      response: null,
      extracted_variables: {},
      assertions: [],
      assertion_total: payload.assertion_total,
      assertion_passed: payload.assertion_passed,
      error_message: null,
    }
    const idx = steps.value.findIndex((s) => s.step_index === payload.step_index)
    if (idx >= 0) steps.value.splice(idx, 1, row)
    else steps.value.push(row)
    runningStep.value = null
  }

  /** run.snapshot 整体替换执行与步骤(服务端从数据库读出的权威现状,
   * 首连/重连游标失效时到达):终态则停流,快照本身即权威,无需重拉。 */
  function handleSnapshot(payload: {
    run: RunOut
    steps: StepOut[]
    steps_truncated: boolean
  }): void {
    run.value = payload.run
    steps.value = payload.steps
    stepsTruncated.value = payload.steps_truncated
    if (TERMINAL_STATUSES.has(payload.run.status)) {
      runningStep.value = null
      progress.value = null
      stopStream()
    }
  }

  function handleRunFinished(
    runId: string,
    client: ApiClient,
    payload: { status: string; summary: RunOut['summary'] },
  ): void {
    if (run.value !== null) {
      run.value = { ...run.value, status: payload.status, summary: payload.summary }
    }
    runningStep.value = null
    progress.value = null
    stopStream()
    // 重拉一次:用数据库权威行替换运行期依据事件合成的状态
    void refresh(runId, client)
  }

  /** 订阅报告详情的事件流:先 refresh 打底(已终态则不必订阅),
   * 无原生 EventSource 的环境(jsdom)直接降级为轮询。 */
  async function startStream(
    runId: string,
    client: ApiClient = api,
    eventSourceFactory: EventSourceFactory = defaultEventSourceFactory,
  ): Promise<void> {
    stopStream()
    stopPolling()
    streamRunId = runId
    try {
      // 先取数、守卫后再写:无条件写入会在 await 期间竞态,
      // 把已切换/卸载的旧 run 数据写进新报告的状态
      const [runRow, stepRows] = await Promise.all([
        fetchRun(runId, client),
        fetchSteps(runId, client),
      ])
      if (streamRunId !== runId) return // await 期间订阅已被停止/替换(如切换报告)
      run.value = runRow
      steps.value = stepRows.items
      stepsTruncated.value = stepRows.truncated
    } catch {
      // 首刷失败也要留下能自愈的通道:轮询的下一跳会重试,不能搁浅在空页
      if (streamRunId === runId) startPolling(runId, client)
      return
    }
    if (run.value !== null && TERMINAL_STATUSES.has(run.value.status)) return

    if (eventSourceFactory === defaultEventSourceFactory && typeof EventSource === 'undefined') {
      startPolling(runId, client)
      return
    }

    const es = eventSourceFactory(`/api/v1/runs/${runId}/events`)
    source = es
    streaming.value = true
    const onStarted = (e: MessageEvent) => {
      if (streamRunId === runId) handleRunStarted(JSON.parse(e.data))
    }
    const onStepStarted = (e: MessageEvent) => {
      if (streamRunId !== runId) return
      const payload: { step_index: number; step_name: string; step_type: string } = JSON.parse(
        e.data,
      )
      runningStep.value = {
        stepIndex: payload.step_index,
        stepName: payload.step_name,
        stepType: payload.step_type,
      }
    }
    const onStepFinished = (e: MessageEvent) => {
      if (streamRunId === runId) upsertStep(JSON.parse(e.data))
    }
    const onProgress = (e: MessageEvent) => {
      if (streamRunId === runId) progress.value = JSON.parse(e.data)
    }
    const onFinished = (e: MessageEvent) => {
      if (streamRunId === runId) handleRunFinished(runId, client, JSON.parse(e.data))
    }
    streamHandlers = [
      [
        'run.snapshot',
        ((e: MessageEvent) => {
          if (streamRunId === runId) handleSnapshot(JSON.parse(e.data))
        }) as EventListener,
      ],
      ['run.started', onStarted as EventListener],
      ['step.started', onStepStarted as EventListener],
      ['step.finished', onStepFinished as EventListener],
      ['run.progress', onProgress as EventListener],
      ['run.finished', onFinished as EventListener],
    ]
    for (const [name, handler] of streamHandlers) es.addEventListener(name, handler)
  }

  /** 先摘回调再关闭:关闭后迟到的错误/事件不改变状态。 */
  function stopStream(): void {
    streamRunId = null
    if (source === null) return
    const es = source
    source = null
    for (const [name, handler] of streamHandlers) es.removeEventListener(name, handler)
    streamHandlers = []
    es.close()
    streaming.value = false
  }

  // ── 上次执行用的环境:弹窗默认选中(按项目记) ────────────────────
  function lastEnvId(projectId: number): number | null {
    const raw = localStorage.getItem(lastEnvKey(projectId))
    return raw === null ? null : Number(raw)
  }

  function rememberEnv(projectId: number, envId: number): void {
    localStorage.setItem(lastEnvKey(projectId), String(envId))
  }

  return {
    run,
    steps,
    stepsTruncated,
    polling,
    streaming,
    progress,
    runningStep,
    runItems,
    nextCursor,
    listLoading,
    listRuns,
    trigger,
    loadRun,
    loadSteps,
    refresh,
    reset,
    startPolling,
    stopPolling,
    startStream,
    stopStream,
    cancel,
    lastEnvId,
    rememberEnv,
  }
})
