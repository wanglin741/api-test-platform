<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import { TERMINAL_STATUSES, useRunStore } from '@/stores/run'

import type { components } from '@/api/schema'

type StepOut = components['schemas']['StepOut']

interface AssertionRow {
  type: string
  operator: string
  expected: unknown
  actual: unknown
  passed: boolean
  message?: string | null
}

const STATUS_TAG: Record<string, 'success' | 'danger' | 'warning' | 'info' | 'primary'> = {
  success: 'success',
  passed: 'success',
  failed: 'danger',
  error: 'danger',
  stopped: 'warning',
  stopping: 'warning',
  running: 'primary',
  pending: 'info',
}

const STEP_DOT: Record<string, string> = {
  passed: 'dot-passed',
  failed: 'dot-failed',
  error: 'dot-error',
  skipped: 'dot-skipped',
}

function statusTag(status: string) {
  return STATUS_TAG[status] ?? 'info'
}

function routeParamsId(raw: string | string[] | undefined): string {
  return Array.isArray(raw) ? (raw[0] ?? '') : (raw ?? '')
}

const route = useRoute()
const runStore = useRunStore()

const runId = routeParamsId(route.params.id)

onMounted(() => {
  // 报告详情是单例:先清掉上一条 run 的残留,再开始拉新报告
  runStore.reset()
  runStore.startPolling(runId, api)
})

onBeforeUnmount(() => {
  runStore.stopPolling()
})

async function cancel() {
  try {
    await runStore.cancel(runId, api)
  } catch {
    // 已结束(409)等错误已由全局拦截器弹出
  }
}

function assertionRows(step: StepOut): AssertionRow[] {
  return step.assertions as unknown as AssertionRow[]
}

function assertionText(a: AssertionRow): string {
  return `${a.type} ${a.operator} ${JSON.stringify(a.expected)} → ${JSON.stringify(a.actual)}`
}

function extractedEntries(step: StepOut): [string, unknown][] {
  return Object.entries(step.extracted_variables ?? {})
}

// 快照明细按步骤展开/收起(请求头/体 + 响应体可能很长)
const expanded = ref<Set<number>>(new Set())

function toggleSnapshot(step: StepOut): void {
  const next = new Set(expanded.value)
  if (next.has(step.id)) next.delete(step.id)
  else next.add(step.id)
  expanded.value = next
}

function hasSnapshot(step: StepOut): boolean {
  return step.request !== null || step.response !== null
}
</script>

<template>
  <div v-if="!runStore.run" class="loading" v-loading="true" />
  <div v-else class="page">
    <div class="header">
      <h2>
        执行报告 <small>{{ runStore.run.run_id }}</small>
      </h2>
      <el-tag class="status-tag" :type="statusTag(runStore.run.status)" size="large">
        {{ runStore.run.status }}
      </el-tag>
      <el-button
        v-if="runStore.run && !TERMINAL_STATUSES.has(runStore.run.status)"
        class="cancel-btn"
        type="danger"
        plain
        @click="cancel"
      >
        取消执行
      </el-button>
    </div>

    <div class="meta">
      用例 #{{ runStore.run.case_id }}(v{{ runStore.run.case_version }})· 环境 #{{
        runStore.run.environment_id
      }}
      · {{ runStore.run.trigger_mode }} · {{ runStore.run.created_at }}
      <template v-if="runStore.run.duration_ms !== null">
        · {{ runStore.run.duration_ms }}ms
      </template>
    </div>

    <div v-if="runStore.run.summary" class="summary">
      <span>共 {{ runStore.run.summary.total_steps }} 步</span>
      <span class="passed">通过 {{ runStore.run.summary.passed }}</span>
      <span class="failed">失败 {{ runStore.run.summary.failed }}</span>
      <span class="errored">出错 {{ runStore.run.summary.errored }}</span>
      <span class="skipped">跳过 {{ runStore.run.summary.skipped }}</span>
    </div>

    <el-alert
      v-if="runStore.stepsTruncated"
      class="truncated"
      type="warning"
      :closable="false"
      title="步骤明细超出展示上限,仅显示部分"
    />

    <div class="timeline">
      <div v-for="step in runStore.steps" :key="step.id" class="step-card">
        <div class="step-head">
          <span class="dot" :class="STEP_DOT[step.status] ?? 'dot-pending'" />
          <span class="step-name">{{ step.step_index + 1 }}. {{ step.step_name }}</span>
          <el-tag :type="statusTag(step.status)" size="small">{{ step.status }}</el-tag>
          <span class="assertion-count"
            >断言 {{ step.assertion_passed }}/{{ step.assertion_total }}</span
          >
          <span class="duration">{{ step.duration_ms }}ms</span>
        </div>

        <div v-if="step.request" class="request">
          {{ step.request.method }} {{ step.request.url }}
        </div>
        <div v-if="step.error_message" class="error-message">{{ step.error_message }}</div>
        <div v-if="extractedEntries(step).length" class="extracted">
          <span v-for="[key, value] in extractedEntries(step)" :key="key" class="extracted-item">
            {{ key }}={{ String(value) }}
          </span>
        </div>

        <ul v-if="step.assertions.length" class="assertions">
          <li v-for="(a, i) in assertionRows(step)" :key="i" :class="a.passed ? 'ok' : 'bad'">
            {{ assertionText(a) }}
            <template v-if="a.message"> — {{ a.message }}</template>
          </li>
        </ul>

        <template v-if="hasSnapshot(step)">
          <el-button class="snapshots-toggle" text size="small" @click="toggleSnapshot(step)">
            {{ expanded.has(step.id) ? '收起快照' : '请求/响应快照' }}
          </el-button>
          <div v-if="expanded.has(step.id)" class="snapshots">
            <template v-if="step.request">
              <div class="snap-title">请求</div>
              <pre class="snap-body"
                >{{ step.request.method }} {{ step.request.url }}
{{
                  Object.entries(step.request.headers ?? {})
                    .map(([k, v]) => `${k}: ${v}`)
                    .join('\n')
                }}

{{ step.request.body ?? '' }}</pre>
            </template>
            <template v-if="step.response">
              <div class="snap-title">
                响应
                <el-tag v-if="step.response.truncated" type="warning" size="small">
                  响应体已截断
                </el-tag>
                <span v-if="step.response.size !== null" class="snap-size">
                  {{ step.response.size }} bytes
                </span>
              </div>
              <pre class="snap-body"
                >{{ step.response.status_code ?? '-' }}
{{
                  Object.entries(step.response.headers ?? {})
                    .map(([k, v]) => `${k}: ${v}`)
                    .join('\n')
                }}

{{ step.response.body ?? '' }}</pre>
            </template>
          </div>
        </template>
      </div>
      <div v-if="!runStore.steps.length" class="none">暂无步骤明细</div>
    </div>
  </div>
</template>

<style scoped>
.header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header h2 {
  margin: 0;
  font-size: 18px;
}

.header small {
  color: var(--el-text-color-secondary);
  font-weight: normal;
}

.meta,
.summary {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-top: 8px;
}

.summary {
  display: flex;
  gap: 16px;
}

.summary .passed {
  color: var(--el-color-success);
}

.summary .failed,
.summary .errored {
  color: var(--el-color-danger);
}

.truncated {
  margin-top: 12px;
}

.timeline {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.step-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 14px;
}

.step-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.dot-passed {
  background: var(--el-color-success);
}

.dot-failed,
.dot-error {
  background: var(--el-color-danger);
}

.dot-skipped {
  background: var(--el-color-info);
}

.dot-pending {
  background: var(--el-border-color);
}

.assertion-count {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.duration {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.request {
  font-family: monospace;
  font-size: 12px;
  margin-top: 6px;
  color: var(--el-text-color-regular);
}

.error-message {
  margin-top: 6px;
  color: var(--el-color-danger);
  font-size: 13px;
}

.extracted {
  margin-top: 6px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.extracted-item {
  font-family: monospace;
  font-size: 12px;
  background: var(--el-fill-color-light);
  border-radius: 3px;
  padding: 1px 6px;
}

.assertions {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 12px;
}

.assertions .ok {
  color: var(--el-color-success);
}

.assertions .bad {
  color: var(--el-color-danger);
}

.snapshots {
  margin-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
  padding-top: 8px;
}

.snap-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.snap-size {
  font-family: monospace;
}

.snap-body {
  margin: 4px 0 12px;
  padding: 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.none {
  color: var(--el-text-color-secondary);
  padding: 16px 0;
}

.loading {
  min-height: 300px;
}
</style>
