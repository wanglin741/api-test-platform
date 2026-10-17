/**
 * 项目状态(全站数据的作用域根,前端设计 §4)。
 * load 注入 client(默认全局 api):store 缝测试换 mock。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type ApiClient, type Project } from '@/api/client'

export const useProjectStore = defineStore('project', () => {
  const items = ref<Project[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

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

  return { items, loading, error, load }
})
