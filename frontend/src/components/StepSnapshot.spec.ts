import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import StepSnapshot from './StepSnapshot.vue'

import type { VueWrapper } from '@vue/test-utils'

const REQUEST = {
  method: 'POST',
  url: 'http://dev/login',
  headers: { 'X-Token': 'abc' },
  query: { page: '1' },
  body: '{"u":"a"}',
}

const RESPONSE = {
  status_code: 200,
  headers: { 'content-type': 'application/json' },
  body: '{"ok":true}',
  size: 13,
  truncated: false,
}

const ASSERTIONS = [
  { type: 'status_code', operator: 'eq', expected: 200, actual: 200, passed: true, message: null },
  {
    type: 'jsonpath',
    operator: 'eq',
    expected: 'x',
    actual: 'y',
    passed: false,
    message: '值不符',
  },
]

async function mountSnapshot(props: Record<string, unknown>): Promise<VueWrapper> {
  const wrapper = mount(StepSnapshot, { props, global: { plugins: [ElementPlus] } })
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('StepSnapshot', () => {
  it('请求 pane:方法/URL/查询参数/请求头/请求体(变量替换后的实际值)', async () => {
    const wrapper = await mountSnapshot({ request: REQUEST, response: RESPONSE })

    const requestPane = wrapper.findAll('.pane')[0]!
    expect(requestPane.find('.request-line').text()).toContain('POST')
    expect(requestPane.find('.request-line').text()).toContain('http://dev/login')
    expect(requestPane.text()).toContain('page')
    expect(requestPane.text()).toContain('1')
    expect(requestPane.text()).toContain('X-Token')
    expect(requestPane.find('.pre').text()).toContain('"u"')
  })

  it('响应 pane:状态码/大小/响应头/响应体,截断标记可见', async () => {
    const wrapper = await mountSnapshot({
      request: REQUEST,
      response: { ...RESPONSE, truncated: true },
    })

    const responsePane = wrapper.findAll('.pane')[1]!
    expect(responsePane.find('.pane-title').text()).toContain('200')
    expect(responsePane.text()).toContain('13')
    expect(responsePane.text()).toContain('content-type')
    expect(responsePane.find('.pre').text()).toContain('"ok"')
    expect(responsePane.text()).toContain('已截断')
  })

  it('断言逐条 PASS/FAIL(含期望/实际/message),提取变量独立 tab', async () => {
    const wrapper = await mountSnapshot({
      request: REQUEST,
      response: RESPONSE,
      assertions: ASSERTIONS,
      extracted: { token: 'abc' },
    })

    const rows = wrapper.findAll('.assert-row')
    expect(rows.length).toBeGreaterThanOrEqual(2)
    expect(rows[0]?.text()).toContain('PASS')
    expect(rows[0]?.text()).toContain('200')
    expect(rows[1]?.text()).toContain('FAIL')
    expect(rows[1]?.text()).toContain('y')
    expect(rows[1]?.text()).toContain('值不符')

    // Element Plus 的 tab-pane 内容都在 DOM(仅显隐切换),提取行随提取 tab 渲染
    expect(wrapper.text()).toContain('token')
    expect(wrapper.text()).toContain('abc')
  })

  it('缺请求或缺响应:对应 pane 出空态,不炸', async () => {
    const wrapper = await mountSnapshot({ request: null, response: null })

    expect(wrapper.findAll('.pane .el-empty').length).toBe(2)
    expect(wrapper.text()).toContain('未发送请求')
  })

  it('无断言/无提取:空态提示而非空列表', async () => {
    const wrapper = await mountSnapshot({ request: REQUEST, response: RESPONSE })

    expect(wrapper.text()).toContain('断言(0/0)')
    expect(wrapper.text()).toContain('该步骤没有断言')
    expect(wrapper.text()).toContain('提取(0)')
  })

  it('layout 透传到分栏容器(none 时响应 pane 隐藏)', async () => {
    const wrapper = await mountSnapshot({ request: REQUEST, response: RESPONSE, layout: 'none' })

    expect(wrapper.find('.split').classes()).toContain('none')
  })
})
