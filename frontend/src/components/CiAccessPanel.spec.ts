import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'

import type { ApiClient } from '@/api/client'

import CiAccessPanel from './CiAccessPanel.vue'

const { get, post, del } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: { GET: get, POST: post, DELETE: del } as unknown as ApiClient }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const TOKEN = {
  id: 1,
  project_id: 1,
  name: 'Jenkins',
  scopes: ['run:trigger', 'report:read'],
  expires_at: '2026-12-31T00:00:00Z',
  last_used_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const ENV_DEV = { id: 3, name: 'dev' }

function mockGets({ tokens = [TOKEN] as object[] } = {}) {
  get.mockImplementation(async (path: unknown) => {
    const url = String(path)
    if (url.includes('/api-tokens')) return okResponse(tokens)
    if (url.includes('/environments')) return okResponse([ENV_DEV])
    return okResponse([])
  })
}

async function mountPanel(planEnvironmentId: number | null = 3) {
  setActivePinia(createPinia())
  const wrapper = mount(CiAccessPanel, {
    global: {
      plugins: [ElementPlus],
      stubs: { teleport: true, 'el-select': true, 'el-option': true, 'el-date-picker': true },
    },
    props: { projectId: 1, planId: 10, planEnvironmentId },
  })
  await flushPromises()
  return wrapper
}

describe('CiAccessPanel Token 管理(#42)', () => {
  it('按项目加载并渲染 Token 列表(名称/scopes/过期/最近使用),不渲染明文', async () => {
    mockGets()
    const wrapper = await mountPanel()

    const row = wrapper.find('.token-row')
    expect(row.text()).toContain('Jenkins')
    expect(row.text()).toContain('run:trigger')
    expect(row.text()).toContain('report:read')
    expect(row.text()).toContain('2026') // 过期时间本地化展示
    expect(row.text()).toContain('从未使用')
    expect(row.text()).not.toContain('at_')

    const [path, opts] = get.mock.calls.find((c) => String(c[0]).includes('/api-tokens')) as [
      string,
      { params: { path: { project_id: number } } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/api-tokens')
    expect(opts.params.path.project_id).toBe(1)
  })

  it('空列表显示空态', async () => {
    mockGets({ tokens: [] })
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain('暂无 API Token')
  })

  it('创建:POST 携带名称/默认 scopes/可选过期;明文只在一次性弹窗出现', async () => {
    mockGets()
    post.mockResolvedValue(okResponse({ ...TOKEN, token: 'at_secret_once' }))
    const wrapper = await mountPanel()

    await wrapper.find('.create-btn').trigger('click')
    await flushPromises()
    await wrapper.find('.token-name-input input').setValue('GitLab')
    await wrapper.find('.create-dialog .el-dialog__footer .el-button--primary').trigger('click')
    await flushPromises()

    const [path, opts] = post.mock.calls[0] as [
      string,
      { body: { name: string; scopes: string[]; expires_at: string | null } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/api-tokens')
    expect(opts.body).toEqual({
      name: 'GitLab',
      scopes: ['run:trigger', 'report:read'],
      expires_at: null,
    })

    // 明文仅出现在一次性弹窗的只读输入里;列表行不含明文
    const input = wrapper.find('.plaintext-input input')
    expect(input.exists()).toBe(true)
    expect((input.element as HTMLInputElement).value).toBe('at_secret_once')
    expect((input.element as HTMLInputElement).readOnly).toBe(true)
    expect(wrapper.find('.token-row').text()).not.toContain('at_secret_once')
    expect(wrapper.find('.plaintext-warning').text()).toContain('只显示一次')
  })

  it('关闭一次性明文弹窗后明文从界面清除', async () => {
    mockGets()
    post.mockResolvedValue(okResponse({ ...TOKEN, token: 'at_secret_once' }))
    const wrapper = await mountPanel()

    await wrapper.find('.create-btn').trigger('click')
    await flushPromises()
    await wrapper.find('.token-name-input input').setValue('GitLab')
    await wrapper.find('.create-dialog .el-dialog__footer .el-button--primary').trigger('click')
    await flushPromises()
    expect(wrapper.find('.plaintext-input').exists()).toBe(true)

    // 关闭弹窗(v-model 置 false):明文随一次性弹窗一起清掉
    await wrapper
      .find('.plaintext-dialog .el-dialog__footer .el-button:last-child')
      .trigger('click')
    await flushPromises()

    expect(wrapper.find('.plaintext-input').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('at_secret_once')
  })

  it('复制明文:写入剪贴板', async () => {
    const writeText = vi.fn(async () => {})
    Object.assign(navigator, { clipboard: { writeText } })
    mockGets()
    post.mockResolvedValue(okResponse({ ...TOKEN, token: 'at_secret_once' }))
    const wrapper = await mountPanel()

    await wrapper.find('.create-btn').trigger('click')
    await flushPromises()
    await wrapper.find('.token-name-input input').setValue('GitLab')
    await wrapper.find('.create-dialog .el-dialog__footer .el-button--primary').trigger('click')
    await flushPromises()
    await wrapper.find('.copy-plaintext').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('at_secret_once')
  })

  it('撤销:二次确认后 DELETE 并刷新列表', async () => {
    mockGets()
    del.mockResolvedValue(okResponse(undefined))
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue(undefined as never)
    const wrapper = await mountPanel()

    await wrapper.find('.revoke-btn').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(del).toHaveBeenCalledWith('/api/v1/api-tokens/{token_id}', {
      params: { path: { token_id: 1 } },
    })
    confirmSpy.mockRestore()
  })
})

describe('CiAccessPanel curl 片段(#42)', () => {
  it('按当前项目/计划/环境生成,不含 trigger_mode,含 Idempotency-Key 占位', async () => {
    mockGets()
    const wrapper = await mountPanel(3)

    const snippet = wrapper.find('.curl-snippet').text()
    expect(snippet).toContain('/api/v1/projects/1/runs')
    expect(snippet).toContain('Authorization: Bearer')
    expect(snippet).toContain('"plan_id": 10')
    expect(snippet).toContain('"environment_id": 3')
    expect(snippet).toContain('Idempotency-Key')
    expect(snippet).not.toContain('trigger_mode')
  })

  it('清空环境选择后片段省略 environment_id(沿用计划默认环境)', async () => {
    mockGets()
    const wrapper = await mountPanel(3)
    expect(wrapper.find('.curl-snippet').text()).toContain('"environment_id": 3')

    // 可清除 select 清空时给 undefined
    ;(wrapper.findComponent('.curl-env') as VueWrapper).vm.$emit('update:modelValue', undefined)
    await flushPromises()

    const snippet = wrapper.find('.curl-snippet').text()
    expect(snippet).not.toContain('environment_id')
    expect(snippet).toContain('"plan_id": 10')
  })

  it('复制 curl:写入剪贴板;失败时提示', async () => {
    const writeText = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('denied'))
    Object.assign(navigator, { clipboard: { writeText } })
    const errorSpy = vi.spyOn(ElMessage, 'error')
    mockGets()
    const wrapper = await mountPanel(3)

    await wrapper.find('.copy-curl').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith(wrapper.find('.curl-snippet').text())

    await wrapper.find('.copy-curl').trigger('click')
    await flushPromises()
    expect(errorSpy).toHaveBeenCalledWith('复制失败,请手动选择')

    errorSpy.mockRestore()
  })
})
