/**
 * 环境状态(当前项目作用域)。load/create/remove 注入 client 供测试替换。
 * selectedId 是变量面板的上下文;选中项失效时回落到第一个环境。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type EnvironmentOut = components['schemas']['EnvironmentOut']
type EnvironmentCreate = components['schemas']['EnvironmentCreate']
type EnvironmentUpdate = components['schemas']['EnvironmentUpdate']

export const useEnvironmentStore = defineStore('environment', () => {
  const items = ref<EnvironmentOut[]>([])
  const loading = ref(false)
  const selectedId = ref<number | null>(null)

  async function load(projectId: number, client: ApiClient = api) {
    loading.value = true
    try {
      const { data } = await client.GET('/api/v1/projects/{project_id}/environments', {
        params: { path: { project_id: projectId } },
      })
      items.value = data ?? []
      const stillThere = items.value.some((e) => e.id === selectedId.value)
      if (!stillThere) selectedId.value = items.value[0]?.id ?? null
    } finally {
      loading.value = false
    }
  }

  /** 新建环境:成功后重载并选中;失败向上抛(组件层提示 409 等)。 */
  async function create(projectId: number, payload: EnvironmentCreate, client: ApiClient = api) {
    const { data } = await client.POST('/api/v1/projects/{project_id}/environments', {
      params: { path: { project_id: projectId } },
      body: payload,
    })
    if (!data) return null
    await load(projectId, client)
    selectedId.value = data.id
    return data
  }

  /** 编辑环境:成功后用响应替换本地行(不整体重载)。 */
  async function update(id: number, payload: EnvironmentUpdate, client: ApiClient = api) {
    const { data } = await client.PATCH('/api/v1/environments/{environment_id}', {
      params: { path: { environment_id: id } },
      body: payload,
    })
    if (!data) return null
    const index = items.value.findIndex((e) => e.id === id)
    if (index >= 0) items.value[index] = data
    return data
  }

  /** 删除环境:成功后重载(选中随 load 回落)。 */
  async function remove(projectId: number, id: number, client: ApiClient = api) {
    await client.DELETE('/api/v1/environments/{environment_id}', {
      params: { path: { environment_id: id } },
    })
    await load(projectId, client)
  }

  return { items, loading, selectedId, load, create, update, remove }
})
