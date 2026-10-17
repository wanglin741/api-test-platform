import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import type { ApiClient } from '@/api/client'
import { useProjectStore } from '@/stores/project'

import CasesView from './CasesView.vue'

const { push, get, post } = vi.hoisted(() => ({
  push: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, POST: post } as unknown as ApiClient }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const CASE_A = {
  id: 1,
  project_id: 1,
  name: '登录链路',
  description: null,
  priority: 'P0',
  tags: [],
  current_version: 2,
  created_by: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

async function mountView() {
  setActivePinia(createPinia())
  useProjectStore().currentId = 1
  const wrapper = mount(CasesView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('CasesView', () => {
  it('渲染用例列表(名称/优先级/版本),点击行跳详情', async () => {
    get.mockResolvedValue(okResponse({ items: [CASE_A], total: 1, page: 1, page_size: 20 }))
    const wrapper = await mountView()

    expect(wrapper.find('.row .name').text()).toBe('登录链路')
    expect(wrapper.find('.row .priority').text()).toBe('P0')
    expect(wrapper.find('.row .version').text()).toBe('v2')

    await wrapper.find('.row').trigger('click')
    expect(push).toHaveBeenCalledWith('/cases/1')
  })

  it('新建用例:弹窗提交后跳编辑页', async () => {
    get.mockResolvedValue(okResponse({ items: [], total: 0, page: 1, page_size: 20 }))
    post.mockResolvedValue(okResponse({ ...CASE_A, dsl: {} }))
    const wrapper = await mountView()

    await wrapper.find('.new-btn').trigger('click')
    await wrapper.find('.case-dialog input').setValue('下单链路')
    await wrapper.find('.case-dialog .submit-btn').trigger('click')
    await flushPromises()

    const [, opts] = post.mock.calls[0] as [string, { body: { name: string } }]
    expect(opts.body.name).toBe('下单链路')
    expect(push).toHaveBeenCalledWith('/cases/1')
  })

  it('分页:超出一页时「下一页」可用,点击带 page=2 拉取', async () => {
    get.mockResolvedValue(okResponse({ items: [CASE_A], total: 25, page: 1, page_size: 20 }))
    const wrapper = await mountView()

    const next = wrapper.find('.pager .next')
    expect(next.attributes('disabled')).toBeUndefined()

    await next.trigger('click')
    await flushPromises()

    const [, opts] = get.mock.calls.at(-1) as [string, { params: { query: { page: number } } }]
    expect(opts.params.query.page).toBe(2)
  })
})
