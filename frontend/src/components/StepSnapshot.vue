<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'

import type { components } from '@/api/schema'

type RequestSnapshot = components['schemas']['RequestSnapshotOut']
type ResponseSnapshot = components['schemas']['ResponseSnapshotOut']

/**
 * 步骤明细的公共形状:调试端点(DebugAssertionOut)与报告步骤(StepOut.assertions)
 * 都能结构化赋值进来;报告侧的历史断言没有 message 字段也不受影响。
 */
interface AssertionRow {
  type: string
  operator: string
  expected: unknown
  actual: unknown
  passed: boolean
  message?: string | null
}

/**
 * 请求/响应快照 + 断言/提取明细(前端设计 §6.1/§6.2 两端共用):
 * 调试面板与报告展开明细渲染同一份信息架构。纯展示组件:
 * 格式化失败就原样显示,不猜格式;layout 由宿主决定(调试面板可切,报告固定上下)。
 */
const props = defineProps({
  request: { type: Object as PropType<RequestSnapshot | null>, default: null },
  response: { type: Object as PropType<ResponseSnapshot | null>, default: null },
  assertions: { type: Array as PropType<AssertionRow[]>, default: () => [] },
  extracted: { type: Object as PropType<Record<string, unknown>>, default: () => ({}) },
  layout: {
    type: String as PropType<'vertical' | 'horizontal' | 'none'>,
    default: 'vertical',
  },
})

function pretty(text: string | null | undefined): string {
  if (!text) return ''
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

/** 期望/实际/提取值可能是对象或数组:JSON 序列化展示,别渲染成 [object Object] */
function display(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

const responseBody = computed(() => pretty(props.response?.body))
const requestBody = computed(() => pretty(props.request?.body))

const queryEntries = computed(() => Object.entries(props.request?.query ?? {}))

const assertSummary = computed(() => {
  const passed = props.assertions.filter((a) => a.passed).length
  return { passed, total: props.assertions.length }
})

const extractedEntries = computed(() => Object.entries(props.extracted))
</script>

<template>
  <div class="split" :class="layout">
    <div class="pane">
      <div class="pane-title">实际请求</div>
      <template v-if="request">
        <div class="request-line">
          <el-tag size="small" type="info">{{ request.method }}</el-tag>
          <code class="url">{{ request.url }}</code>
        </div>
        <div v-if="queryEntries.length" class="kv-block">
          <div v-for="[k, v] in queryEntries" :key="k" class="kv-line">
            <span class="k">{{ k }}</span
            >: <span class="v">{{ v }}</span>
          </div>
        </div>
        <div class="kv-block">
          <div v-for="(v, k) in request.headers" :key="k" class="kv-line">
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
        <span v-if="response" class="meta">
          {{ response.status_code ?? '-' }} · {{ response.size ?? 0 }}B
          <el-tag v-if="response.truncated" size="small" type="warning">已截断</el-tag>
        </span>
      </div>
      <template v-if="response">
        <div class="kv-block">
          <div v-for="(v, k) in response.headers ?? {}" :key="k" class="kv-line">
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
      <div v-for="(a, i) in assertions" :key="i" class="assert-row">
        <el-tag :type="a.passed ? 'success' : 'danger'" size="small">
          {{ a.passed ? 'PASS' : 'FAIL' }}
        </el-tag>
        <code class="assert-expr">{{ a.type }} {{ a.operator }} {{ display(a.expected) }}</code>
        <span class="actual">实际:{{ display(a.actual) }}</span>
        <span v-if="a.message" class="hint">{{ a.message }}</span>
      </div>
      <el-empty v-if="assertSummary.total === 0" description="该步骤没有断言" :image-size="60" />
    </el-tab-pane>
    <el-tab-pane :label="`提取(${extractedEntries.length})`">
      <div v-for="[k, v] in extractedEntries" :key="k" class="assert-row">
        <el-tag size="small" type="success">{{ k }}</el-tag>
        <code>{{ display(v) }}</code>
      </div>
      <el-empty v-if="extractedEntries.length === 0" description="未提取变量" :image-size="60" />
    </el-tab-pane>
  </el-tabs>
</template>

<style scoped>
.split {
  display: grid;
  gap: 12px;
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

.meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-weight: normal;
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
