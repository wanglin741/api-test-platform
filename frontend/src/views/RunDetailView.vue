<script setup lang="ts">
import { ArrowRight } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import StepSnapshot from '@/components/StepSnapshot.vue'
import { TERMINAL_STATUSES, useRunStore } from '@/stores/run'
import { gatePassed, triggerModeLabel } from '@/lib/format'

import type { components } from '@/api/schema'

type StepOut = components['schemas']['StepOut']

/** StepSnapshot 断言行的公共形状(报告侧历史断言为宽松 dict,需收窄) */
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
  // 报告详情是单例:先清掉上一条 run 的残留,再开始拉新报告。
  // 事件流为主(SSE);无 EventSource 的环境由 store 内部降级为轮询
  runStore.reset()
  void runStore.startStream(runId, api)
})

onBeforeUnmount(() => {
  runStore.stopStream()
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

// ── 统计卡(前端设计 §6.2):键与后端落库的 run.summary 一致 ──────────
// 运行中 summary 尚未落库,显示 — 而不是误显 0
function statValue(key: 'total' | 'passed' | 'failed' | 'error' | 'skipped'): number | '—' {
  const summary = runStore.run?.summary
  if (summary === null || summary === undefined) return '—'
  const value = summary[key]
  return typeof value === 'number' ? value : 0
}

const durationText = () => {
  const ms = runStore.run?.duration_ms
  return typeof ms === 'number' ? `${ms}ms` : '—'
}

// 门禁标签:summary.gate_passed 键存在才渲染(未配置门禁 = 不显示,#42)
const gate = computed(() => gatePassed(runStore.run?.summary))

// 导出 JUnit:仅终态可导出(#42),文本响应在 store 里转 Blob 下载
const exporting = ref(false)
async function exportJunit() {
  exporting.value = true
  try {
    await runStore.exportJunit(runId, api)
  } catch {
    // 错误已由全局拦截器弹出(如 run_not_finished)
  } finally {
    exporting.value = false
  }
}

// 快照明细按步骤展开/收起(前端设计 §6.2:先扫统计,再对失败步骤逐个看现场)
// key 用「用例序号-步骤序号」复合:计划执行里 step_index 跨用例重复,
// 单用例执行 case_index 恒 0,两种模式 key 都稳定,展开态不丢
const expanded = ref<Set<string>>(new Set())

function stepKey(step: StepOut): string {
  return `${step.case_index ?? 0}-${step.step_index}`
}

function toggleStep(step: StepOut): void {
  const key = stepKey(step)
  const next = new Set(expanded.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expanded.value = next
}

// ── 分组渲染(前端设计 §6.3):计划执行按用例分组,单用例平铺 ──────────
// 平铺模式包一层无组头的伪组,行渲染只有一份标记
const displayGroups = computed(() => {
  const groups = runStore.caseGroups
  if (groups !== null) return groups.map((g) => ({ ...g, showHeader: true }))
  return [
    {
      caseIndex: 0,
      caseName: '',
      status: runStore.run?.status ?? '',
      steps: runStore.steps,
      showHeader: false,
    },
  ]
})

function groupAssertion(steps: StepOut[]): string {
  const total = steps.reduce((sum, s) => sum + s.assertion_total, 0)
  const passed = steps.reduce((sum, s) => sum + s.assertion_passed, 0)
  return `断言 ${passed}/${total}`
}
</script>

<template>
  <div v-if="!runStore.run" class="loading" v-loading="true" />
  <div v-else class="page">
    <div class="header">
      <h2>
        测试报告 <small>{{ runStore.run.run_id }}</small>
      </h2>
      <el-tag class="status-tag" :type="statusTag(runStore.run.status)" size="large">
        {{ runStore.run.status }}
      </el-tag>
      <el-tag
        v-if="gate !== undefined"
        class="gate-tag"
        :type="gate ? 'success' : 'danger'"
        size="large"
      >
        {{ gate ? '门禁通过' : '门禁未通过' }}
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
      <el-button
        v-if="runStore.run && TERMINAL_STATUSES.has(runStore.run.status)"
        class="export-btn"
        :loading="exporting"
        @click="exportJunit"
      >
        导出 JUnit
      </el-button>
    </div>

    <div class="stats">
      <div class="stat-card">
        <div class="stat-num">{{ statValue('total') }}</div>
        <div class="stat-label">总步骤</div>
      </div>
      <div class="stat-card good">
        <div class="stat-num">{{ statValue('passed') }}</div>
        <div class="stat-label">通过</div>
      </div>
      <div class="stat-card bad">
        <div class="stat-num">{{ statValue('failed') }}</div>
        <div class="stat-label">失败</div>
      </div>
      <div class="stat-card bad">
        <div class="stat-num">{{ statValue('error') }}</div>
        <div class="stat-label">出错</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ statValue('skipped') }}</div>
        <div class="stat-label">跳过</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ durationText() }}</div>
        <div class="stat-label">总耗时</div>
      </div>
    </div>

    <div class="meta-line">
      <template v-if="runStore.run.plan_id != null">计划 #{{ runStore.run.plan_id }}</template>
      <template v-else>用例 #{{ runStore.run.case_id }}(v{{ runStore.run.case_version }})</template>
      · 环境 #{{ runStore.run.environment_id }} ·
      {{ triggerModeLabel(runStore.run.trigger_mode) }} · {{ runStore.run.created_at }}
    </div>

    <el-alert
      v-if="runStore.stepsTruncated"
      class="truncated"
      type="warning"
      :closable="false"
      title="步骤明细超出展示上限,仅显示部分"
    />

    <div class="step-list">
      <!-- 执行期实时行:进度连续推进 + 正在执行的步骤(事件流驱动) -->
      <div v-if="runStore.progress || runStore.runningStep" class="live-line">
        <el-progress
          v-if="runStore.progress"
          class="live-progress"
          :percentage="Math.round((runStore.progress.completed / runStore.progress.total) * 100)"
          :stroke-width="8"
        />
        <span class="live-text">
          {{
            runStore.runningStep
              ? runStore.runningStep.caseName !== undefined
                ? `正在执行「${runStore.runningStep.caseName}」第 ${runStore.runningStep.stepIndex + 1} 步:${runStore.runningStep.stepName}`
                : `正在执行第 ${runStore.runningStep.stepIndex + 1} 步:${runStore.runningStep.stepName}`
              : `${runStore.progress?.completed ?? 0}/${runStore.progress?.total ?? 0} 已完成`
          }}
        </span>
      </div>
      <template v-for="group in displayGroups" :key="group.caseIndex">
        <!-- 计划执行组头:用例名 + 状态 + 组内断言数;被跳用例无行,组头仍展示 -->
        <div
          v-if="group.showHeader"
          class="case-head"
          :class="{ 'is-bad': group.status === 'failed' || group.status === 'error' }"
        >
          <el-tag :type="statusTag(group.status)" size="small">{{ group.status }}</el-tag>
          <span class="case-name">{{ group.caseName }}</span>
          <span class="assertion-count">{{ groupAssertion(group.steps) }}</span>
        </div>
        <div
          v-for="step in group.steps"
          :key="stepKey(step)"
          class="step-row"
          :class="{ 'is-bad': step.status === 'failed' || step.status === 'error' }"
        >
          <div class="row-head" @click="toggleStep(step)">
            <el-icon class="chevron" :class="{ open: expanded.has(stepKey(step)) }">
              <ArrowRight />
            </el-icon>
            <el-tag :type="statusTag(step.status)" size="small">{{ step.status }}</el-tag>
            <span class="step-name">{{ step.step_index + 1 }}. {{ step.step_name }}</span>
            <span class="assertion-count"
              >断言 {{ step.assertion_passed }}/{{ step.assertion_total }}</span
            >
            <span class="duration">{{ step.duration_ms }}ms</span>
          </div>

          <div v-if="expanded.has(stepKey(step))" class="step-detail">
            <el-alert
              v-if="step.error_message"
              class="row-error"
              type="warning"
              :closable="false"
              :title="step.error_message"
            />
            <StepSnapshot
              :request="step.request ?? null"
              :response="step.response ?? null"
              :assertions="assertionRows(step)"
              :extracted="step.extracted_variables"
            />
          </div>
        </div>
      </template>
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

.stats {
  display: flex;
  gap: 12px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.stat-card {
  min-width: 96px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 14px;
  text-align: center;
}

.stat-num {
  font-size: 20px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.stat-card.good .stat-num {
  color: var(--el-color-success);
}

.stat-card.bad .stat-num {
  color: var(--el-color-danger);
}

.meta-line {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-top: 10px;
}

.truncated {
  margin-top: 12px;
}

.step-list {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 计划报告的用例组头:细字号一行,失败/出错组语义色突出 */
.case-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 6px 4px;
}

.case-head.is-bad {
  box-shadow: inset 3px 0 0 var(--el-color-danger);
  background: var(--el-color-danger-light-9);
  padding-left: 8px;
}

.case-head .case-name {
  font-weight: 600;
  font-size: 13px;
}

.live-line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.live-progress {
  width: 200px;
}

.live-text {
  color: var(--el-color-primary);
  font-size: 13px;
}

.step-row {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}

/* 失败/出错行语义色突出:左侧色条 + 行头底色(调研 §C,MeterSphere 形态) */
.step-row.is-bad {
  border-color: var(--el-color-danger-light-5);
}

.step-row.is-bad .row-head {
  box-shadow: inset 3px 0 0 var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.row-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  background: var(--el-bg-color);
}

.row-head:hover {
  background: var(--el-fill-color-light);
}

.step-row.is-bad .row-head:hover {
  background: var(--el-color-danger-light-8);
}

.chevron {
  color: var(--el-text-color-secondary);
  transition: transform 0.15s;
}

.chevron.open {
  transform: rotate(90deg);
}

.step-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.assertion-count {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  flex-shrink: 0;
}

.duration {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.step-detail {
  border-top: 1px dashed var(--el-border-color-lighter);
  padding: 10px 12px;
}

.row-error {
  margin-bottom: 8px;
}

.none {
  color: var(--el-text-color-secondary);
  padding: 16px 0;
}

.loading {
  min-height: 300px;
}
</style>
