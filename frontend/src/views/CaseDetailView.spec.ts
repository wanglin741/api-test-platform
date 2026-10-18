import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import { ApiError } from '@/api/client'
import type { ApiClient } from '@/api/client'

import CaseDetailView from './CaseDetailView.vue'
import DslEditor from '@/components/DslEditor.vue'

const { push, get, patch, post } = vi.hoisted(() => ({
  push: vi.fn(),
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '7' } }),
  useRouter: () => ({ push }),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, PATCH: patch, POST: post } as unknown as ApiClient }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const DETAIL = {
  id: 7,
  project_id: 1,
  name: '登录链路',
  description: null,
  priority: 'P0',
  tags: [],
  current_version: 2,
  dsl: { dsl_version: 1, steps: [{ id: 'step-1', name: 'step1' }] },
  created_by: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

const DEBUG_RESULT = {
  step_id: 'step-1',
  step_name: 'step1',
  status: 'passed',
  duration_ms: 12,
  attempts: 1,
  error_message: null,
  request: { method: 'GET', url: 'http://inner.example.com/api', headers: {}, body: null },
  response: { status_code: 200, headers: {}, body: '{"ok":true}', size: 10, truncated: false },
  extracted: { token: 'abc' },
  assertions: [{ type: 'status_code', operator: 'eq', expected: 200, actual: 200, passed: true }],
}

async function mountView(pinia = createPinia()) {
  // 项目 id 持久化在 localStorage:清掉,避免用例间串台(需要时测试里显式设置)
  window.localStorage.clear()
  setActivePinia(pinia)
  const wrapper = mount(CaseDetailView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

/** 详情页加载会同时打两个 GET:环境列表 + 用例详情,按路径分流。 */
function mockDetailGet(body: unknown, environments: unknown[] = []) {
  get.mockImplementation((path: string) =>
    Promise.resolve(okResponse(path.includes('/environments') ? environments : body)),
  )
}

describe('CaseDetailView', () => {
  it('载入详情:名称与 DSL 渲染进编辑器', async () => {
    mockDetailGet(DETAIL)
    const wrapper = await mountView()

    expect((wrapper.find('.header input').element as HTMLInputElement).value).toBe('登录链路')
    expect(wrapper.find('.dsl-editor .cm-content').element.textContent).toContain('"steps"')
  })

  it('保存:PATCH 提交名称 + 解析后的 dsl(规范化补齐默认值),版本号随之更新', async () => {
    mockDetailGet(DETAIL)
    const saved = { ...DETAIL, current_version: 3 }
    patch.mockResolvedValue(okResponse(saved))
    const wrapper = await mountView()

    wrapper.findComponent(DslEditor).vm.$emit('update:modelValue', '{"dsl_version":1,"steps":[]}')
    await wrapper.vm.$nextTick()
    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()

    const [path, opts] = patch.mock.calls[0] as [string, { body: { name: string; dsl: unknown } }]
    expect(path).toBe('/api/v1/cases/{case_id}')
    expect(opts.body.name).toBe('登录链路')
    expect(opts.body.dsl).toEqual({
      dsl_version: 1,
      config: { variables: {}, timeout_ms: 10_000, fail_fast: false },
      steps: [],
    })
    expect(wrapper.find('.version').text()).toBe('v3')
  })

  it('编排视图编辑 URL:改动同步进 dsl,随 PATCH 提交', async () => {
    mockDetailGet(DETAIL)
    patch.mockResolvedValue(okResponse({ ...DETAIL, current_version: 3 }))
    const wrapper = await mountView()

    await wrapper.find('.step-detail .url input').setValue('https://api.example.com/login')

    // 源码文本同步刷新
    expect(wrapper.findComponent(DslEditor).props('modelValue')).toContain('api.example.com/login')

    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()

    const [, opts] = patch.mock.calls[0] as [
      string,
      { body: { dsl: { steps: { request: { url: string } }[] } } },
    ]
    expect(opts.body.dsl.steps[0]?.request.url).toBe('https://api.example.com/login')
  })

  it('脏步骤:编辑后左列表出小圆点,保存后消失', async () => {
    mockDetailGet(DETAIL)
    patch.mockResolvedValue(okResponse({ ...DETAIL, current_version: 3 }))
    const wrapper = await mountView()

    expect(wrapper.find('.dirty-dot').exists()).toBe(false)
    await wrapper.find('.step-detail .url input').setValue('https://api.example.com/login')
    expect(wrapper.find('.dirty-dot').exists()).toBe(true)

    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.dirty-dot').exists()).toBe(false)
  })

  it('拖拽重排:不改动任何一步内容也算脏,保存按新顺序提交', async () => {
    const TWO_STEPS = {
      ...DETAIL,
      dsl: {
        dsl_version: 1,
        steps: [
          { id: 'step-1', name: 'a', request: { method: 'GET', url: '/a' } },
          { id: 'step-2', name: 'b', request: { method: 'GET', url: '/b' } },
        ],
      },
    }
    mockDetailGet(TWO_STEPS)
    patch.mockResolvedValue(okResponse({ ...TWO_STEPS, current_version: 3 }))
    const wrapper = await mountView()

    expect(wrapper.find('.dirty-dot').exists()).toBe(false)
    // 模拟拖拽结果:draggable 回吐换序后的同一批步骤对象
    const draggable = wrapper.findComponent({ name: 'draggable' })
    const steps = draggable.props('modelValue') as { id: string }[]
    draggable.vm.$emit('update:modelValue', [steps[1], steps[0]])
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.dirty-dot').exists()).toBe(true)

    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()
    const [, opts] = patch.mock.calls[0] as [string, { body: { dsl: { steps: { id: string }[] } } }]
    expect(opts.body.dsl.steps.map((s) => s.id)).toEqual(['step-2', 'step-1'])
  })

  it('单步调试:先保存脏改动再调同步端点,结果渲染进调试面板', async () => {
    // 环境按用例自己的 project_id 加载(而非全局 currentId),下拉取到该项目的环境
    mockDetailGet(DETAIL, [{ id: 3, name: 'staging', base_url: 'http://x', is_default: false }])
    patch.mockResolvedValue(okResponse({ ...DETAIL, current_version: 3 }))
    post.mockResolvedValue(okResponse(DEBUG_RESULT))
    const wrapper = await mountView()

    // 先弄脏:调试端点执行已保存版本,脏改动必须先落库
    await wrapper.find('.step-detail .url input').setValue('https://api.example.com/now')
    await wrapper.find('.op-debug').trigger('click')
    await flushPromises()

    const [debugPath, debugOpts] = post.mock.calls[0] as [
      string,
      { params: { path: { step_id: string }; query: { environment_id: number } } },
    ]
    expect(debugPath).toBe('/api/v1/cases/{case_id}/steps/{step_id}/debug')
    expect(debugOpts.params.query.environment_id).toBe(3)
    // 调试端点执行已保存版本:脏改动先落库
    const patchOrder = patch.mock.invocationCallOrder[0]
    const postOrder = post.mock.invocationCallOrder[0]
    expect(patchOrder).toBeDefined()
    expect(postOrder).toBeDefined()
    expect(patchOrder as number).toBeLessThan(postOrder as number)
    expect(wrapper.find('.debug-panel').text()).toContain('passed')
    expect(wrapper.find('.debug-panel').text()).toContain('token')
  })

  it('改名后保存:新名称随 PATCH 提交', async () => {
    mockDetailGet(DETAIL)
    patch.mockResolvedValue(okResponse({ ...DETAIL, name: '下单主链路', current_version: 3 }))
    const wrapper = await mountView()

    await wrapper.find('.header input').setValue('下单主链路')
    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()

    const [, opts] = patch.mock.calls[0] as [string, { body: { name: string } }]
    expect(opts.body.name).toBe('下单主链路')
    expect((wrapper.find('.header input').element as HTMLInputElement).value).toBe('下单主链路')
  })

  it('保存返回 dsl_invalid:回显每条校验错误,不弹全局错误也能看到明细', async () => {
    mockDetailGet(DETAIL)
    patch.mockRejectedValueOnce(
      new ApiError('DSL 结构校验失败', 'dsl_invalid', 422, {
        errors: [
          { loc: ['dsl', 'steps', 0, 'method'], msg: 'Field required', type: 'missing' },
          { loc: ['dsl', 'steps', 0, 'url'], msg: '不能为空', type: 'value_error' },
        ],
      }),
    )
    const wrapper = await mountView()

    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()

    const items = wrapper.findAll('.validation-errors li')
    expect(items).toHaveLength(2)
    expect(items[0]?.text()).toContain('dsl.steps.0.method')
    expect(items[1]?.text()).toContain('不能为空')
  })

  it('编辑器内容不是合法 JSON:标出语法错误,不发 PATCH', async () => {
    mockDetailGet(DETAIL)
    const wrapper = await mountView()

    wrapper.findComponent(DslEditor).vm.$emit('update:modelValue', '{oops')
    await wrapper.vm.$nextTick()
    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.syntax-error').exists()).toBe(true)
    expect(patch).not.toHaveBeenCalled()
  })

  it('源码有语法错误:阻止切回步骤视图,合法后恢复切换', async () => {
    mockDetailGet(DETAIL)
    const wrapper = await mountView()

    await wrapper.find('#tab-source').trigger('click')
    expect(wrapper.find('#tab-source').classes()).toContain('is-active')

    wrapper.findComponent(DslEditor).vm.$emit('update:modelValue', '{oops')
    await wrapper.vm.$nextTick()
    await wrapper.find('#tab-steps').trigger('click')
    expect(wrapper.find('#tab-source').classes()).toContain('is-active')

    wrapper.findComponent(DslEditor).vm.$emit('update:modelValue', '{"dsl_version":1,"steps":[]}')
    await wrapper.vm.$nextTick()
    await wrapper.find('#tab-steps').trigger('click')
    expect(wrapper.find('#tab-steps').classes()).toContain('is-active')
  })
})
