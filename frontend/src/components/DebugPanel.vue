<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PropType } from 'vue'

import type { components } from '@/api/schema'

type StepDebugOut = components['schemas']['StepDebugOut']

/**
 * 单步调试结果:实际请求(变量替换后)+ 真实响应快照,分栏可切(上下/左右/不分屏);
 * 断言逐条 pass/fail、提取结果独立 tab(调研 §C3,MeterSphere 形态)。
 * 纯展示组件:结果来自同步调试端点,格式化失败就原样显示,不猜格式。
 */
const props = defineProps({
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

function pretty(text: string | null | undefined): string {
  if (!text) return ''
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

const responseBody = computed(() => pretty(props.result?.response?.body))
const requestBody = computed(() => pretty(props.result?.request?.body))

const queryEntries = computed(() => Object.entries(props.result?.request?.query ?? {}))

const assertSummary = computed(() => {
  const list = props.result?.assertions ?? []
  const passed = list.filter((a) => a.passed).length
  return { passed, total: list.length, list }
})

const extractedEntries = computed(() => Object.entries(props.result?.extracted ?? {}))
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

      <div class="split" :class="layout">
        <div class="pane">
          <div class="pane-title">实际请求</div>
          <template v-if="result.request">
            <div class="request-line">
              <el-tag size="small" type="info">{{ result.request.method }}</el-tag>
              <code class="url">{{ result.request.url }}</code>
            </div>
            <div v-if="queryEntries.length" class="kv-block">
              <div v-for="[k, v] in queryEntries" :key="k" class="kv-line">
                <span class="k">{{ k }}</span
                >: <span class="v">{{ v }}</span>
              </div>
            </div>
            <div class="kv-block">
              <div v-for="(v, k) in result.request.headers" :key="k" class="kv-line">
                <span class="k">{{ k }}</span
                >: <span class="v">{{ v }}</span>
              </div>
            </div>
            <pre v-if="requestBody" class="pre">{{ requestBody }}</pre>
          </template>
          <el-empty v-else description="未发送请求" :image-size="60" />
        </div>

        <div class="pane">
          <div class="pane-title">
            响应
            <span v-if="result.response" class="meta">
              {{ result.response.status_code ?? '-' }} · {{ result.response.size ?? 0 }}B
              <el-tag v-if="result.response.truncated" size="small" type="warning">已截断</el-tag>
            </span>
          </div>
          <template v-if="result.response">
            <div class="kv-block">
              <div v-for="(v, k) in result.response.headers ?? {}" :key="k" class="kv-line">
                <span class="k">{{ k }}</span
                >: <span class="v">{{ v }}</span>
              </div>
            </div>
            <pre class="pre">{{ responseBody }}</pre>
          </template>
          <el-empty v-else description="无响应" :image-size="60" />
        </div>
      </div>

      <el-tabs class="result-tabs">
        <el-tab-pane :label="`断言(${assertSummary.passed}/${assertSummary.total})`">
          <div v-for="(a, i) in assertSummary.list" :key="i" class="assert-row">
            <el-tag :type="a.passed ? 'success' : 'danger'" size="small">
              {{ a.passed ? 'PASS' : 'FAIL' }}
            </el-tag>
            <code class="assert-expr">{{ a.type }} {{ a.operator }} {{ a.expected }}</code>
            <span class="actual">实际:{{ a.actual }}</span>
            <span v-if="a.message" class="hint">{{ a.message }}</span>
          </div>
          <el-empty
            v-if="assertSummary.total === 0"
            description="该步骤没有断言"
            :image-size="60"
          />
        </el-tab-pane>
        <el-tab-pane :label="`提取(${extractedEntries.length})`">
          <div v-for="[k, v] in extractedEntries" :key="k" class="assert-row">
            <el-tag size="small" type="success">{{ k }}</el-tag>
            <code>{{ v }}</code>
          </div>
          <el-empty
            v-if="extractedEntries.length === 0"
            description="未提取变量"
            :image-size="60"
          />
        </el-tab-pane>
      </el-tabs>
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

.split {
  display: grid;
  gap: 12px;
  margin-top: 8px;
}

.split.vertical {
  grid-template-columns: 1fr;
}

.split.horizontal {
  grid-template-columns: 1fr 1fr;
}

.split.none {
  grid-template-columns: 1fr;
}

.split.none .pane + .pane {
  display: none;
}

.pane {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  padding: 8px;
  min-width: 0;
}

.pane-title {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.request-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.url {
  word-break: break-all;
}

.kv-block {
  margin-bottom: 6px;
}

.kv-line {
  font-size: 12px;
  line-height: 1.6;
}

.kv-line .k {
  color: var(--el-text-color-secondary);
}

.pre {
  margin: 0;
  padding: 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.result-tabs {
  margin-top: 4px;
}

.assert-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  flex-wrap: wrap;
}

.assert-expr {
  word-break: break-all;
}

.actual {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.hint {
  color: var(--el-color-danger);
  font-size: 12px;
}
</style>
