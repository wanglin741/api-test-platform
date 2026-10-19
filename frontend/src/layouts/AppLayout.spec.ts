import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'

import AppLayout from './AppLayout.vue'

describe('AppLayout', () => {
  it('挂载:渲染侧边栏四项导航', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: AppLayout },
        { path: '/cases', component: { template: '<div />' } },
        { path: '/environments', component: { template: '<div />' } },
        { path: '/plans', component: { template: '<div />' } },
        { path: '/runs', component: { template: '<div />' } },
      ],
    })
    await router.push('/cases')
    await router.isReady()

    const wrapper = mount(AppLayout, {
      global: { plugins: [router, ElementPlus, createPinia()] },
    })

    const items = wrapper.findAll('.el-menu-item')
    // issue #20:导航措辞对齐平台惯例,路由路径不变;#29 增加测试计划
    expect(items.map((i) => i.text())).toEqual(['接口用例', '环境与变量', '测试计划', '测试报告'])
  })
})
