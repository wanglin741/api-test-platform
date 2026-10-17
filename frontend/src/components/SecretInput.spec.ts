import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import SecretInput from './SecretInput.vue'

describe('SecretInput', () => {
  it('默认打码(password),点眼睛切换明文', async () => {
    const wrapper = mount(SecretInput, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: 's3cret' },
    })
    const input = wrapper.find('input')
    expect(input.attributes('type')).toBe('password')

    await wrapper.find('.eye').trigger('click')
    expect(wrapper.find('input').attributes('type')).toBe('text')

    await wrapper.find('.eye').trigger('click')
    expect(wrapper.find('input').attributes('type')).toBe('password')
  })

  it('输入值回传 modelValue', async () => {
    const wrapper = mount(SecretInput, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: '' },
    })
    await wrapper.find('input').setValue('abc')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['abc'])
  })
})
