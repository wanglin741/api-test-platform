import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api/client'
import { downloadText, junitFileName } from '@/utils/format'

import type { ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type RunOut = components['schemas']['RunOut']
type StepOut = components['schemas']['StepOut']

/** 计划报告的用例分组行:组头展示用例名 + 状态,组内是既有步骤行。 */
export interface CaseGroupView {
  caseIndex: number
  caseName: string
  status: string
  steps: StepOut[]
}

/** run.summary.cases 的一行(数据模型 §run.summary,计划执行附加)。 */
interface PlanCaseSummary {
  case_index: number
  case_id: number | null
  case_name: string
  status: string
  total: number
  passed: number
  failed: number
  error: number
  skipped: number
}

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
    planId: number | null = null,
  ): Promise<void> {
    listLoading.value = true
    try {
      // 先清旧行:请求失败或切换项目时,不在新上下文里显示上一次的列表
      runItems.value = []
      nextCursor.value = null
      const query: Record<string, unknown> = { limit }
      if (cursor !== null) query.cursor = cursor
      if (planId !== null) query.plan_id = planId
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

  /** 计划触发动线(前端设计 §6.4):弹窗选环境后从计划详情页一键触发。 */
  async function triggerPlan(
    projectId: number,
    planId: number,
    environmentId: number,
    client: ApiClient = api,
  ): Promise<string | null> {
    const { data } = await client.POST('/api/v1/projects/{project_id}/runs', {
      params: { path: { project_id: projectId } },
      body: { plan_id: planId, environment_id: environmentId },
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
    // epoch 守卫:await 期间报告被切换/重置(reset/startStream 递增)的话,
    // 旧请求的迟到响应不得写进新报告(loadRun/loadSteps 是全部远端写入口)
    const epochAtStart = epoch
    const data = await fetchRun(runId, client)
    if (epochAtStart === epoch) run.value = data
  }

  async function loadSteps(runId: string, client: ApiClient = api): Promise<void> {
    const epochAtStart = epoch
    const result = await fetchSteps(runId, client)
    if (epochAtStart !== epoch) return
    steps.value = result.items
    stepsTruncated.value = result.truncated
  }

  async function refresh(runId: string, client: ApiClient = api): Promise<void> {
    await Promise.all([loadRun(runId, client), loadSteps(runId, client)])
  }

  /** 报告详情是单例:进入新 run 前清掉旧报告,避免渲染残留/误取消。 */
  function reset(): void {
    epoch += 1
    run.value = null
    steps.value = []
    stepsTruncated.value = false
    progress.value = null
    runningStep.value = null
    caseNames.value = new Map()
  }

  let timer: ReturnType<typeof setInterval> | null = null
  let lastRunJson: string | null = null
  // 报告代数:reset/startStream 各自递增;所有远端写回(loadRun/loadSteps)
  // 在 await 恢复点校验,迟到的旧响应不能覆盖当前打开的报告
  let epoch = 0

  /** 计划报告的用例分组视图(前端设计 §6.3)。单用例执行返回 null ——
   * 组件渲染平铺列表,与改造前一致。
   *
   * 两个数据来源:终态 summary.cases 是权威名单(含被跳用例 —— 它们没有
   * 步骤行,不记组就「凭空消失」);运行期 summary 未落库,从步骤行聚合,
   * 用例名取 step 事件携带的 case_name。 */
  const caseGroups = computed<CaseGroupView[] | null>(() => {
    const current = run.value
    if (current === null || current.plan_id === null || current.plan_id === undefined) return null
    const summaryCases = (current.summary as { cases?: PlanCaseSummary[] } | null | undefined)
      ?.cases
    if (Array.isArray(summaryCases) && summaryCases.length > 0) {
      return summaryCases.map((c) => ({
        caseIndex: c.case_index,
        caseName: c.case_name,
        status: c.status,
        steps: steps.value.filter((s) => (s.case_index ?? 0) === c.case_index),
      }))
    }
    const groups: CaseGroupView[] = []
    const byIndex = new Map<number, CaseGroupView>()
    for (const s of steps.value) {
      const ci = s.case_index ?? 0
      let group = byIndex.get(ci)
      if (group === undefined) {
        group = {
          caseIndex: ci,
          caseName: caseNames.value.get(ci) ?? `用例 #${s.case_id ?? ci}`,
          status: 'running',
          steps: [],
        }
        byIndex.set(ci, group)
        groups.push(group)
      }
      group.steps.push(s)
    }
    return groups
  })

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

  /** 导出终态 JUnit(前端设计 §6.9):文本响应转 Blob 下载,
   * 文件名优先取服务端 Content-Disposition,缺失回退 run-{id}.xml。 */
  async function exportJunit(runId: string, client: ApiClient = api): Promise<void> {
    const { data, response } = await client.GET('/api/v1/runs/{run_id}/export', {
      params: { path: { run_id: runId }, query: { format: 'junit' } },
      parseAs: 'text',
    })
    downloadText(data ?? '', junitFileName(response.headers.get('Content-Disposition'), runId))
  }

  // ── SSE 实时进度(架构设计 §6):事件流为主,轮询降级 ────────────────
  // 连续 3 次连接失败降级轮询;onopen 清零,避免「连上又断」误判累计
  const MAX_STREAM_FAILURES = 3
  const streaming = ref(false)
  // 运行期状态:进度(已完成/总数)与正在执行的步骤 —— 只由事件驱动,
  // 统计卡仍只由汇总数据驱动(不从步骤事件合成)
  const progress = ref<{ completed: number; total: number } | null>(null)
  const runningStep = ref<{
    stepIndex: number
    stepName: string
    stepType: string
    /** 计划执行的用例归属;单用例执行不带 */
    caseIndex?: number
    caseName?: string
  } | null>(null)
  // 计划执行的用例名(按 case_index 记,来自 step 事件):运行期分组展示用;
  // 终态由 summary.cases 提供权威名单(含被跳用例)
  const caseNames = ref<Map<number, string>>(new Map())
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

  /** step.finished 按 step_index 幂等 upsert:快照路径下可能重复投递。
   * 已有行合并而非整行替换 —— 服务端先取流尾再读数据库,快照里已含
   * 明细的落库行可能随后被同一步的重放事件盖成摘要行;明细字段(request/
   * response/断言/提取)以已有行为准,摘要字段(状态/耗时/断言数)以事件为准。
   * 运行期新合成行只有摘要字段,请求/响应明细终态重拉后由数据库行补齐。 */
  function upsertStep(payload: {
    step_index: number
    step_name: string
    step_type?: string
    status: string
    duration_ms: number | null
    assertion_total: number
    assertion_passed: number
    /** 计划执行的用例归属(#30):单用例事件不带,行取缺省 0 */
    case_index?: number
    case_id?: number | null
    case_name?: string
  }): void {
    const caseIndex = payload.case_index ?? 0
    if (payload.case_name !== undefined) caseNames.value.set(caseIndex, payload.case_name)
    // 行 key 是 (用例序号, 步骤序号) 复合:计划执行里 step_index 跨用例重复,
    // 单用例执行 case_index 恒 0,两种模式统一
    const idx = steps.value.findIndex(
      (s) => (s.case_index ?? 0) === caseIndex && s.step_index === payload.step_index,
    )
    const prev = idx >= 0 ? steps.value[idx] : undefined
    const row: StepOut = {
      // 负数哨兵 id:key 是复合键,id 只需不与真实行冲突
      id: prev?.id ?? -(caseIndex * 100000 + payload.step_index + 1),
      step_index: payload.step_index,
      step_name: payload.step_name,
      // 跳过的步骤没有 step.started:类型以事件载荷为准(引擎随 finished 下发)
      step_type: payload.step_type ?? runningStep.value?.stepType ?? prev?.step_type ?? 'http',
      status: payload.status,
      duration_ms: payload.duration_ms ?? 0,
      case_index: caseIndex,
      case_id: payload.case_id ?? prev?.case_id ?? null,
      request: prev?.request ?? null,
      response: prev?.response ?? null,
      extracted_variables: prev?.extracted_variables ?? {},
      assertions: prev?.assertions ?? [],
      assertion_total: payload.assertion_total,
      assertion_passed: payload.assertion_passed,
      error_message: prev?.error_message ?? null,
    }
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
      stopStream() // 终态:快照即权威,停流(运行期状态随 stopStream 清除)且不重拉
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
    stopStream()
    // 重拉一次:用数据库权威行替换运行期依据事件合成的状态。
    // epoch 已随 stopStream 递增,这里的在途重拉若遇切换/卸载,写回被守卫拦截
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
      const payload: {
        step_index: number
        step_name: string
        step_type: string
        case_index?: number
        case_name?: string
      } = JSON.parse(e.data)
      if (payload.case_index !== undefined && payload.case_name !== undefined) {
        caseNames.value.set(payload.case_index, payload.case_name)
      }
      runningStep.value = {
        stepIndex: payload.step_index,
        stepName: payload.step_name,
        stepType: payload.step_type,
        caseIndex: payload.case_index,
        caseName: payload.case_name,
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
    // 连接失败累计:连上(open)即清零,「连上又断」不算连续失败
    let failures = 0
    const onOpen = () => {
      failures = 0
    }
    const onError = () => {
      if (streamRunId !== runId) return
      failures += 1
      if (failures < MAX_STREAM_FAILURES) return
      // 降级:流与轮询互斥,先停流再启轮询,页面功能不中断
      stopStream()
      startPolling(runId, client)
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
      ['open', onOpen as EventListener],
      ['error', onError as EventListener],
    ]
    for (const [name, handler] of streamHandlers) es.addEventListener(name, handler)
  }

  /** 先摘回调再关闭:关闭后迟到的错误/事件不改变状态。
   * 递增 epoch:卸载/收尾后,handleRunFinished 触发的重拉等在途请求
   * 的迟到响应不再写回状态(如终态后用户已切到别的报告)。
   * 同时清运行期状态(progress/runningStep 只由事件流维护,流停了
   * 就该消失 —— 降级轮询后残留会冻结成一个永远走不完的进度条)。 */
  function stopStream(): void {
    epoch += 1
    streamRunId = null
    progress.value = null
    runningStep.value = null
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
    caseGroups,
    runItems,
    nextCursor,
    listLoading,
    listRuns,
    trigger,
    triggerPlan,
    loadRun,
    loadSteps,
    refresh,
    reset,
    startPolling,
    stopPolling,
    startStream,
    stopStream,
    cancel,
    exportJunit,
    lastEnvId,
    rememberEnv,
  }
})
