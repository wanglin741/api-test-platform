import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import DslEditor from './DslEditor.vue'
import type { EditorView } from 'codemirror'

describe('DslEditor', () => {
  it('挂载时把 modelValue 渲染进编辑器', () => {
    const wrapper = mount(DslEditor, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: '{"dsl_version": 1}' },
    })

    const content = wrapper.find('.dsl-editor .cm-content')
    expect(content.element.textContent).toBe('{"dsl_version": 1}')
  })

  it('编辑器内输入时向父组件发新文本', async () => {
    const wrapper = mount(DslEditor, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: '' },
    })

    const exposed = wrapper.vm as unknown as { getView: () => EditorView | null }
    const view = exposed.getView()
    expect(view).not.toBeNull()
    view?.dispatch({ changes: { from: 0, insert: '{"a": 1}' } })
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['{"a": 1}'])
  })

  it('外部改 modelValue 时同步回编辑器(如载入详情)', async () => {
    const wrapper = mount(DslEditor, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: '{}' },
    })

    await wrapper.setProps({ modelValue: '{"b": 2}' })
    await wrapper.vm.$nextTick()

    const content = wrapper.find('.dsl-editor .cm-content')
    expect(content.element.textContent).toBe('{"b": 2}')
  })
})
