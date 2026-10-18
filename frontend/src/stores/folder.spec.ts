import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { buildTree, useFolderStore } from './folder'

import type { FolderOut } from './folder'

function mockClient(handlers: Record<string, (...args: unknown[]) => unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

function folder(id: number, name: string, parentId: number | null, sortOrder = 0): FolderOut {
  return {
    id,
    project_id: 1,
    scope: 'case',
    parent_id: parentId,
    name,
    sort_order: sortOrder,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

describe('buildTree', () => {
  it('扁平列表按 parent_id 组树,后端排序即树内顺序', () => {
    const tree = buildTree([
      folder(2, 'B', null, 0),
      folder(1, 'A', null, 1),
      folder(3, 'A-2', 1),
      folder(4, 'A-1', 1),
    ])

    expect(tree.map((n) => n.id)).toEqual([2, 1])
    expect(tree[1]?.children.map((n) => n.id)).toEqual([3, 4])
  })

  it('悬挂 parent_id 不进树,不炸', () => {
    const tree = buildTree([folder(9, '孤儿', 999)])
    expect(tree).toEqual([])
  })
})

describe('folderStore', () => {
  it('load:GET 目录列表并记下项目 id', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ GET: vi.fn(async () => okResponse([folder(1, '登录链路', null)])) })
    const store = useFolderStore()

    await store.load(1, client)

    expect(store.folders).toHaveLength(1)
    expect(store.projectId).toBe(1)
    expect(store.tree[0]?.name).toBe('登录链路')
    expect((client.GET as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/folders',
    )
  })

  it('create:POST 后重取列表', async () => {
    setActivePinia(createPinia())
    const GET = vi.fn(async () => okResponse([folder(1, '新目录', null)]))
    const client = mockClient({
      GET,
      POST: vi.fn(async () => okResponse(folder(1, '新目录', null))),
    })
    const store = useFolderStore()
    await store.load(1, client)
    GET.mockClear()

    await store.create({ name: '新目录' }, client)

    expect((client.POST as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/projects/{project_id}/folders',
    )
    expect(GET).toHaveBeenCalledTimes(1)
  })

  it('rename:PATCH 改名端点', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse([])),
      PATCH: vi.fn(async () => okResponse(folder(3, '新名', null))),
    })
    const store = useFolderStore()
    await store.load(1, client)

    await store.rename(3, '新名', client)

    const [path, opts] = (client.PATCH as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { params: { path: { folder_id: number } }; body: { name: string } },
    ]
    expect(path).toBe('/api/v1/folders/{folder_id}')
    expect(opts.params.path.folder_id).toBe(3)
    expect(opts.body).toEqual({ name: '新名' })
  })

  it('move:POST move 端点,带 parent_id + sort_order', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse([])),
      POST: vi.fn(async () => okResponse(folder(3, 'A', 2))),
    })
    const store = useFolderStore()
    await store.load(1, client)

    await store.move(3, 2, 1, client)

    const [path, opts] = (client.POST as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { params: { path: { folder_id: number } }; body: { parent_id: number; sort_order: number } },
    ]
    expect(path).toBe('/api/v1/folders/{folder_id}/move')
    expect(opts.params.path.folder_id).toBe(3)
    expect(opts.body).toEqual({ parent_id: 2, sort_order: 1 })
  })

  it('move 到顶层:parent_id 传 null', async () => {
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse([])),
      POST: vi.fn(async () => okResponse(folder(3, 'A', null))),
    })
    const store = useFolderStore()
    await store.load(1, client)

    await store.move(3, null, 0, client)

    const [, opts] = (client.POST as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { body: { parent_id: number | null } },
    ]
    expect(opts.body.parent_id).toBeNull()
  })

  it('remove:DELETE 后重取', async () => {
    setActivePinia(createPinia())
    const GET = vi.fn(async () => okResponse([]))
    const client = mockClient({ GET, DELETE: vi.fn(async () => ({ data: undefined })) })
    const store = useFolderStore()
    await store.load(1, client)
    GET.mockClear()

    await store.remove(3, client)

    expect((client.DELETE as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(
      '/api/v1/folders/{folder_id}',
    )
    expect(GET).toHaveBeenCalledTimes(1)
  })
})
