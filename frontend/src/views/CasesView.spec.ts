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
  folder_id: 2,
  name: '登录链路',
  description: null,
  priority: 'P0',
  tags: [],
  current_version: 2,
  created_by: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

const FOLDERS = [
  {
    id: 2,
    project_id: 1,
    scope: 'case',
    parent_id: null,
    name: '登录链路',
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

/** GET 按路径分路由:/folders 给目录,/cases 给用例分页。 */
function routeGet(casesBody: unknown, folders = FOLDERS) {
  get.mockImplementation(async (path: string) => {
    if (path.includes('/folders')) return okResponse(folders)
    return okResponse(casesBody)
  })
}

async function mountView() {
  setActivePinia(createPinia())
  useProjectStore().currentId = 1
  const wrapper = mount(CasesView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('CasesView', () => {
  it('左树右表:树渲染目录,右表渲染用例并显示所属目录', async () => {
    routeGet({ items: [CASE_A], total: 1, page: 1, page_size: 20 })
    const wrapper = await mountView()

    expect(wrapper.find('.tree-pane').text()).toContain('登录链路')
    expect(wrapper.find('.row .name').text()).toBe('登录链路')
    expect(wrapper.find('.row .priority').text()).toBe('P0')
    expect(wrapper.find('.row .version').text()).toBe('v2')
    expect(wrapper.find('.row .folder').text()).toBe('登录链路')

    await wrapper.find('.row').trigger('click')
    expect(push).toHaveBeenCalledWith('/cases/1')
  })

  it('folder_id 为空的用例显示「未分类」', async () => {
    routeGet({ items: [{ ...CASE_A, folder_id: null }], total: 1, page: 1, page_size: 20 })
    const wrapper = await mountView()

    expect(wrapper.find('.row .folder').text()).toBe('未分类')
  })

  it('选中目录后按 folder_id 拉取(含子目录由后端处理),选「全部用例」不带过滤', async () => {
    routeGet({ items: [CASE_A], total: 1, page: 1, page_size: 20 })
    const wrapper = await mountView()
    get.mockClear()

    await wrapper.findAll('.node-row')[1]?.trigger('click') // 登录链路
    await flushPromises()
    let [, opts] = get.mock.calls.at(-1) as [
      string,
      { params: { query: Record<string, number | undefined> } },
    ]
    expect(opts?.params.query.folder_id).toBe(2)

    await wrapper.find('.all-row').trigger('click')
    await flushPromises()
    ;[, opts] = get.mock.calls.at(-1) as [
      string,
      { params: { query: Record<string, number | undefined> } },
    ]
    expect(opts?.params.query.folder_id).toBeUndefined()
  })

  it('新建用例:弹窗默认带当前目录,提交携带 folder_id', async () => {
    routeGet({ items: [], total: 0, page: 1, page_size: 20 })
    post.mockResolvedValue(okResponse({ ...CASE_A, dsl: {} }))
    const wrapper = await mountView()

    // 先选中目录 2,再打开弹窗:默认落在该目录
    await wrapper.findAll('.node-row')[1]?.trigger('click')
    await flushPromises()
    await wrapper.find('.new-btn').trigger('click')
    await wrapper.find('.case-dialog input').setValue('下单链路')
    await wrapper.find('.case-dialog .submit-btn').trigger('click')
    await flushPromises()

    const [, opts] = post.mock.calls[0] as [string, { body: { name: string; folder_id: number } }]
    expect(opts.body.name).toBe('下单链路')
    expect(opts.body.folder_id).toBe(2)
    expect(push).toHaveBeenCalledWith('/cases/1')
  })

  it('分页:超出一页时「下一页」可用,点击带 page=2 拉取', async () => {
    routeGet({ items: [CASE_A], total: 25, page: 1, page_size: 20 })
    const wrapper = await mountView()

    const next = wrapper.find('.pager .next')
    expect(next.attributes('disabled')).toBeUndefined()

    await next.trigger('click')
    await flushPromises()

    const [, opts] = get.mock.calls.at(-1) as [
      string,
      { params: { query: { page: number; folder_id?: number } } },
    ]
    expect(opts.params.query.page).toBe(2)
    expect(opts.params.query.folder_id).toBeUndefined()
  })
})
