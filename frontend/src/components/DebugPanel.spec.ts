import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import DebugPanel from './DebugPanel.vue'

import type { VueWrapper } from '@vue/test-utils'
import type { components } from '@/api/schema'

type StepDebugOut = components['schemas']['StepDebugOut']

const RESULT: StepDebugOut = {
  step_id: 'step-1',
  step_name: 's1',
  status: 'passed',
  duration_ms: 42,
  attempts: 1,
  error_message: null,
  request: {
    method: 'GET',
    url: 'http://127.0.0.1:9999/json',
    headers: { Authorization: 'Bearer x' },
    body: null,
  },
  response: {
    status_code: 200,
    headers: { 'content-type': 'application/json' },
    body: '{"code":0,"data":{"hello":"world"}}',
    size: 36,
    truncated: false,
  },
  extracted: { hello: 'world' },
  assertions: [
    { type: 'status_code', operator: 'eq', expected: 200, actual: 200, passed: true, message: null },
    {
      type: 'jsonpath',
      operator: 'eq',
      expected: 'x',
      actual: 'world',
      passed: false,
      message: null,
    },
  ],
}

async function mountPanel(result: StepDebugOut | null, loading = false): Promise<VueWrapper> {
  const wrapper = mount(DebugPanel, {
    props: { result, loading },
    global: { plugins: [ElementPlus] },
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('DebugPanel', () => {
  it('渲染状态/实际请求/响应快照', async () => {
    const wrapper = await mountPanel(RESULT)

    expect(wrapper.find('.debug-head').text()).toContain('passed')
    expect(wrapper.find('.request-line').text()).toContain('http://127.0.0.1:9999/json')
    // 响应体 pretty-print 后仍含关键字段
    expect(wrapper.find('.pre').text()).toContain('"hello"')
    expect(wrapper.findAll('.pane-title')[1]?.text()).toContain('200')
  })

  it('断言逐条 PASS/FAIL,提取变量独立展示', async () => {
    const wrapper = await mountPanel(RESULT)

    const rows = wrapper.findAll('.assert-row')
    expect(rows).toHaveLength(3) // 2 断言 + 1 提取
    expect(rows[0]?.text()).toContain('PASS')
    expect(rows[1]?.text()).toContain('FAIL')
    expect(rows[1]?.text()).toContain('world') // 实际值
    expect(rows[2]?.text()).toContain('hello')
  })

  it('分栏切换:上下/左右/不分屏', async () => {
    const wrapper = await mountPanel(RESULT)

    expect(wrapper.find('.split').classes()).toContain('vertical')
    const radios = wrapper.find('.layout-switch').findAll('input')
    await radios[1]?.setValue() // 左右
    expect(wrapper.find('.split').classes()).toContain('horizontal')
    await radios[2]?.setValue() // 不分屏
    expect(wrapper.find('.split').classes()).toContain('none')
  })

  it('skipped(无请求无响应):空态提示 + 错误原因', async () => {
    const wrapper = await mountPanel({
      ...RESULT,
      status: 'skipped',
      error_message: '变量 hello 未定义',
      request: null,
      response: null,
      extracted: {},
      assertions: [],
    })

    expect(wrapper.find('.debug-error').text()).toContain('变量 hello 未定义')
    expect(wrapper.findAll('.pane .el-empty').length).toBe(2)
  })

  it('无结果且不在加载中:引导调试', async () => {
    const wrapper = await mountPanel(null)
    expect(wrapper.text()).toContain('调试')
  })
})
