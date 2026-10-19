import { createRouter, createWebHistory } from 'vue-router'

import AppLayout from '@/layouts/AppLayout.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', redirect: '/cases' },
        { path: 'cases', name: 'cases', component: () => import('@/views/CasesView.vue') },
        {
          path: 'cases/:id',
          name: 'case-detail',
          component: () => import('@/views/CaseDetailView.vue'),
        },
        {
          path: 'environments',
          name: 'environments',
          component: () => import('@/views/EnvironmentsView.vue'),
        },
        { path: 'plans', name: 'plans', component: () => import('@/views/PlanListView.vue') },
        {
          path: 'plans/:id',
          name: 'plan-detail',
          component: () => import('@/views/PlanDetailView.vue'),
        },
        { path: 'runs', name: 'runs', component: () => import('@/views/RunsView.vue') },
        {
          path: 'runs/:id',
          name: 'run-detail',
          component: () => import('@/views/RunDetailView.vue'),
        },
      ],
    },
  ],
})

export default router
