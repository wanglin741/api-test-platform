import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useProjectStore } from './project'

/** store 缝的 mock client:只实现 store 实际调用的方法(前端设计 §7 缝 1)。 */
function mockClient(
  handlers: { GET?: (...args: unknown[]) => unknown; POST?: (...args: unknown[]) => unknown } = {},
): ApiClient {
  return {
    GET: handlers.GET ?? vi.fn(),
    POST: handlers.POST ?? vi.fn(),
  } as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

describe('projectStore', () => {
  it('load 成功:填充列表并清除错误', async () => {
    setActivePinia(createPinia())
    localStorage.clear()
    const client = mockClient({
      GET: vi.fn(async () => okResponse([{ id: 1, name: '示例项目' }])),
    })
    const store = useProjectStore()

    await store.load(client)

    expect(store.items).toEqual([{ id: 1, name: '示例项目' }])
    expect(store.error).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('load 失败:error 记录信息,items 保持为空', async () => {
    setActivePinia(createPinia())
    localStorage.clear()
    const client = mockClient({
      GET: vi.fn(async () => {
        throw new Error('项目 不存在（project_not_found）')
      }),
    })
    const store = useProjectStore()

    await store.load(client)

    expect(store.items).toEqual([])
    expect(store.error).toContain('project_not_found')
    expect(store.loading).toBe(false)
  })

  it('select:更新当前项目并持久化到 localStorage', async () => {
    setActivePinia(createPinia())
    localStorage.clear()
    const client = mockClient({
      GET: vi.fn(async () =>
        okResponse([
          { id: 1, name: 'A' },
          { id: 2, name: 'B' },
        ]),
      ),
    })
    const store = useProjectStore()
    await store.load(client)

    store.select(2)

    expect(store.currentId).toBe(2)
    expect(store.current?.name).toBe('B')
    expect(localStorage.getItem('apitest.current-project-id')).toBe('2')
  })

  it('刷新恢复上次选择:load 后仅当 id 仍在列表中才生效', async () => {
    localStorage.setItem('apitest.current-project-id', '7')
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse([{ id: 7, name: '老项目' }])),
    })

    await useProjectStore().load(client)

    expect(useProjectStore().currentId).toBe(7)
  })

  it('恢复的 id 已不存在(如 seed 重置):回落到列表第一个,不再拿失效 id 请求', async () => {
    localStorage.setItem('apitest.current-project-id', '2')
    setActivePinia(createPinia())
    const client = mockClient({
      GET: vi.fn(async () => okResponse([{ id: 1, name: '示例项目' }])),
    })
    const store = useProjectStore()

    await store.load(client)

    expect(store.currentId).toBe(1)
    expect(localStorage.getItem('apitest.current-project-id')).toBe('1')
  })

  it('列表为空:currentId 清空并移除持久化', async () => {
    localStorage.setItem('apitest.current-project-id', '9')
    setActivePinia(createPinia())
    const client = mockClient({ GET: vi.fn(async () => okResponse([])) })
    const store = useProjectStore()

    await store.load(client)

    expect(store.currentId).toBeNull()
    expect(localStorage.getItem('apitest.current-project-id')).toBeNull()
  })

  it('create:POST 后重载列表并选中新项目', async () => {
    setActivePinia(createPinia())
    localStorage.clear()
    let items = [{ id: 1, name: '已有' }]
    const client = mockClient({
      GET: vi.fn(async () => okResponse(items)),
      POST: vi.fn(async () => {
        items = [...items, { id: 5, name: '新建' }]
        return okResponse({ id: 5, name: '新建' })
      }),
    })
    const store = useProjectStore()
    await store.load(client)

    const created = await store.create({ name: '新建' }, client)

    expect(created?.id).toBe(5)
    expect(store.items).toHaveLength(2)
    expect(store.currentId).toBe(5)
  })

  it('create 失败:错误向上抛(store 不吞)', async () => {
    setActivePinia(createPinia())
    localStorage.clear()
    const client = mockClient({
      POST: vi.fn(async () => {
        throw new Error('项目 已存在')
      }),
    })
    const store = useProjectStore()

    await expect(store.create({ name: '重复' }, client)).rejects.toThrow('项目 已存在')
  })
})
