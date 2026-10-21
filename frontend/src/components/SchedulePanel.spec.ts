import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus, { ElMessageBox } from 'element-plus'

import type { ApiClient } from '@/api/client'

import SchedulePanel from './SchedulePanel.vue'

const { get, post, patch, delete: del, push } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    api: { GET: get, POST: post, PATCH: patch, DELETE: del } as unknown as ApiClient,
  }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const SCHEDULE = {
  id: 1,
  project_id: 1,
  name: '每晚全量',
  plan_id: 10,
  cron_expression: '0 2 * * *',
  timezone: 'Asia/Shanghai',
  enabled: true,
  environment_id: null,
  next_run_at: '2026-10-22T18:00:00Z',
  last_run_at: null,
  last_run_id: null,
  last_skip_at: null,
  last_skip_reason: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const ENV_DEV = { id: 3, name: 'dev' }

function mockGets({ schedules = [SCHEDULE] as object[] } = {}) {
  get.mockImplementation(async (path: unknown, opts?: { params?: { query?: object } }) => {
    const url = String(path)
    if (url.includes('/schedules')) {
      const query = (opts?.params ?? { query: {} }).query as { plan_id?: number }
      expect(query.plan_id).toBe(10)
      return okResponse(schedules)
    }
    if (url.includes('/environments')) return okResponse([ENV_DEV])
    return okResponse([])
  })
}

async function mountPanel() {
  setActivePinia(createPinia())
  // el-select 在 jsdom 的弹层定位依赖真实布局:弹层组件一律打桩(同 PlanDetailView.spec)
  const wrapper = mount(SchedulePanel, {
    global: {
      plugins: [ElementPlus],
      stubs: { teleport: true, 'el-select': true, 'el-option': true },
    },
    props: { projectId: 1, planId: 10 },
  })
  await flushPromises()
  return wrapper
}

describe('SchedulePanel(计划详情的定时调度卡片,#36)', () => {
  it('按项目 + 计划加载并渲染列表(名称、cron、下次执行时间、启用态)', async () => {
    mockGets()
    const wrapper = await mountPanel()

    const row = wrapper.find('.schedule-row')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('每晚全量')
    expect(row.text()).toContain('0 2 * * *')
    expect(row.text()).toContain('Asia/Shanghai')
    expect(row.text()).not.toContain('暂无调度')
  })

  it('无调度时显示空态', async () => {
    mockGets({ schedules: [] })
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain('暂无定时调度')
  })

  it('新建:弹窗提交 POST,body 携带 cron/时区/计划', async () => {
    mockGets()
    post.mockResolvedValue(okResponse(SCHEDULE))
    const wrapper = await mountPanel()

    await wrapper.find('.panel-actions button').trigger('click')
    await flushPromises()
    const dialog = wrapper.find('.el-dialog')
    expect(dialog.exists()).toBe(true)

    // 弹窗内改 cron 与名称(时区/环境是 el-select,已 stub,保持默认 Asia/Shanghai)
    await dialog.find('.schedule-name input').setValue('工作日早间')
    await dialog.find('.cron-input input').setValue('30 8 * * 1-5')
    await dialog.find('.el-dialog__footer .el-button--primary').trigger('click')
    await flushPromises()

    const [path, opts] = post.mock.calls[0] as [
      string,
      { body: { name: string; plan_id: number; cron_expression: string; timezone: string } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/schedules')
    expect(opts.body).toEqual({
      name: '工作日早间',
      plan_id: 10,
      cron_expression: '30 8 * * 1-5',
      timezone: 'Asia/Shanghai',
      environment_id: null,
    })
  })

  it('新建:非 5 段 cron 前端粗校验拦截,不发请求', async () => {
    mockGets()
    const wrapper = await mountPanel()

    await wrapper.find('.panel-actions button').trigger('click')
    await flushPromises()
    const dialog = wrapper.find('.el-dialog')
    await dialog.find('.cron-input input').setValue('0 2 * *')
    await dialog.find('.el-dialog__footer .el-button--primary').trigger('click')
    await flushPromises()

    expect(post).not.toHaveBeenCalled()
  })

  it('启停:开关切换 POST enable/disable,响应当作权威行', async () => {
    mockGets()
    const wrapper = await mountPanel()
    post.mockResolvedValue(okResponse({ ...SCHEDULE, enabled: false, next_run_at: null }))

    await wrapper.find('.schedule-row .el-switch').trigger('click')
    await flushPromises()

    const [path] = post.mock.calls[0] as [string]
    expect(path).toBe('/api/v1/schedules/{schedule_id}/disable')
    expect(wrapper.find('.schedule-row').text()).not.toContain('已启用')
  })

  it('删除:确认后 DELETE 并刷新列表', async () => {
    mockGets()
    const wrapper = await mountPanel()
    del.mockResolvedValue(okResponse(undefined))
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue(undefined as never)

    await wrapper.find('.schedule-row .delete-btn').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(del).toHaveBeenCalledWith('/api/v1/schedules/{schedule_id}', {
      params: { path: { schedule_id: 1 } },
    })
    confirmSpy.mockRestore()
  })

  it('编辑:PATCH cron 后用响应行更新(下次执行时间服务端重算)', async () => {
    mockGets()
    const wrapper = await mountPanel()
    patch.mockResolvedValue(
      okResponse({ ...SCHEDULE, cron_expression: '0 5 * * *', next_run_at: '2026-10-22T21:00:00Z' }),
    )

    await wrapper.find('.schedule-row .edit-btn').trigger('click')
    await flushPromises()
    const dialog = wrapper.find('.el-dialog')
    await dialog.find('.cron-input input').setValue('0 5 * * *')
    await dialog.find('.el-dialog__footer .el-button--primary').trigger('click')
    await flushPromises()

    const [path, opts] = patch.mock.calls[0] as [
      string,
      { body: { cron_expression: string } },
    ]
    expect(path).toBe('/api/v1/schedules/{schedule_id}')
    expect(opts.body).toEqual({ cron_expression: '0 5 * * *' })
    expect(wrapper.find('.schedule-row').text()).toContain('0 5 * * *')
  })
})

describe('SchedulePanel 上次执行与跳过原因(#37)', () => {
  const RAN_SCHEDULE = {
    ...SCHEDULE,
    last_run_at: '2026-10-21T02:00:00Z',
    last_run_id: '0b6e2c22-9c9d-4f4e-a1d2-4a5c6b7d8e9f',
    last_skip_at: '2026-10-21T03:00:00Z',
    last_skip_reason: 'overlap',
  }

  it('渲染上次执行时间、跳过原因中文映射,并暴露跳转上次 run 的入口', async () => {
    mockGets({ schedules: [RAN_SCHEDULE] })
    const wrapper = await mountPanel()

    const row = wrapper.find('.schedule-row')
    expect(row.text()).toContain('上次执行')
    expect(row.text()).toContain('上次执行未结束,跳过本次')
    expect(wrapper.find('.last-run-link').exists()).toBe(true)
  })

  it('点击「上次报告」跳转到该 run 的详情路由', async () => {
    mockGets({ schedules: [RAN_SCHEDULE] })
    const wrapper = await mountPanel()

    await wrapper.find('.last-run-link').trigger('click')

    expect(push).toHaveBeenCalledWith('/runs/0b6e2c22-9c9d-4f4e-a1d2-4a5c6b7d8e9f')
  })

  it('未知跳过原因原样展示,不空白', async () => {
    mockGets({ schedules: [{ ...SCHEDULE, last_skip_reason: 'future_reason' }] })
    const wrapper = await mountPanel()

    expect(wrapper.find('.schedule-row').text()).toContain('future_reason')
  })

  it('没有跳过记录时不渲染跳过标签与跳转入口', async () => {
    mockGets()
    const wrapper = await mountPanel()

    expect(wrapper.find('.skip-reason').exists()).toBe(false)
    expect(wrapper.find('.last-run-link').exists()).toBe(false)
  })
})
