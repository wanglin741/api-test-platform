import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import StepDetail from './StepDetail.vue'

import type { CaseDsl, StepSpec } from '@/types/dsl'
import type { VueWrapper } from '@vue/test-utils'

function makeStep(overrides: Partial<StepSpec> = {}): StepSpec {
  return {
    id: 'step-1',
    name: '示例步骤',
    type: 'http',
    enabled: true,
    request: {
      method: 'GET',
      url: 'https://httpbin.org/get',
      headers: { 'X-A': '1' },
      params: { q: 'x' },
      body: null,
      timeout_ms: null,
      follow_redirects: true,
      verify_ssl: true,
    },
    extract: [],
    assert: [],
    retry: { times: 0, interval_ms: 1000, backoff: 'fixed' },
    ...overrides,
  }
}

function makeDsl(steps: StepSpec[]): CaseDsl {
  return {
    dsl_version: 1,
    config: { variables: {}, timeout_ms: 10_000, fail_fast: false },
    steps,
  }
}

async function mountDetail(step: StepSpec): Promise<VueWrapper> {
  const wrapper = mount(StepDetail, {
    props: { modelValue: step },
    global: { plugins: [ElementPlus] },
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('StepDetail', () => {
  it('渲染步骤:名称/方法/URL/请求头/查询参数', async () => {
    const step = makeStep()
    const wrapper = await mountDetail(step)

    expect((wrapper.find('.name input').element as HTMLInputElement).value).toBe('示例步骤')
    expect((wrapper.find('.url input').element as HTMLInputElement).value).toBe(
      'https://httpbin.org/get',
    )
    // 就地编辑传入对象
    await wrapper.find('.name input').setValue('改名')
    await wrapper.find('.url input').setValue('https://api.example.com/x')
    expect(step.name).toBe('改名')
    expect(step.request.url).toBe('https://api.example.com/x')
  })

  it('Body 类型切换:json 文本写入 content,切 form-data 重置为 dict', async () => {
    const step = makeStep()
    const wrapper = await mountDetail(step)

    // el-select 的选择通过组件事件驱动(jsdom 里模拟不了下拉)
    const bodySelect = wrapper.find('.body-type').findComponent({ name: 'ElSelect' })
    await bodySelect.vm.$emit('update:modelValue', 'json')
    await wrapper.vm.$nextTick()
    const textarea = wrapper.find('.body-content textarea')
    expect(textarea.exists()).toBe(true)
    await textarea.setValue('{"a":1}')
    expect(step.request.body?.type).toBe('json')
    expect(step.request.body?.content).toBe('{"a":1}')

    // 切 form-data → content 重置为空 dict,KvEditor 渲染
    await bodySelect.vm.$emit('update:modelValue', 'form-data')
    await wrapper.vm.$nextTick()
    expect(step.request.body?.type).toBe('form-data')
    expect(step.request.body?.content).toEqual({})
    expect(wrapper.find('.body-content.kv-editor').exists()).toBe(true)
  })

  it('提取与断言:添加/删除行写入 step 数组', async () => {
    const step = makeStep()
    const wrapper = await mountDetail(step)

    const extractBtn = wrapper.findAll('.section > .section-title .el-button')[0]
    await extractBtn?.trigger('click')
    expect(step.extract).toHaveLength(1)
    expect(step.extract?.[0]?.type).toBe('jsonpath')

    const assertBtn = wrapper.findAll('.section > .section-title .el-button')[1]
    await assertBtn?.trigger('click')
    expect(step.assert).toHaveLength(1)
    expect(step.assert?.[0]?.type).toBe('status_code')

    await wrapper.findAll('.section')[0]?.find('.el-button--danger').trigger('click') // 删除提取行
    expect(step.extract).toHaveLength(0)
    expect(step.assert).toHaveLength(1) // 断言行不受影响
  })

  it('Body 清空:content 保持空串而非 null(引擎会发出字面量 "null"/"None")', async () => {
    const step = makeStep({
      request: {
        ...makeStep().request,
        body: { type: 'json', content: '{"a":1}' },
      },
    })
    const wrapper = await mountDetail(step)

    const textarea = wrapper.find('.body-content textarea')
    expect(textarea.exists()).toBe(true)
    await textarea.setValue('') // 全选删除

    expect(step.request.body?.content).toBe('')
  })

  it('断言期望值:true/false/null 关键字还原为原生值(引擎 _eq 布尔只与布尔比较)', async () => {
    const step = makeStep({
      assert: [{ type: 'jsonpath', operator: 'eq', expected: true, expression: '$.deleted' }],
    })
    const wrapper = await mountDetail(step)

    const input = wrapper.find('input[placeholder="期望值"]')
    expect((input.element as HTMLInputElement).value).toBe('true')

    await input.setValue('false')
    expect(step.assert?.[0]?.expected).toBe(false)

    await input.setValue('null')
    expect(step.assert?.[0]?.expected).toBeNull()

    await input.setValue('ok')
    expect(step.assert?.[0]?.expected).toBe('ok')
  })

  it('重试与启用:直接写回 step', async () => {
    const step = makeStep()
    const wrapper = await mountDetail(step)

    await wrapper.find('.el-switch input').setValue(false)
    expect(step.enabled).toBe(false)
  })

  it('makeDsl 工厂:工作台可按步骤组装独立 DSL(回归共享可变状态)', () => {
    const a = makeDsl([makeStep()])
    const b = makeDsl([makeStep()])
    const [firstA] = a.steps
    const [firstB] = b.steps
    expect(a).not.toBe(b)
    expect(firstA).toBeDefined()
    expect(firstB).toBeDefined()
    expect(firstA).not.toBe(firstB)
  })
})
