<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import { useRunStore } from '@/stores/run'

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

const TERMINAL = new Set(['success', 'failed', 'error', 'stopped'])

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

function assertionText(a: AssertionRow): string {
  return `${a.type} ${a.operator} ${JSON.stringify(a.expected)} → ${JSON.stringify(a.actual)}`
}

function requestLine(step: StepOut): string {
  return step.request ? `${step.request.method} ${step.request.url}` : ''
}
</script>

<template>
  <div v-if="!runStore.run" class="loading" v-loading="true" />
  <div v-else class="page">
    <div class="header">
      <h2>执行报告 <small>{{ runStore.run.run_id }}</small></h2>
      <el-tag class="status-tag" :type="statusTag(runStore.run.status)" size="large">
        {{ runStore.run.status }}
      </el-tag>
      <el-button
        v-if="runStore.run && !TERMINAL.has(runStore.run.status)"
        class="cancel-btn"
        type="danger"
        plain
        @click="cancel"
      >
        取消执行
      </el-button>
    </div>

    <div class="meta">
      用例 #{{ runStore.run.case_id }}(v{{ runStore.run.case_version }})·
      环境 #{{ runStore.run.environment_id }} ·
      {{ runStore.run.trigger_mode }} · {{ runStore.run.created_at }}
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
          <span class="duration">{{ step.duration_ms }}ms</span>
        </div>

        <div v-if="requestLine(step)" class="request">{{ requestLine(step) }}</div>
        <div v-if="step.error_message" class="error-message">{{ step.error_message }}</div>

        <ul v-if="step.assertions.length" class="assertions">
          <li v-for="(a, i) in (step.assertions as unknown as AssertionRow[])" :key="i"
            :class="a.passed ? 'ok' : 'bad'">
            {{ assertionText(a) }}
            <template v-if="a.message"> — {{ a.message }}</template>
          </li>
        </ul>
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

.summary .failed {
  color: var(--el-color-danger);
}

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

.dot-failed {
  background: var(--el-color-danger);
}

.dot-error {
  background: var(--el-color-danger);
}

.dot-skipped {
  background: var(--el-color-info);
}

.dot-pending {
  background: var(--el-border-color);
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

.none {
  color: var(--el-text-color-secondary);
  padding: 16px 0;
}

.loading {
  min-height: 300px;
}
</style>
