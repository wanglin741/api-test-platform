import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import type { ApiClient } from '@/api/client'

import RunDetailView from './RunDetailView.vue'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'r-1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, POST: post } as unknown as ApiClient }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const RUN = {
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
  created_at: '2026-01-01T00:00:00Z',
  started_at: '2026-01-01T00:00:01Z',
  finished_at: '2026-01-01T00:00:02Z',
}

const STEPS = {
  items: [
    {
      id: 1,
      step_index: 0,
      step_name: '登录',
      step_type: 'http',
      status: 'passed',
      duration_ms: 30,
      request: {
        method: 'POST',
        url: 'http://dev/login',
        headers: { 'X-Token': 'abc' },
        body: '{"u":"a"}',
      },
      response: { status_code: 200, headers: {}, body: '{"ok":true}', size: 13, truncated: false },
      extracted_variables: { token: 'abc' },
      assertions: [{ type: 'status', operator: 'eq', expected: 200, actual: 200, passed: true }],
      assertion_total: 1,
      assertion_passed: 1,
      error_message: null,
    },
    {
      id: 2,
      step_index: 1,
      step_name: '下单',
      step_type: 'http',
      status: 'failed',
      duration_ms: 45,
      request: { method: 'POST', url: 'http://dev/orders', headers: {}, body: null },
      response: { status_code: 500, headers: {}, body: 'boom', size: 4, truncated: true },
      extracted_variables: {},
      assertions: [
        {
          type: 'status',
          operator: 'eq',
          expected: 200,
          actual: 500,
          passed: false,
          message: '响应码不符',
        },
      ],
      assertion_total: 1,
      assertion_passed: 0,
      error_message: null,
    },
  ],
  count: 2,
  truncated: false,
}

function mockRun(run: object, steps: object) {
  get.mockImplementation(async (path: unknown) =>
    okResponse(String(path).includes('/steps') ? steps : run),
  )
}

async function mountView() {
  setActivePinia(createPinia())
  const wrapper = mount(RunDetailView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('RunDetailView', () => {
  it('渲染 run 状态、summary 与步骤时间线(含断言明细与通过数)', async () => {
    mockRun(RUN, STEPS)
    const wrapper = await mountView()

    expect(wrapper.find('.status-tag').text()).toBe('success')
    expect(wrapper.find('.summary .passed').text()).toContain('1')
    const cards = wrapper.findAll('.step-card')
    expect(cards).toHaveLength(2)
    expect(cards[0]?.text()).toContain('登录')
    expect(cards[0]?.text()).toContain('POST http://dev/login')
    expect(cards[0]?.text()).toContain('断言 1/1')
    expect(cards[0]?.text()).toContain('status eq 200')
    expect(cards[1]?.text()).toContain('断言 0/1')
    expect(cards[1]?.text()).toContain('响应码不符')
  })

  it('展开步骤可见请求/响应快照、提取变量;截断有标记', async () => {
    mockRun(RUN, STEPS)
    const wrapper = await mountView()

    const first = wrapper.findAll('.step-card')[0]!
    expect(first.find('.extracted').text()).toContain('token=abc')

    await first.find('.snapshots-toggle').trigger('click')
    expect(first.find('.snapshots').text()).toContain('{"u":"a"}')
    expect(first.find('.snapshots').text()).toContain('{"ok":true}')

    const second = wrapper.findAll('.step-card')[1]!
    await second.find('.snapshots-toggle').trigger('click')
    expect(second.find('.snapshots').text()).toContain('响应体已截断')
  })

  it('非终态:显示取消按钮,点击后 POST cancel', async () => {
    mockRun({ ...RUN, status: 'running' }, { items: [], count: 0, truncated: false })
    post.mockResolvedValue(okResponse({ run_id: 'r-1', status: 'stopping' }))
    const wrapper = await mountView()

    const cancel = wrapper.find('.cancel-btn')
    expect(cancel.exists()).toBe(true)
    await cancel.trigger('click')
    await flushPromises()

    expect(post.mock.calls[0]?.[0]).toBe('/api/v1/runs/{run_id}/cancel')
  })

  it('终态:无取消按钮;步骤被截断时给出标记', async () => {
    mockRun(RUN, { items: STEPS.items, count: 2, truncated: true })
    const wrapper = await mountView()

    expect(wrapper.find('.cancel-btn').exists()).toBe(false)
    expect(wrapper.find('.truncated').exists()).toBe(true)
  })
})
