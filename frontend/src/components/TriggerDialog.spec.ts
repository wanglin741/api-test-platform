import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import type { ApiClient } from '@/api/client'
import { useEnvironmentStore } from '@/stores/environment'
import { useProjectStore } from '@/stores/project'
import { useRunStore } from '@/stores/run'

import TriggerDialog from './TriggerDialog.vue'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { POST: post } as unknown as ApiClient }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const ENV_A = {
  id: 1, project_id: 1, name: 'dev', description: null, base_url: 'http://dev',
  default_headers: {}, auth: null, is_default: false, sort_order: 0,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}
const ENV_B = {
  id: 2, project_id: 1, name: 'staging', description: null, base_url: 'http://staging',
  default_headers: {}, auth: null, is_default: true, sort_order: 1,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}

/** 环境列表由父页面负责加载;测试直接种进 store。 */
async function mountDialog() {
  setActivePinia(createPinia())
  useProjectStore().currentId = 1
  useEnvironmentStore().items = [ENV_A, ENV_B]
  const wrapper = mount(TriggerDialog, {
    global: { plugins: [ElementPlus] },
    props: { caseId: 7, modelValue: true },
  })
  await flushPromises()
  return wrapper
}

describe('TriggerDialog', () => {
  it('打开时默认选中上次用过的环境', async () => {
    localStorage.setItem('apitest.last-env-1', '1')

    await mountDialog()

    expect(useEnvironmentStore().selectedId).toBe(1)
  })

  it('无历史记录时选 is_default 环境', async () => {
    localStorage.clear()

    await mountDialog()

    expect(useEnvironmentStore().selectedId).toBe(2)
  })

  it('提交:用当前选中环境触发,emit triggered 并记住环境', async () => {
    post.mockResolvedValue(okResponse({ run_id: 'r-9', status: 'pending' }))
    const wrapper = await mountDialog()
    useEnvironmentStore().selectedId = 1

    await wrapper.find('.submit-btn').trigger('click')
    await flushPromises()

    const [, opts] = post.mock.calls[0] as [
      string,
      { body: { case_id: number; environment_id: number } },
    ]
    expect(opts.body).toEqual({ case_id: 7, environment_id: 1 })
    expect(wrapper.emitted('triggered')?.at(-1)).toEqual(['r-9'])
    expect(useRunStore().lastEnvId(1)).toBe(1)
  })
})
