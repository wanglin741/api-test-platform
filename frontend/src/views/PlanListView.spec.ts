import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus, { ElMessageBox } from 'element-plus'

import type { ApiClient } from '@/api/client'
import { useProjectStore } from '@/stores/project'

import PlanListView from './PlanListView.vue'

const { get, post, del, push } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, POST: post, DELETE: del } as unknown as ApiClient }
})

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return { ...actual, ElMessageBox: { ...actual.ElMessageBox, confirm: vi.fn() } }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const PLAN_A = {
  id: 1,
  project_id: 1,
  name: '每晚回归',
  description: null,
  environment_id: null,
  execute_mode: 'serial',
  concurrency: 1,
  stop_on_fail: false,
  item_count: 3,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

async function mountView() {
  setActivePinia(createPinia())
  useProjectStore().currentId = 1
  const wrapper = mount(PlanListView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('PlanListView', () => {
  it('渲染计划列表(名称/执行模式/用例数),点击行进详情', async () => {
    get.mockResolvedValue(okResponse([PLAN_A]))
    const wrapper = await mountView()

    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.text()).toContain('每晚回归')
    expect(rows[0]!.text()).toContain('串行')
    expect(rows[0]!.text()).toContain('3')

    await rows[0]!.trigger('click')
    expect(push).toHaveBeenCalledWith('/plans/1')
  })

  it('新建计划:POST 后跳详情页继续挑用例', async () => {
    get.mockResolvedValue(okResponse([PLAN_A]))
    post.mockResolvedValue(okResponse({ ...PLAN_A, id: 9, name: '冒烟' }))
    const wrapper = await mountView()

    await wrapper.find('.bar button').trigger('click') // 新建计划
    await wrapper.find('input').setValue('冒烟')
    await wrapper.find('.el-dialog__footer button.el-button--primary').trigger('click')
    await flushPromises()

    const [path, opts] = post.mock.calls[0] as [string, { body: { name: string } }]
    expect(path).toBe('/api/v1/projects/{project_id}/plans')
    expect(opts.body.name).toBe('冒烟')
    expect(push).toHaveBeenCalledWith('/plans/9')
  })

  it('删除计划:确认后 DELETE 并重载', async () => {
    get.mockResolvedValue(okResponse([PLAN_A]))
    del.mockResolvedValue(okResponse(undefined))
    const wrapper = await mountView()

    vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as never)
    await wrapper.find('.el-table__row .el-button--danger').trigger('click')
    await flushPromises()

    expect(ElMessageBox.confirm).toHaveBeenCalled()
    const [path] = del.mock.calls[0] as [string]
    expect(path).toBe('/api/v1/plans/{plan_id}')
  })
})
