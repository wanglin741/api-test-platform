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

// summary 键与后端落库一致(total/error,不是 total_steps/errored —— 引擎落库见 §报告)
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
    total: 2,
    passed: 1,
    failed: 1,
    error: 0,
    skipped: 0,
    assertion_total: 2,
    assertion_passed: 1,
    request_duration_ms: 118,
    pass_rate: 0.5,
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
  it('统计卡:总步骤/通过/失败/出错/跳过/总耗时 + 元信息一行', async () => {
    mockRun(RUN, STEPS)
    const wrapper = await mountView()

    const cards = wrapper.findAll('.stat-card')
    expect(cards).toHaveLength(6)
    expect(cards[0]?.text()).toBe('2总步骤')
    expect(cards[1]?.text()).toBe('1通过')
    expect(cards[2]?.text()).toBe('1失败')
    expect(cards[3]?.text()).toBe('0出错')
    expect(cards[4]?.text()).toBe('0跳过')
    expect(cards[5]?.text()).toBe('120ms总耗时')

    const meta = wrapper.find('.meta-line')
    expect(meta.text()).toContain('用例 #7')
    expect(meta.text()).toContain('v2')
    expect(meta.text()).toContain('环境 #3')
    expect(meta.text()).toContain('manual')
  })

  it('运行中(summary 未落):统计卡显示 — 而不是 0', async () => {
    mockRun(
      { ...RUN, status: 'running', summary: null, duration_ms: null },
      {
        items: [],
        count: 0,
        truncated: false,
      },
    )
    const wrapper = await mountView()

    expect(wrapper.findAll('.stat-card')[0]?.text()).toBe('—总步骤')
    expect(wrapper.findAll('.stat-card')[1]?.text()).toBe('—通过')
  })

  it('步骤行:状态/名称/耗时/断言通过数;失败行有醒目标记', async () => {
    mockRun(RUN, STEPS)
    const wrapper = await mountView()

    const rows = wrapper.findAll('.step-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]?.text()).toContain('登录')
    expect(rows[0]?.text()).toContain('断言 1/1')
    expect(rows[0]?.text()).toContain('30ms')
    expect(rows[0]?.classes()).not.toContain('is-bad')

    expect(rows[1]?.classes()).toContain('is-bad')
    expect(rows[1]?.text()).toContain('断言 0/1')
  })

  it('点行展开明细:请求/响应快照 + 断言逐条 + 提取变量 + 截断标记;再点收起', async () => {
    mockRun(RUN, STEPS)
    const wrapper = await mountView()

    const first = wrapper.findAll('.step-row')[0]!
    expect(first.find('.step-detail').exists()).toBe(false)
    await first.find('.row-head').trigger('click')
    expect(first.find('.step-detail').exists()).toBe(true)
    expect(first.find('.step-detail').text()).toContain('http://dev/login')
    expect(first.find('.step-detail').text()).toContain('"u"')
    expect(first.find('.step-detail').text()).toContain('PASS')
    expect(first.find('.step-detail').text()).toContain('token')

    await first.find('.row-head').trigger('click')
    expect(first.find('.step-detail').exists()).toBe(false)
  })

  it('失败步骤展开:断言 FAIL 带期望/实际与 message,响应截断有标记', async () => {
    mockRun(RUN, STEPS)
    const wrapper = await mountView()

    const second = wrapper.findAll('.step-row')[1]!
    await second.find('.row-head').trigger('click')
    const detail = second.find('.step-detail')
    expect(detail.text()).toContain('FAIL')
    expect(detail.text()).toContain('500')
    expect(detail.text()).toContain('响应码不符')
    expect(detail.text()).toContain('已截断')
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

describe('RunDetailView 计划分组(#31)', () => {
  const PLAN_RUN = {
    ...RUN,
    run_id: 'r-plan',
    case_id: null,
    case_version: null,
    plan_id: 12,
    summary: {
      ...RUN.summary,
      cases: [
        {
          case_index: 0,
          case_id: 11,
          case_name: '登录',
          status: 'failed',
          total: 1,
          passed: 0,
          failed: 1,
          error: 0,
          skipped: 0,
        },
        {
          case_index: 1,
          case_id: 12,
          case_name: '下单',
          status: 'skipped',
          total: 0,
          passed: 0,
          failed: 0,
          error: 0,
          skipped: 0,
        },
      ],
    },
  }

  function planStep(caseIndex: number, caseId: number, name: string, status: string) {
    return {
      id: caseIndex * 10 + 1,
      step_index: 0,
      step_name: name,
      step_type: 'http',
      status,
      duration_ms: 30,
      case_index: caseIndex,
      case_id: caseId,
      request: { method: 'POST', url: `http://dev/${name}`, headers: {}, body: null },
      response: { status_code: 200, headers: {}, body: '{}', size: 2, truncated: false },
      extracted_variables: {},
      assertions: [{ type: 'status', operator: 'eq', expected: 200, actual: 200, passed: true }],
      assertion_total: 1,
      assertion_passed: 1,
      error_message: null,
    }
  }

  it('计划报告:按用例分组,组头 = 用例名 + 状态 + 组内断言数;失败组突出', async () => {
    mockRun(
      PLAN_RUN,
      {
        items: [planStep(0, 11, '登录步骤', 'failed'), planStep(1, 12, '下单步骤', 'passed')],
        count: 2,
        truncated: false,
      },
    )
    const wrapper = await mountView()

    const heads = wrapper.findAll('.case-head')
    expect(heads).toHaveLength(2)
    expect(heads[0]?.text()).toContain('登录')
    expect(heads[0]?.text()).toContain('failed')
    expect(heads[0]?.classes()).toContain('is-bad')
    // 被跳用例没有步骤行,组头仍展示(否则凭空消失)
    expect(heads[1]?.text()).toContain('下单')
    expect(heads[1]?.text()).toContain('skipped')
    expect(heads[1]?.classes()).not.toContain('is-bad')

    // 组内复用既有步骤行,可展开快照
    const rows = wrapper.findAll('.step-row')
    expect(rows).toHaveLength(2)
    await rows[0]!.find('.row-head').trigger('click')
    expect(rows[0]!.find('.step-detail').text()).toContain('http://dev/登录步骤')
  })

  it('计划运行中:「正在执行」文案带用例名', async () => {
    mockRun({ ...PLAN_RUN, status: 'running', summary: null }, { items: [], count: 0, truncated: false })
    const wrapper = await mountView()
    const { useRunStore } = await import('@/stores/run')
    const store = useRunStore()

    store.progress = { completed: 1, total: 3 }
    store.runningStep = {
      stepIndex: 1,
      stepName: '提交订单',
      stepType: 'http',
      caseIndex: 1,
      caseName: '下单',
    }
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.live-text').text()).toContain('正在执行「下单」第 2 步:提交订单')
  })

  it('单用例报告:无组头,平铺渲染与改造前一致', async () => {
    mockRun(RUN, STEPS)
    const wrapper = await mountView()

    expect(wrapper.findAll('.case-head')).toHaveLength(0)
    expect(wrapper.findAll('.step-row')).toHaveLength(2)
  })
})
