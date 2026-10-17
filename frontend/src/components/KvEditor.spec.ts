import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import KvEditor from './KvEditor.vue'

import type { VueWrapper } from '@vue/test-utils'

async function mountEditor(modelValue: Record<string, unknown>): Promise<VueWrapper> {
  const wrapper = mount(KvEditor, {
    props: { modelValue, keyPlaceholder: 'K', valuePlaceholder: 'V' },
    global: { plugins: [ElementPlus] },
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('KvEditor', () => {
  it('行内编辑:值直接写进 dict(字符串化),键名渲染', async () => {
    const model: Record<string, unknown> = { token: 'abc' }
    const wrapper = await mountEditor(model)

    expect(wrapper.findAll('.row')).toHaveLength(1)
    await wrapper.find('.row .value input').setValue('changed')
    expect(model.token).toBe('changed')
  })

  it('改键名:值搬原始引用,类型不动(未触碰的数字/布尔保持原类型)', async () => {
    const model: Record<string, unknown> = { port: 8080, debug: true }
    const wrapper = await mountEditor(model)

    const keys = wrapper.findAll('.row .key input')
    await keys[0]?.setValue('PORT')
    await keys[1]?.setValue('DEBUG')

    expect(model).toEqual({ PORT: 8080, DEBUG: true })
    expect(model.PORT).toBe(8080)
    expect(model.DEBUG).toBe(true)
  })

  it('空键新行:补上键名后才写入 dict', async () => {
    const model: Record<string, unknown> = {}
    const wrapper = await mountEditor(model)

    await wrapper.find('button').trigger('click') // 添加行
    expect(model).toEqual({})

    await wrapper.find('.row .key input').setValue('name')
    await wrapper.find('.row .value input').setValue('x')
    expect(model).toEqual({ name: 'x' })
  })

  it('删除行:同步删 dict 键', async () => {
    const model: Record<string, unknown> = { a: 1 }
    const wrapper = await mountEditor(model)

    await wrapper.find('.row button').trigger('click')
    expect(model).toEqual({})
    expect(wrapper.findAll('.row')).toHaveLength(0)
  })
})
