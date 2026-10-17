/**
 * 变量状态(项目级 + 环境级两个作用域)。
 * 后端语义是「整体替换」:GET 读全量,编辑后 PUT 整表提交(VariableReplace)。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type VariableIn = components['schemas']['VariableIn']
type VariableOut = components['schemas']['VariableOut']

function toEditable(rows: VariableOut[]): VariableIn[] {
  return rows.map(({ name, value, is_secret, description }) => ({ name, value, is_secret, description }))
}

export const useVariableStore = defineStore('variables', () => {
  const projectVars = ref<VariableIn[]>([])
  const envVars = ref<VariableIn[]>([])
  const loading = ref(false)

  async function loadProjectVars(projectId: number, client: ApiClient = api) {
    loading.value = true
    try {
      const { data } = await client.GET('/api/v1/projects/{project_id}/variables', {
        params: { path: { project_id: projectId } },
      })
      projectVars.value = toEditable(data ?? [])
    } finally {
      loading.value = false
    }
  }

  async function loadEnvVars(environmentId: number, client: ApiClient = api) {
    loading.value = true
    try {
      const { data } = await client.GET('/api/v1/environments/{environment_id}/variables', {
        params: { path: { environment_id: environmentId } },
      })
      envVars.value = toEditable(data ?? [])
    } finally {
      loading.value = false
    }
  }

  async function saveProjectVars(projectId: number, items: VariableIn[], client: ApiClient = api) {
    await client.PUT('/api/v1/projects/{project_id}/variables', {
      params: { path: { project_id: projectId } },
      body: { items },
    })
  }

  async function saveEnvVars(environmentId: number, items: VariableIn[], client: ApiClient = api) {
    await client.PUT('/api/v1/environments/{environment_id}/variables', {
      params: { path: { environment_id: environmentId } },
      body: { items },
    })
  }

  return { projectVars, envVars, loading, loadProjectVars, loadEnvVars, saveProjectVars, saveEnvVars }
})
