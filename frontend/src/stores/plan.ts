/**
 * 测试计划状态(当前项目作用域)。load/create/update/remove/loadDetail/saveItems
 * 注入 client 供测试替换(issue #29)。current + planItems 是详情页的上下文。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type PlanOut = components['schemas']['PlanOut']
type PlanCreate = components['schemas']['PlanCreate']
type PlanUpdate = components['schemas']['PlanUpdate']
type PlanItemOut = components['schemas']['PlanItemOut']

export type { PlanOut, PlanCreate, PlanUpdate, PlanItemOut }

export const usePlanStore = defineStore('plan', () => {
  const items = ref<PlanOut[]>([])
  const loading = ref(false)
  const current = ref<PlanOut | null>(null)
  const planItems = ref<PlanItemOut[]>([])

  async function load(projectId: number, client: ApiClient = api) {
    loading.value = true
    try {
      const { data } = await client.GET('/api/v1/projects/{project_id}/plans', {
        params: { path: { project_id: projectId } },
      })
      items.value = data ?? []
    } finally {
      loading.value = false
    }
  }

  /** 新建计划:成功后重载列表;失败向上抛(组件层提示)。 */
  async function create(projectId: number, payload: PlanCreate, client: ApiClient = api) {
    const { data } = await client.POST('/api/v1/projects/{project_id}/plans', {
      params: { path: { project_id: projectId } },
      body: payload,
    })
    if (!data) return null
    await load(projectId, client)
    return data
  }

  /** 编辑计划:成功后同步本地列表行与详情行(不整体重载)。 */
  async function update(planId: number, payload: PlanUpdate, client: ApiClient = api) {
    const { data } = await client.PATCH('/api/v1/plans/{plan_id}', {
      params: { path: { plan_id: planId } },
      body: payload,
    })
    if (!data) return null
    const index = items.value.findIndex((p) => p.id === planId)
    if (index >= 0) items.value[index] = data
    if (current.value?.id === planId) current.value = data
    return data
  }

  /** 删除计划:成功后重载列表;详情上下文一并清掉。 */
  async function remove(projectId: number, planId: number, client: ApiClient = api) {
    await client.DELETE('/api/v1/plans/{plan_id}', { params: { path: { plan_id: planId } } })
    if (current.value?.id === planId) {
      current.value = null
      planItems.value = []
    }
    await load(projectId, client)
  }

  /** 详情:计划 + 清单一起取,切换计划时整组替换。 */
  async function loadDetail(planId: number, client: ApiClient = api) {
    const [plan, planItemsResp] = await Promise.all([
      client.GET('/api/v1/plans/{plan_id}', { params: { path: { plan_id: planId } } }),
      client.GET('/api/v1/plans/{plan_id}/items', { params: { path: { plan_id: planId } } }),
    ])
    current.value = plan.data ?? null
    planItems.value = planItemsResp.data ?? []
    return current.value
  }

  /** 清单整体替换:caseIds 数组顺序即执行顺序。 */
  async function saveItems(planId: number, caseIds: number[], client: ApiClient = api) {
    const { data } = await client.PUT('/api/v1/plans/{plan_id}/items', {
      params: { path: { plan_id: planId } },
      body: { items: caseIds.map((case_id) => ({ case_id })) },
    })
    planItems.value = data ?? []
    // item_count 变了,同步列表行,省一次重载
    const index = items.value.findIndex((p) => p.id === planId)
    if (index >= 0 && current.value) {
      items.value[index] = { ...items.value[index]!, item_count: planItems.value.length }
    }
    return planItems.value
  }

  function reset() {
    items.value = []
    current.value = null
    planItems.value = []
  }

  return {
    items,
    loading,
    current,
    planItems,
    load,
    create,
    update,
    remove,
    loadDetail,
    saveItems,
    reset,
  }
})
