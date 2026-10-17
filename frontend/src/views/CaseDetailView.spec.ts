import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import { ApiError } from '@/api/client'
import type { ApiClient } from '@/api/client'

import CaseDetailView from './CaseDetailView.vue'
import DslEditor from '@/components/DslEditor.vue'

const { push, get, patch } = vi.hoisted(() => ({
  push: vi.fn(),
  get: vi.fn(),
  patch: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '7' } }),
  useRouter: () => ({ push }),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, PATCH: patch } as unknown as ApiClient }
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
  dsl: { dsl_version: 1, steps: [{ name: 'step1' }] },
  created_by: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

async function mountView() {
  setActivePinia(createPinia())
  const wrapper = mount(CaseDetailView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('CaseDetailView', () => {
  it('载入详情:名称与 DSL 渲染进编辑器', async () => {
    get.mockResolvedValue(okResponse(DETAIL))
    const wrapper = await mountView()

    expect((wrapper.find('.header input').element as HTMLInputElement).value).toBe('登录链路')
    expect(wrapper.find('.dsl-editor .cm-content').element.textContent).toContain('"steps"')
  })

  it('保存:PATCH 提交名称 + 解析后的 dsl,版本号随之更新', async () => {
    get.mockResolvedValue(okResponse(DETAIL))
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
    expect(opts.body.dsl).toEqual({ dsl_version: 1, steps: [] })
    expect(wrapper.find('.version').text()).toBe('v3')
  })

  it('改名后保存:新名称随 PATCH 提交', async () => {
    get.mockResolvedValue(okResponse(DETAIL))
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
    get.mockResolvedValue(okResponse(DETAIL))
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
    get.mockResolvedValue(okResponse(DETAIL))
    const wrapper = await mountView()

    wrapper.findComponent(DslEditor).vm.$emit('update:modelValue', '{oops')
    await wrapper.vm.$nextTick()
    await wrapper.find('.save-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.syntax-error').exists()).toBe(true)
    expect(patch).not.toHaveBeenCalled()
  })
})
