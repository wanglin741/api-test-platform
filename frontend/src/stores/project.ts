/**
 * 项目状态(全站数据的作用域根,前端设计 §4)。
 * load/create 注入 client(默认全局 api):store 缝测试换 mock。
 * 当前项目 id 持久化 localStorage,刷新后保持;所有页面按它过滤。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, type ApiClient, type Project } from '@/api/client'

import type { components } from '@/api/schema'

type ProjectCreate = components['schemas']['ProjectCreate']

const CURRENT_KEY = 'apitest.current-project-id'

function readStoredId(): number | null {
  const raw = localStorage.getItem(CURRENT_KEY)
  if (raw === null) return null
  const parsed = Number(raw)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

export const useProjectStore = defineStore('project', () => {
  const items = ref<Project[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const currentId = ref<number | null>(readStoredId())

  const current = computed(() => items.value.find((p) => p.id === currentId.value) ?? null)

  async function load(client: ApiClient = api) {
    loading.value = true
    error.value = null
    try {
      const { data } = await client.GET('/api/v1/projects')
      items.value = data ?? []
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : String(exc)
    } finally {
      loading.value = false
    }
  }

  function select(id: number) {
    currentId.value = id
    localStorage.setItem(CURRENT_KEY, String(id))
  }

  /** 创建项目:成功后重载列表并选中新项目;失败向上抛(组件层提示)。 */
  async function create(payload: ProjectCreate, client: ApiClient = api) {
    const { data } = await client.POST('/api/v1/projects', { body: payload })
    if (!data) return null
    await load(client)
    select(data.id)
    return data
  }

  return { items, loading, error, currentId, current, load, select, create }
})
