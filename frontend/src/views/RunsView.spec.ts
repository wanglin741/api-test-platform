import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import type { ApiClient } from '@/api/client'
import { useProjectStore } from '@/stores/project'

import RunsView from './RunsView.vue'

const { get, post, push } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), push: vi.fn() }))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, POST: post } as unknown as ApiClient }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const ENV_DEV = {
  id: 3,
  project_id: 1,
  name: 'dev',
  description: null,
  base_url: 'http://dev',
  default_headers: {},
  auth: null,
  is_default: true,
  sort_order: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const RUN_A = {
  run_id: 'r-1',
  project_id: 1,
  case_id: 7,
  case_version: 2,
  environment_id: 3,
  trigger_mode: 'manual',
  status: 'success',
  duration_ms: 120,
  summary: {
    status: 'success',
    total_steps: 2,
    passed: 1,
    failed: 1,
    errored: 0,
    skipped: 0,
    duration_ms: 120,
  },
  created_at: '2026-01-02T00:00:00Z',
  started_at: null,
  finished_at: null,
}

const PAGE_1 = { items: [RUN_A], next_cursor: 'cursor-page-2' }

/** 按路径模板分流:path 是模板串,实际项目 id 在 opts.params.path 里。 */
function mockRunsList(byProject: Record<string, unknown>, envs: object[] = [ENV_DEV]) {
  get.mockImplementation(
    async (path: unknown, opts?: { params?: { path?: { project_id?: number } } }) => {
      const url = String(path)
      if (url.includes('environments')) return okResponse(envs)
      const pid = String(opts?.params?.path?.project_id ?? '')
      return okResponse(byProject[pid] ?? { items: [], next_cursor: null })
    },
  )
}

async function mountView() {
  setActivePinia(createPinia())
  useProjectStore().currentId = 1
  const wrapper = mount(RunsView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('RunsView', () => {
  it('渲染报告列表(状态/环境名/通过率/耗时),点击行跳详情', async () => {
    mockRunsList({ 1: PAGE_1 })
    const wrapper = await mountView()

    const rows = wrapper.findAll('.row')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.text()).toContain('r-1')
    expect(rows[0]?.text()).toContain('success')
    expect(rows[0]?.text()).toContain('dev') // 环境列显示名称
    expect(rows[0]?.text()).toContain('1/2') // 通过率来自 summary

    await rows[0]!.trigger('click')
    expect(push).toHaveBeenCalledWith('/runs/r-1')
  })

  it('项目切换后重新拉取该项目的列表(AC4)', async () => {
    mockRunsList({ 1: PAGE_1, 2: { items: [], next_cursor: null } })
    const wrapper = await mountView()
    expect(wrapper.findAll('.row')).toHaveLength(1)

    useProjectStore().currentId = 2
    await flushPromises()

    expect(wrapper.findAll('.row')).toHaveLength(0)
    const runsCalls = get.mock.calls.filter((call) => String(call[0]).includes('/runs'))
    const lastCall = runsCalls.at(-1) as [string, { params: { path: { project_id: number } } }]
    expect(lastCall[1].params.path.project_id).toBe(2)
  })

  it('再次执行:复用执行弹窗,带上原 run 的用例与环境', async () => {
    mockRunsList({ 1: PAGE_1 })
    post.mockResolvedValue(okResponse({ run_id: 'r-9', status: 'pending' }))
    const wrapper = await mountView()

    await wrapper.find('.row .rerun-btn').trigger('click')
    await flushPromises()
    // 弹窗已打开,提交后应触发执行
    await wrapper.find('.submit-btn').trigger('click')
    await flushPromises()

    const [path, opts] = post.mock.calls[0] as [
      string,
      { body: { case_id: number; environment_id: number } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/runs')
    expect(opts.body).toEqual({ case_id: 7, environment_id: 3 })
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith('/runs/r-9')
  })

  it('游标分页:只有「下一页」,点击带 cursor 拉取;没有下一页时禁用', async () => {
    mockRunsList({
      1: PAGE_1,
      // 带游标的请求由 store 发出;这里让所有不带游标的重复请求也回第一页即可
    })
    const wrapper = await mountView()

    // 只支持下一页:没有「上一页」按钮
    expect(wrapper.find('.pager .prev').exists()).toBe(false)
    expect((wrapper.find('.pager .next').element as HTMLButtonElement).disabled).toBe(false)

    await wrapper.find('.pager .next').trigger('click')
    await flushPromises()

    const runsCalls = get.mock.calls.filter(
      (call) => String(call[0]).includes('/runs') && !String(call[0]).includes('environments'),
    )
    const lastCall = runsCalls.at(-1) as [string, { params: { query: Record<string, string> } }]
    expect(lastCall[1].params.query.cursor).toBe('cursor-page-2')
  })

  it('末页:next_cursor 为空时「下一页」禁用', async () => {
    mockRunsList({ 1: { items: [RUN_A], next_cursor: null } })
    const wrapper = await mountView()

    expect((wrapper.find('.pager .next').element as HTMLButtonElement).disabled).toBe(true)
  })
})
