/**
 * 调度状态(项目作用域,计划详情页加载时按 plan 过滤,issue #36)。
 * load/create/update/remove/enable/disable 注入 client 供测试替换(前端设计 §5)。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type ScheduleOut = components['schemas']['ScheduleOut']
type ScheduleCreate = components['schemas']['ScheduleCreate']
type ScheduleUpdate = components['schemas']['ScheduleUpdate']

export type { ScheduleOut, ScheduleCreate, ScheduleUpdate }

export const useScheduleStore = defineStore('schedule', () => {
  const items = ref<ScheduleOut[]>([])
  const loading = ref(false)
  // 代际守卫:并发 load 时,只有最后一次发起的响应允许落盘
  let loadSeq = 0

  async function load(projectId: number, planId: number | null, client: ApiClient = api) {
    const seq = ++loadSeq
    loading.value = true
    try {
      const { data } = await client.GET('/api/v1/projects/{project_id}/schedules', {
        params: {
          path: { project_id: projectId },
          query: planId !== null ? { plan_id: planId } : undefined,
        },
      })
      if (seq === loadSeq) items.value = data ?? []
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  /** 新建调度:成功后重载列表;失败向上抛(组件层提示)。 */
  async function create(
    projectId: number,
    payload: ScheduleCreate,
    client: ApiClient = api,
  ): Promise<ScheduleOut | null> {
    const { data } = await client.POST('/api/v1/projects/{project_id}/schedules', {
      params: { path: { project_id: projectId } },
      body: payload,
    })
    if (!data) return null
    await load(projectId, null, client)
    return data
  }

  /** 编辑调度:成功后同步本地列表行(next_run_at 由服务端重算,以响应为准)。 */
  async function update(
    scheduleId: number,
    payload: ScheduleUpdate,
    client: ApiClient = api,
  ): Promise<ScheduleOut | null> {
    const { data } = await client.PATCH('/api/v1/schedules/{schedule_id}', {
      params: { path: { schedule_id: scheduleId } },
      body: payload,
    })
    if (!data) return null
    const index = items.value.findIndex((s) => s.id === scheduleId)
    if (index >= 0) items.value[index] = data
    return data
  }

  /** 删除调度:成功后重载列表。 */
  async function remove(projectId: number, scheduleId: number, client: ApiClient = api) {
    await client.DELETE('/api/v1/schedules/{schedule_id}', {
      params: { path: { schedule_id: scheduleId } },
    })
    await load(projectId, null, client)
  }

  /** 启停:响应当作权威行替换(next_run_at 语义在服务端)。 */
  async function setEnabled(
    scheduleId: number,
    enabled: boolean,
    client: ApiClient = api,
  ): Promise<ScheduleOut | null> {
    const path = enabled
      ? ('/api/v1/schedules/{schedule_id}/enable' as const)
      : ('/api/v1/schedules/{schedule_id}/disable' as const)
    const { data } = await client.POST(path, {
      params: { path: { schedule_id: scheduleId } },
    })
    if (!data) return null
    const index = items.value.findIndex((s) => s.id === scheduleId)
    if (index >= 0) items.value[index] = data
    return data
  }

  function reset() {
    items.value = []
  }

  return { items, loading, load, create, update, remove, setEnabled, reset }
})
