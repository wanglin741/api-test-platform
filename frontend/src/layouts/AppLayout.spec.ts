import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ElementPlus from 'element-plus'

import AppLayout from './AppLayout.vue'

describe('AppLayout', () => {
  it('挂载:渲染侧边栏三项导航', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: AppLayout },
        { path: '/cases', component: { template: '<div />' } },
        { path: '/environments', component: { template: '<div />' } },
        { path: '/runs', component: { template: '<div />' } },
      ],
    })
    await router.push('/cases')
    await router.isReady()

    const wrapper = mount(AppLayout, {
      global: { plugins: [router, ElementPlus] },
    })

    const items = wrapper.findAll('.el-menu-item')
    expect(items.map((i) => i.text())).toEqual(['用例管理', '环境与变量', '执行报告'])
  })
})
