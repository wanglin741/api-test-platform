/**
 * API Token 状态(项目作用域,issue #42;前端设计 §5/§6.8)。
 *
 * 明文只在创建响应里返回一次:`create` 把它交给调用方即止,绝不写进
 * 任何 store 状态 —— 列表(items)只承载 ApiTokenOut(无 token 字段)。
 * load 带代际守卫,先例见 schedule store。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type ApiClient } from '@/api/client'

import type { components } from '@/api/schema'

type ApiTokenOut = components['schemas']['ApiTokenOut']
type ApiTokenCreated = components['schemas']['ApiTokenCreated']
type ApiTokenCreate = components['schemas']['ApiTokenCreate']

export type { ApiTokenOut, ApiTokenCreated, ApiTokenCreate }

export const useApiTokenStore = defineStore('apiToken', () => {
  const items = ref<ApiTokenOut[]>([])
  const loading = ref(false)
  let loadSeq = 0

  async function load(projectId: number, client: ApiClient = api) {
    const seq = ++loadSeq
    loading.value = true
    try {
      const { data } = await client.GET('/api/v1/projects/{project_id}/api-tokens', {
        params: { path: { project_id: projectId } },
      })
      if (seq === loadSeq) items.value = data ?? []
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  /** 创建 Token:一次性明文随返回值交给组件展示,不进入 store 状态。 */
  async function create(
    projectId: number,
    payload: ApiTokenCreate,
    client: ApiClient = api,
  ): Promise<ApiTokenCreated | null> {
    const { data } = await client.POST('/api/v1/projects/{project_id}/api-tokens', {
      params: { path: { project_id: projectId } },
      body: payload,
    })
    if (!data) return null
    await load(projectId, client)
    return data
  }

  /** 撤销(软删):成功后重载列表。 */
  async function revoke(projectId: number, tokenId: number, client: ApiClient = api) {
    await client.DELETE('/api/v1/api-tokens/{token_id}', {
      params: { path: { token_id: tokenId } },
    })
    await load(projectId, client)
  }

  function reset() {
    items.value = []
  }

  return { items, loading, load, create, revoke, reset }
})
