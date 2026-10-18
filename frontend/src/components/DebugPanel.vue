<script setup lang="ts">
import { ref } from 'vue'
import type { PropType } from 'vue'

import StepSnapshot from '@/components/StepSnapshot.vue'

import type { components } from '@/api/schema'

type StepDebugOut = components['schemas']['StepDebugOut']

/**
 * 单步调试结果面板:状态头(耗时/尝试次数)+ 分栏切换;
 * 请求/响应/断言/提取的渲染共用 StepSnapshot(与报告展开明细同一信息架构)。
 * 纯展示组件:结果来自同步调试端点。
 */
defineProps({
  result: {
    type: Object as PropType<StepDebugOut | null>,
    default: null,
  },
  loading: { type: Boolean, default: false },
})

// vertical=上下(默认) horizontal=左右 none=不分屏
const layout = ref<'vertical' | 'horizontal' | 'none'>('vertical')

const STATUS_TAG: Record<string, 'success' | 'danger' | 'warning' | 'info'> = {
  passed: 'success',
  failed: 'danger',
  error: 'danger',
  skipped: 'info',
}
</script>

<template>
  <div v-loading="loading" class="debug-panel">
    <template v-if="result">
      <div class="debug-head">
        <el-tag :type="STATUS_TAG[result.status] ?? 'info'" size="small">{{
          result.status
        }}</el-tag>
        <span class="meta">{{ result.duration_ms ?? 0 }}ms · 第 {{ result.attempts }} 次尝试</span>
        <span class="layout-switch">
          分栏:
          <el-radio-group v-model="layout" size="small">
            <el-radio-button value="vertical">上下</el-radio-button>
            <el-radio-button value="horizontal">左右</el-radio-button>
            <el-radio-button value="none">不分屏</el-radio-button>
          </el-radio-group>
        </span>
      </div>

      <el-alert
        v-if="result.error_message"
        class="debug-error"
        type="warning"
        :closable="false"
        :title="result.error_message"
      />

      <StepSnapshot
        :request="result.request ?? null"
        :response="result.response ?? null"
        :assertions="result.assertions"
        :extracted="result.extracted"
        :layout="layout"
        class="debug-snapshot"
      />
    </template>
    <el-empty v-else-if="!loading" description="点击「调试」执行当前步骤" :image-size="80" />
  </div>
</template>

<style scoped>
.debug-panel {
  border-top: 1px solid var(--el-border-color-lighter);
  margin-top: 12px;
  padding-top: 8px;
  min-height: 120px;
}

.debug-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.layout-switch {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.debug-error {
  margin-top: 8px;
}

.debug-snapshot {
  margin-top: 8px;
}
</style>
