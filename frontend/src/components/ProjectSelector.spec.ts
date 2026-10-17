import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import type { ApiClient } from '@/api/client'

import ProjectSelector from './ProjectSelector.vue'
import { useProjectStore } from '@/stores/project'

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

describe('ProjectSelector', () => {
  it('渲染项目选项,切换选择持久化', async () => {
    localStorage.clear()
    setActivePinia(createPinia())
    const client = {
      GET: vi.fn(async () => okResponse([{ id: 1, name: 'Alpha' }, { id: 2, name: 'Beta' }])),
    } as unknown as ApiClient
    const store = useProjectStore()
    await store.load(client)
    // el-select 的下拉经 teleport 挂到 body,attachTo 后从 document 断言
    const wrapper = mount(ProjectSelector, {
      attachTo: document.body,
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    await wrapper.find('.el-select').trigger('click')
    await flushPromises()
    const options = Array.from(document.body.querySelectorAll('.el-select-dropdown__item'))
    expect(options.map((o) => o.textContent?.trim())).toEqual(['Alpha', 'Beta'])

    const beta = options[1]
    expect(beta).toBeDefined()
    beta!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(store.currentId).toBe(2)
    expect(localStorage.getItem('apitest.current-project-id')).toBe('2')
    wrapper.unmount()
  })
})
