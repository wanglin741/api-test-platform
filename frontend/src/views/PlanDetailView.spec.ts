import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import type { ApiClient } from '@/api/client'
import { useProjectStore } from '@/stores/project'

import PlanDetailView from './PlanDetailView.vue'

const { get, patch, put, post, push } = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '1' } }),
  useRouter: () => ({ push }),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    api: { GET: get, PATCH: patch, PUT: put, POST: post } as unknown as ApiClient,
  }
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

function mockGets({
  items = [ITEM_LOGIN] as object[],
  plan = PLAN as object,
  runs = { items: [], next_cursor: null } as object,
  schedules = [] as object[],
  tokens = [] as object[],
} = {}) {
  get.mockImplementation(async (path: unknown) => {
    const url = String(path)
    if (url.includes('/items')) return okResponse(items)
    if (url.includes('/schedules')) return okResponse(schedules)
    if (url.includes('/api-tokens')) return okResponse(tokens)
    if (url.includes('/environments')) return okResponse([ENV_DEV])
    if (url.includes('/cases')) return okResponse({ items: [CASE_LOGIN, CASE_ORDER], total: 2 })
    if (url.includes('/runs')) return okResponse(runs)
    return okResponse(plan)
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

  it('CI 接入面板挂载:按项目加载 Token 列表(#42)', async () => {
    mockGets()
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('CI 接入')
    const tokenCall = get.mock.calls.find((call) => String(call[0]).includes('/api-tokens')) as [
      string,
      { params: { path: { project_id: number } } },
    ]
    expect(tokenCall[1].params.path.project_id).toBe(1)
  })

  it('最近执行:挂该计划的执行历史,点击进入报告(#33)', async () => {
    mockGets({
      runs: {
        items: [
          {
            run_id: 'r-9',
            plan_id: 1,
            case_id: null,
            case_version: null,
            environment_id: 3,
            trigger_mode: 'manual',
            status: 'failed',
            duration_ms: 88,
            summary: { total: 3, passed: 2, failed: 1 },
            created_at: '2026-01-02T00:00:00Z',
          },
        ],
        next_cursor: null,
      },
    })
    const wrapper = await mountView()

    const rows = wrapper.findAll('.recent-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.text()).toContain('r-9')
    expect(rows[0]!.text()).toContain('failed')

    // 拉取时带 plan_id 筛选
    const runsCall = get.mock.calls.find((call) => String(call[0]).includes('/runs')) as [
      string,
      { params: { query: Record<string, unknown> } },
    ]
    expect(runsCall[1].params.query.plan_id).toBe(1)

    await rows[0]!.trigger('click')
    expect(push).toHaveBeenCalledWith('/runs/r-9')
  })

  it('最近执行按计划自己的 project_id 拉取,不信全局选中项目(#33 review)', async () => {
    mockGets({
      runs: { items: [], next_cursor: null },
    })
    // 全局选中的是项目 2,但计划属于项目 1(直链/刷新时两者可能不一致)
    setActivePinia(createPinia())
    useProjectStore().currentId = 2
    const wrapper = mount(PlanDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()

    const runsCall = get.mock.calls.find((call) => String(call[0]).includes('/runs')) as [
      string,
      { params: { path: { project_id: number }; query: Record<string, unknown> } },
    ]
    expect(runsCall[1].params.path.project_id).toBe(1)
    expect(runsCall[1].params.query.plan_id).toBe(1)
    expect(wrapper.findAll('.recent-row')).toHaveLength(0)
  })

  it('调度/CI 面板按计划自己的 project_id 拉取,不信全局选中项目(#33 review)', async () => {
    // 计划属于项目 1,全局选中却是项目 2;面板不能拿 2 去拉调度/Token
    mockGets()
    setActivePinia(createPinia())
    useProjectStore().currentId = 2
    mount(PlanDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()

    const schedulesCall = get.mock.calls.find((call) =>
      String(call[0]).includes('/schedules'),
    ) as [string, { params: { path: { project_id: number }; query?: { plan_id?: number } } }]
    expect(schedulesCall[1].params.path.project_id).toBe(1)
    expect(schedulesCall[1].params.query?.plan_id).toBe(1)

    const tokenCall = get.mock.calls.find((call) => String(call[0]).includes('/api-tokens')) as [
      string,
      { params: { path: { project_id: number } } },
    ]
    expect(tokenCall[1].params.path.project_id).toBe(1)
  })
})

describe('PlanDetailView 触发动线(#31)', () => {
  const PLAN_WITH_ENV = { ...PLAN, environment_id: 3 }

  it('弹窗环境默认取计划配置;确认后 POST 计划触发并跳转报告', async () => {
    mockGets({ plan: PLAN_WITH_ENV })
    post.mockResolvedValue(okResponse({ run_id: 'r-9', status: 'pending' }))
    setActivePinia(createPinia())
    useProjectStore().currentId = 1
    // el-select 在 jsdom 的弹窗里会触发递归更新(弹层定位依赖真实布局):
    // 弹层组件一律打桩,断言走 POST 出参,不测弹窗内交互
    const wrapper = mount(PlanDetailView, {
      global: {
        plugins: [ElementPlus],
        stubs: { teleport: true, 'el-select': true, 'el-option': true },
      },
    })
    await flushPromises()

    await wrapper.find('.bar .el-button--primary').trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.el-dialog')
    expect(dialog.exists()).toBe(true)

    await dialog.find('.el-dialog__footer .el-button--primary').trigger('click')
    await flushPromises()

    const [path, opts] = post.mock.calls[0] as [string, { body: unknown }]
    expect(path).toBe('/api/v1/projects/{project_id}/runs')
    expect(opts.body).toEqual({ plan_id: 1, environment_id: 3 })
    expect(push).toHaveBeenCalledWith('/runs/r-9')
  })
})
