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

const ENV_A = {
  id: 3, project_id: 1, name: 'dev', description: null, base_url: 'http://dev',
  default_headers: {}, auth: null, is_default: true, sort_order: 0,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}

const RUN_A = {
  run_id: 'r-1', project_id: 1, case_id: 7, case_version: 2, environment_id: 3,
  trigger_mode: 'manual', status: 'success', duration_ms: 120, summary: null,
  created_at: '2026-01-02T00:00:00Z', started_at: null, finished_at: null,
}
const RUN_B = {
  run_id: 'r-2', project_id: 1, case_id: 8, case_version: 1, environment_id: 3,
  trigger_mode: 'manual', status: 'failed', duration_ms: 60, summary: null,
  created_at: '2026-01-01T00:00:00Z', started_at: null, finished_at: null,
}

async function mountView() {
  setActivePinia(createPinia())
  useProjectStore().currentId = 1
  const wrapper = mount(RunsView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

describe('RunsView', () => {
  it('渲染报告列表(状态/用例/耗时/时间),点击行跳详情', async () => {
    get.mockResolvedValue(okResponse({ items: [RUN_A, RUN_B], total: 2, limit: 20, offset: 0 }))
    const wrapper = await mountView()

    const rows = wrapper.findAll('.row')
    expect(rows).toHaveLength(2)
    expect(rows[0]?.text()).toContain('r-1')
    expect(rows[0]?.text()).toContain('success')
    expect(rows[1]?.text()).toContain('failed')

    await rows[0]!.trigger('click')
    expect(push).toHaveBeenCalledWith('/runs/r-1')
  })

  it('再次执行:复用执行弹窗(带该行的 case_id)', async () => {
    get.mockImplementation(async (path: unknown) =>
      okResponse(
        String(path).includes('environments')
          ? [ENV_A]
          : { items: [RUN_A], total: 1, limit: 20, offset: 0 },
      ),
    )
    post.mockResolvedValue(okResponse({ run_id: 'r-9', status: 'pending' }))
    const wrapper = await mountView()

    await wrapper.find('.row .rerun-btn').trigger('click')
    await flushPromises()
    // 弹窗已打开,提交后应触发执行
    await wrapper.find('.submit-btn').trigger('click')
    await flushPromises()

    const [path, opts] = post.mock.calls[0] as [
      string,
      { body: { case_id: number } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/runs')
    expect(opts.body.case_id).toBe(7)
    expect(push).toHaveBeenCalledWith('/runs/r-9')
  })
})
