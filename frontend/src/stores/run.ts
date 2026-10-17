import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'

import type { ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type RunOut = components['schemas']['RunOut']
type StepOut = components['schemas']['StepOut']

/** 与后端 RUN_TERMINAL_STATUSES 一致;轮询见到这些状态就停。 */
const TERMINAL_STATUSES = new Set(['success', 'failed', 'error', 'stopped'])

function lastEnvKey(projectId: number): string {
  return `apitest.last-env-${projectId}`
}

/** 执行触发 + 报告详情(run + 步骤明细),含 2s 轮询与取消(前端设计 §6)。 */
export const useRunStore = defineStore('run', () => {
  const run = ref<RunOut | null>(null)
  const steps = ref<StepOut[]>([])
  const stepsTruncated = ref(false)
  const polling = ref(false)

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

  async function loadRun(runId: string, client: ApiClient = api): Promise<void> {
    const { data } = await client.GET('/api/v1/runs/{run_id}', {
      params: { path: { run_id: runId } },
    })
    run.value = data ?? null
  }

  async function loadSteps(runId: string, client: ApiClient = api): Promise<void> {
    const { data } = await client.GET('/api/v1/runs/{run_id}/steps', {
      params: { path: { run_id: runId } },
    })
    steps.value = data?.items ?? []
    stepsTruncated.value = data?.truncated ?? false
  }

  async function refresh(runId: string, client: ApiClient = api): Promise<void> {
    await Promise.all([loadRun(runId, client), loadSteps(runId, client)])
  }

  let timer: ReturnType<typeof setInterval> | null = null

  async function pollOnce(runId: string, client: ApiClient): Promise<void> {
    await refresh(runId, client)
    if (run.value !== null && TERMINAL_STATUSES.has(run.value.status)) stopPolling()
  }

  /** 立即刷新一次,非终态则每 intervalMs 再刷;终态自动停。 */
  function startPolling(runId: string, client: ApiClient = api, intervalMs = 2000): void {
    stopPolling()
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
    trigger,
    loadRun,
    loadSteps,
    refresh,
    startPolling,
    stopPolling,
    cancel,
    lastEnvId,
    rememberEnv,
  }
})
