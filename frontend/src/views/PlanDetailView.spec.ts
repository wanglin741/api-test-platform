import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import type { ApiClient } from '@/api/client'
import { useProjectStore } from '@/stores/project'

import PlanDetailView from './PlanDetailView.vue'

const { get, patch, put, push } = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '1' } }),
  useRouter: () => ({ push }),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, PATCH: patch, PUT: put } as unknown as ApiClient }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const PLAN = {
  id: 1,
  project_id: 1,
  name: '每晚回归',
  description: null,
  environment_id: null,
  execute_mode: 'serial',
  concurrency: 1,
  stop_on_fail: false,
  item_count: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const ITEM_LOGIN = { id: 11, item_type: 'case', case_id: 101, case_name: '登录', sort_order: 0 }

const ENV_DEV = { id: 3, name: 'dev' }
const CASE_LOGIN = { id: 101, name: '登录' }
const CASE_ORDER = { id: 102, name: '下单' }

function mockGets({ items = [ITEM_LOGIN] as object[] } = {}) {
  get.mockImplementation(async (path: unknown) => {
    const url = String(path)
    if (url.includes('/items')) return okResponse(items)
    if (url.includes('/environments')) return okResponse([ENV_DEV])
    if (url.includes('/cases')) return okResponse({ items: [CASE_LOGIN, CASE_ORDER], total: 2 })
    return okResponse(PLAN)
  })
}

async function mountView() {
  setActivePinia(createPinia())
  useProjectStore().currentId = 1
  const wrapper = mount(PlanDetailView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('PlanDetailView', () => {
  it('渲染配置与清单(环境名、执行顺序)', async () => {
    mockGets()
    const wrapper = await mountView()

    expect(wrapper.find('.title').text()).toBe('每晚回归')
    expect(wrapper.text()).toContain('登录') // 清单里已有
    expect(wrapper.text()).toContain('下单') // 池里可选
    expect(wrapper.text()).toContain('共 1 条')
  })

  it('保存配置:PATCH 表单值', async () => {
    mockGets()
    patch.mockResolvedValue(okResponse(PLAN))
    const wrapper = await mountView()

    await wrapper.find('.name-input input').setValue('回归 v2')
    await wrapper.find('.config-card button.el-button--primary').trigger('click')
    await flushPromises()

    const [path, opts] = patch.mock.calls[0] as [string, { body: { name: string } }]
    expect(path).toBe('/api/v1/plans/{plan_id}')
    expect(opts.body.name).toBe('回归 v2')
  })

  it('编排清单:添加用例后保存,PUT 按顺序携带 case_id;移除后同样 PUT', async () => {
    mockGets()
    put.mockResolvedValue(
      okResponse([ITEM_LOGIN, { ...ITEM_LOGIN, id: 99, case_id: 102, case_name: '下单' }]),
    )
    const wrapper = await mountView()

    // 从池里添加「下单」
    const poolAdd = wrapper.findAll('.pool-item button').at(-1)!
    await poolAdd.trigger('click')
    expect(wrapper.text()).toContain('共 2 条')

    await wrapper.find('.save-items').trigger('click')
    await flushPromises()

    const [path, opts] = put.mock.calls[0] as [string, { body: { items: { case_id: number }[] } }]
    expect(path).toBe('/api/v1/plans/{plan_id}/items')
    expect(opts.body.items).toEqual([{ case_id: 101 }, { case_id: 102 }])

    // 移除「登录」后 PUT 只剩新加的
    put.mockClear()
    await wrapper.findAll('.picked-item .el-button--danger')[0]!.trigger('click')
    await wrapper.find('.save-items').trigger('click')
    await flushPromises()
    const [, opts2] = put.mock.calls[0] as [string, { body: { items: { case_id: number }[] } }]
    expect(opts2.body.items).toEqual([{ case_id: 102 }])
  })
})
