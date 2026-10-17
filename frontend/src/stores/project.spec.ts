import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useProjectStore } from './project'

/** store 缝的 mock client:只实现 store 实际调用的方法(前端设计 §7 缝 1)。 */
function mockClient(get: (...args: unknown[]) => unknown = vi.fn()): ApiClient {
  return { GET: get } as unknown as ApiClient
}

describe('projectStore', () => {
  it('load 成功:填充列表并清除错误', async () => {
    setActivePinia(createPinia())
    const client = mockClient(
      vi.fn(async () => ({ data: [{ id: 1, name: '示例项目' }], error: undefined, response: new Response() })),
    )
    const store = useProjectStore()

    await store.load(client)

    expect(store.items).toEqual([{ id: 1, name: '示例项目' }])
    expect(store.error).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('load 失败:error 记录信息,items 保持为空', async () => {
    setActivePinia(createPinia())
    const client = mockClient(
      vi.fn(async () => {
        throw new Error('项目 不存在（project_not_found）')
      }),
    )
    const store = useProjectStore()

    await store.load(client)

    expect(store.items).toEqual([])
    expect(store.error).toContain('project_not_found')
    expect(store.loading).toBe(false)
  })
})
