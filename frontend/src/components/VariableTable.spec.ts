import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { components } from '@/api/schema'

import VariableTable from './VariableTable.vue'

type VariableIn = components['schemas']['VariableIn']

const ROW_A: VariableIn = { name: 'a', value: '1', is_secret: false, description: null }
const ROW_B: VariableIn = { name: 'b', value: '2', is_secret: true, description: null }

describe('VariableTable', () => {
  it('渲染行,删除行发新数组', async () => {
    const wrapper = mount(VariableTable, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: [ROW_A, ROW_B] },
    })
    expect(wrapper.findAll('.row')).toHaveLength(2)

    await wrapper.find('.row button').trigger('click')

    // emitted('update:modelValue') 的每条记录是参数数组 [newValue]
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([[ROW_B]])
  })

  it('添加变量:追加空行', async () => {
    const wrapper = mount(VariableTable, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: [] },
    })
    await wrapper.find('.toolbar button').trigger('click')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([
      [{ name: '', value: '', is_secret: false, description: null }],
    ])
  })

  it('行内编辑直接改对象(父组件保存时读同一次数据)', async () => {
    const wrapper = mount(VariableTable, {
      global: { plugins: [ElementPlus] },
      props: { modelValue: [ROW_A] },
    })
    await wrapper.find('.row input').setValue('renamed')

    expect(ROW_A.name).toBe('renamed')
  })
})
