import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'

import type { ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type CaseOut = components['schemas']['CaseOut']
type CaseDetail = components['schemas']['CaseDetail']
type CaseCreate = components['schemas']['CaseCreate']
type CaseUpdate = components['schemas']['CaseUpdate']

const PAGE_SIZE = 20

/** 用例列表 + 详情加载/保存。分页用 page/page_size(后端 CasePage)。 */
export const useCaseStore = defineStore('case', () => {
  const items = ref<CaseOut[]>([])
  const total = ref(0)
  const page = ref(1)

  async function load(projectId: number, client: ApiClient = api, toPage = 1): Promise<void> {
    const { data } = await client.GET('/api/v1/projects/{project_id}/cases', {
      params: { path: { project_id: projectId }, query: { page: toPage, page_size: PAGE_SIZE } },
    })
    items.value = data?.items ?? []
    total.value = data?.total ?? 0
    page.value = toPage
  }

  async function loadOne(caseId: number, client: ApiClient = api): Promise<CaseDetail | null> {
    const { data } = await client.GET('/api/v1/cases/{case_id}', {
      params: { path: { case_id: caseId } },
    })
    return data ?? null
  }

  async function create(
    projectId: number,
    payload: CaseCreate,
    client: ApiClient = api,
  ): Promise<CaseDetail | null> {
    const { data } = await client.POST('/api/v1/projects/{project_id}/cases', {
      params: { path: { project_id: projectId } },
      body: payload,
    })
    await load(projectId, client, page.value)
    return data ?? null
  }

  async function save(
    caseId: number,
    payload: CaseUpdate,
    client: ApiClient = api,
  ): Promise<CaseDetail | null> {
    const { data } = await client.PATCH('/api/v1/cases/{case_id}', {
      params: { path: { case_id: caseId } },
      body: payload,
    })
    return data ?? null
  }

  return { items, total, page, load, loadOne, create, save }
})
