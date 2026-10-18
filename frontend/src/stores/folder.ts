import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api/client'

import type { ApiClient } from '@/api/client'
import type { components } from '@/api/schema'

type FolderOut = components['schemas']['FolderOut']

/** 创建入参:scope 固定 case,sort_order 缺省 0,调用方只给名字与父节点。 */
export interface FolderCreateInput {
  name: string
  parent_id?: number | null
  sort_order?: number
}

export type { FolderOut }

/** 树节点(el-tree data 形状)。children 由 store 按 (sort_order, id) 组装。 */
export interface FolderNode {
  id: number
  name: string
  sort_order: number
  children: FolderNode[]
}

/** 由扁平列表建树:后端已按 (sort_order, id) 排序,父子顺序天然稳定。 */
export function buildTree(folders: FolderOut[]): FolderNode[] {
  const byParent = new Map<number | null, FolderNode[]>()
  for (const folder of folders) {
    const list = byParent.get(folder.parent_id) ?? []
    list.push({ id: folder.id, name: folder.name, sort_order: folder.sort_order, children: [] })
    byParent.set(folder.parent_id, list)
  }
  const attach = (nodes: FolderNode[]): void => {
    for (const node of nodes) {
      node.children = byParent.get(node.id) ?? []
      attach(node.children)
    }
  }
  const roots = byParent.get(null) ?? []
  attach(roots)
  return roots
}

/** 目录树加载 + CRUD。变更后整体重取:树很小,本地增量重排不值得(ADR-0001 已接受排序并发风险)。 */
export const useFolderStore = defineStore('folder', () => {
  const projectId = ref<number | null>(null)
  const folders = ref<FolderOut[]>([])
  const tree = computed<FolderNode[]>(() => buildTree(folders.value))

  async function load(id: number, client: ApiClient = api): Promise<void> {
    try {
      const { data } = await client.GET('/api/v1/projects/{project_id}/folders', {
        params: { path: { project_id: id } },
      })
      projectId.value = id
      folders.value = Array.isArray(data) ? data : []
    } catch (exc) {
      // 失败即清空:保留上一个项目的状态,树显示旧目录、写操作还会落到旧项目
      projectId.value = null
      folders.value = []
      throw exc
    }
  }

  async function reload(client: ApiClient): Promise<void> {
    if (projectId.value !== null) await load(projectId.value, client)
  }

  async function create(payload: FolderCreateInput, client: ApiClient = api): Promise<void> {
    if (projectId.value === null) throw new Error('项目未加载,无法创建目录') // 向上抛,不让调用方误以为成功
    await client.POST('/api/v1/projects/{project_id}/folders', {
      params: { path: { project_id: projectId.value } },
      body: { scope: 'case', sort_order: 0, ...payload },
    })
    await reload(client)
  }

  async function rename(folderId: number, name: string, client: ApiClient = api): Promise<void> {
    await client.PATCH('/api/v1/folders/{folder_id}', {
      params: { path: { folder_id: folderId } },
      body: { name },
    })
    await reload(client)
  }

  async function move(
    folderId: number,
    parentId: number | null,
    sortOrder: number,
    client: ApiClient = api,
  ): Promise<void> {
    await client.POST('/api/v1/folders/{folder_id}/move', {
      params: { path: { folder_id: folderId } },
      body: { parent_id: parentId, sort_order: sortOrder },
    })
    await reload(client)
  }

  async function remove(folderId: number, client: ApiClient = api): Promise<void> {
    await client.DELETE('/api/v1/folders/{folder_id}', {
      params: { path: { folder_id: folderId } },
    })
    await reload(client)
  }

  return { projectId, folders, tree, load, create, rename, move, remove }
})
