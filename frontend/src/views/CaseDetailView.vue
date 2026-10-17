<script setup lang="ts">
import {
  ArrowLeft,
  CopyDocument,
  Delete,
  Plus,
  Rank,
  Setting,
  VideoPlay,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import draggable from 'vuedraggable'

import { api, ApiError } from '@/api/client'
import ConfigDrawer from '@/components/ConfigDrawer.vue'
import DebugPanel from '@/components/DebugPanel.vue'
import DslEditor from '@/components/DslEditor.vue'
import StepDetail from '@/components/StepDetail.vue'
import TriggerDialog from '@/components/TriggerDialog.vue'
import { useCaseStore } from '@/stores/case'
import { useEnvironmentStore } from '@/stores/environment'
import { useProjectStore } from '@/stores/project'
import { useTriggerDialog } from '@/composables/trigger-dialog'
import { newStep, nextStepId, normalizeDsl, parseDslText, toDslText } from '@/types/dsl'

import type { CaseDsl, StepSpec } from '@/types/dsl'
import type { components } from '@/api/schema'

type CaseDetail = components['schemas']['CaseDetail']
type StepDebugOut = components['schemas']['StepDebugOut']

interface DslError {
  loc: (string | number)[]
  msg: string
}

const route = useRoute()
const router = useRouter()
const caseStore = useCaseStore()
const environmentStore = useEnvironmentStore()
const projectStore = useProjectStore()

// ── 执行(与报告列表「再次执行」共用接线) ────────────────────────
const {
  triggerVisible,
  triggerCaseId,
  triggerEnvId,
  open: openTrigger,
  onTriggered,
} = useTriggerDialog()

const detail = ref<CaseDetail | null>(null)
const name = ref('')
const loading = ref(true)
const saving = ref(false)
const validationErrors = ref<DslError[]>([])

// route.params.id 可能是数组(重复段);用例 id 是数字,取不到就跳回列表
const caseId = Number(Array.isArray(route.params.id) ? route.params.id[0] : route.params.id)

// ── DSL 双视图同步:表单改对象、源码改文本,合法即互通 ─────────────
// dsl 是事实来源:表单就地编辑它(深度 watch 刷新文本);源码文本每次改动
// 实时 parse,合法才回写 dsl(格式化的文本原样保留),非法则标语法错误。
const activeTab = ref<'steps' | 'source'>('steps')
const dsl = ref<CaseDsl | null>(null)
const dslText = ref('')
const syntaxError = ref('')
let syncingFromText = false

// ── 脏步骤(调研 §B1/Apifox 小圆点):与上次保存的快照按步骤比对 ──
const savedSteps = ref<Record<string, string>>({})
const savedConfig = ref('')

function snapshotDsl() {
  const map: Record<string, string> = {}
  for (const s of dsl.value?.steps ?? []) map[s.id] = JSON.stringify(s)
  savedSteps.value = map
  savedConfig.value = JSON.stringify(dsl.value?.config)
}

function dirtyOf(step: StepSpec): boolean {
  return JSON.stringify(step) !== savedSteps.value[step.id]
}

const isDirty = computed(() => {
  if (!dsl.value) return false
  if (JSON.stringify(dsl.value.config) !== savedConfig.value) return true
  const steps = dsl.value.steps
  if (Object.keys(savedSteps.value).length !== steps.length) return true
  return steps.some((s) => dirtyOf(s))
})

// ── 编排视图:左步骤列表 + 右详情面板 ────────────────────────────
const selectedStepId = ref<string | null>(null)
const selectedStep = computed<StepSpec | null>(
  () => dsl.value?.steps.find((s) => s.id === selectedStepId.value) ?? null,
)

function selectStep(id: string) {
  if (selectedStepId.value !== id) {
    selectedStepId.value = id
    debugResult.value = null // 换步骤即换上下文,旧调试结果不再适用
  }
}

onMounted(async () => {
  try {
    const got = await caseStore.loadOne(caseId, api)
    if (got) {
      detail.value = got
      name.value = got.name
      dsl.value = normalizeDsl(got.dsl)
      dslText.value = toDslText(dsl.value)
      snapshotDsl()
      selectedStepId.value = dsl.value.steps[0]?.id ?? null
    }
    // 顶部常驻环境选择器(调试目标);与变量页共用环境 store 的选中态
    if (projectStore.currentId !== null) {
      await environmentStore.load(projectStore.currentId, api)
      debugEnvId.value ??= environmentStore.selectedId
    }
  } finally {
    loading.value = false
  }
})

watch(
  dsl,
  () => {
    if (syncingFromText || dsl.value === null) return
    dslText.value = toDslText(dsl.value)
  },
  { deep: true },
)

function onTextChange(text: string) {
  dslText.value = text
  const result = parseDslText(text)
  if (result.ok) {
    syntaxError.value = ''
    syncingFromText = true
    dsl.value = result.dsl
    void nextTick(() => {
      syncingFromText = false
    })
  } else {
    syntaxError.value = result.error
  }
}

function beforeLeaveTab(newTab: string | number): boolean {
  if (newTab === 'steps' && syntaxError.value) {
    ElMessage.warning('DSL 源码有语法错误,修正后再切换到编排视图')
    return false
  }
  return true
}

// ── 步骤操作 ─────────────────────────────────────────────────────
function addStep() {
  if (!dsl.value) return
  const step = newStep(dsl.value.steps)
  dsl.value.steps = [...dsl.value.steps, step]
  selectedStepId.value = step.id
}

function copyStep(index: number) {
  if (!dsl.value) return
  const source = dsl.value.steps[index]
  if (!source) return
  const copy = JSON.parse(JSON.stringify(source)) as StepSpec
  copy.id = nextStepId(dsl.value.steps)
  copy.name = `${source.name} 副本`
  const rest = [...dsl.value.steps]
  rest.splice(index + 1, 0, copy)
  dsl.value.steps = rest
  selectedStepId.value = copy.id
}

function removeStep(index: number) {
  if (!dsl.value) return
  const removed = dsl.value.steps[index]
  dsl.value.steps = dsl.value.steps.filter((_, i) => i !== index)
  if (removed && removed.id === selectedStepId.value) selectedStepId.value = null
}

// ── 单步调试:脏了先保存(端点执行的是已保存版本),再调同步端点 ──
const debugEnvId = ref<number | null>(null)
const debugResult = ref<StepDebugOut | null>(null)
const debugging = ref(false)

async function runDebug(step: StepSpec) {
  if (debugEnvId.value === null) {
    ElMessage.warning('请先在顶部选择调试环境')
    return
  }
  selectStep(step.id)
  debugResult.value = null
  if (isDirty.value) {
    const ok = await save()
    if (!ok) return // 校验失败:错误已在编辑器上方回显
  }
  debugging.value = true
  try {
    debugResult.value = await caseStore.debugStep(caseId, step.id, debugEnvId.value)
  } finally {
    debugging.value = false
  }
}

// ── 保存 ─────────────────────────────────────────────────────────
const configVisible = ref(false)

async function save(): Promise<boolean> {
  validationErrors.value = []
  if (syntaxError.value || dsl.value === null) return false // 语法错误已在编辑器上方标出

  saving.value = true
  try {
    const prevVersion = detail.value?.current_version ?? null
    const saved = await caseStore.save(caseId, { name: name.value.trim(), dsl: dsl.value })
    if (!saved) return false
    detail.value = saved
    dsl.value = normalizeDsl(saved.dsl)
    dslText.value = toDslText(dsl.value)
    snapshotDsl()
    if (!dsl.value.steps.some((s) => s.id === selectedStepId.value)) {
      selectedStepId.value = dsl.value.steps[0]?.id ?? null
    }
    ElMessage.success(
      prevVersion === null || prevVersion === saved.current_version
        ? `已保存为 v${saved.current_version}`
        : `已保存:v${prevVersion} → v${saved.current_version}`,
    )
    return true
  } catch (error) {
    // 业务校验(dsl_invalid)明细在编辑器上方回显;其余错误全局拦截器已提示
    if (error instanceof ApiError && error.code === 'dsl_invalid') {
      const details = error.details as { errors?: DslError[] } | undefined
      validationErrors.value = details?.errors ?? []
    }
    return false
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="loading" class="loading" v-loading="true" />
  <div v-else-if="!detail" class="empty">
    <el-empty description="用例不存在">
      <el-button @click="router.push('/cases')">返回列表</el-button>
    </el-empty>
  </div>
  <div v-else class="page">
    <div class="header">
      <el-button :icon="ArrowLeft" text @click="router.push('/cases')" />
      <el-input v-model="name" class="name-input" maxlength="128" />
      <el-tag class="version" type="info">v{{ detail.current_version }}</el-tag>
      <el-select v-model="debugEnvId" class="env-select" placeholder="调试环境">
        <el-option
          v-for="env in environmentStore.items"
          :key="env.id"
          :label="env.name"
          :value="env.id"
        />
      </el-select>
      <el-button :icon="Setting" text @click="configVisible = true">全局配置</el-button>
      <el-button class="save-btn" type="primary" :loading="saving" @click="save">保存</el-button>
      <el-button class="trigger-btn" type="success" @click="openTrigger(caseId)">执行</el-button>
    </div>

    <TriggerDialog
      v-if="triggerCaseId !== null"
      v-model="triggerVisible"
      :case-id="triggerCaseId"
      :environment-id="triggerEnvId"
      @triggered="onTriggered"
    />

    <ConfigDrawer v-model="configVisible" v-model:dsl="dsl" />

    <el-alert
      v-if="syntaxError"
      class="syntax-error"
      type="error"
      :closable="false"
      :title="syntaxError"
    />
    <el-alert
      v-if="validationErrors.length"
      class="validation-errors"
      type="error"
      :title="`DSL 校验未通过(共 ${validationErrors.length} 处)`"
    >
      <ul>
        <li v-for="(err, i) in validationErrors" :key="i">
          <code>{{ err.loc.join('.') }}</code>
          {{ err.msg }}
        </li>
      </ul>
    </el-alert>

    <el-tabs v-model="activeTab" class="tabs" :before-leave="beforeLeaveTab">
      <el-tab-pane label="编排" name="steps">
        <div v-if="dsl" class="workbench">
          <div class="step-list">
            <draggable
              v-model="dsl.steps"
              item-key="id"
              handle=".drag-handle"
              class="list-body"
              :animation="150"
            >
              <template #item="{ element, index }">
                <div
                  class="step-item"
                  :class="{
                    active: element.id === selectedStepId,
                    disabled: element.enabled === false,
                  }"
                  @click="selectStep(element.id)"
                >
                  <el-icon class="drag-handle"><Rank /></el-icon>
                  <span class="no">#{{ index + 1 }}</span>
                  <span class="item-name">{{ element.name || '未命名步骤' }}</span>
                  <span v-if="dirtyOf(element)" class="dirty-dot" title="未保存" />
                  <span class="ops" @click.stop>
                    <el-tooltip content="调试" placement="top">
                      <el-button
                        :icon="VideoPlay"
                        text
                        size="small"
                        class="op-debug"
                        @click="runDebug(element)"
                      />
                    </el-tooltip>
                    <el-tooltip content="复制" placement="top">
                      <el-button
                        :icon="CopyDocument"
                        text
                        size="small"
                        @click="copyStep(index)"
                      />
                    </el-tooltip>
                    <el-tooltip content="删除" placement="top">
                      <el-button
                        :icon="Delete"
                        text
                        size="small"
                        type="danger"
                        @click="removeStep(index)"
                      />
                    </el-tooltip>
                  </span>
                </div>
              </template>
            </draggable>
            <el-button :icon="Plus" text type="primary" class="add-step" @click="addStep"
              >添加步骤</el-button
            >
          </div>
          <div class="detail-pane">
            <StepDetail
              v-if="selectedStep"
              :model-value="selectedStep"
              :key="selectedStep.id"
            />
            <el-empty v-else description="在左侧选择或添加步骤" :image-size="80" />
            <DebugPanel :result="debugResult" :loading="debugging" />
          </div>
        </div>
      </el-tab-pane>
      <el-tab-pane label="源码" name="source">
        <DslEditor :model-value="dslText" @update:model-value="onTextChange" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.loading {
  min-height: 300px;
}

.header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.name-input {
  width: 280px;
}

.version {
  flex-shrink: 0;
}

.env-select {
  width: 150px;
}

.syntax-error,
.validation-errors {
  margin-bottom: 12px;
}

.validation-errors ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.tabs {
  margin-top: 4px;
}

.workbench {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.step-list {
  width: 300px;
  flex-shrink: 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px;
}

.list-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 40px;
}

.step-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  cursor: pointer;
  background: var(--el-bg-color);
}

.step-item.active {
  border-color: var(--el-color-primary);
}

.step-item.disabled .item-name {
  color: var(--el-text-color-placeholder);
  text-decoration: line-through;
}

.drag-handle {
  cursor: grab;
  color: var(--el-text-color-placeholder);
  flex-shrink: 0;
}

.no {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.item-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.dirty-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-warning);
  flex-shrink: 0;
}

.ops {
  display: none;
  flex-shrink: 0;
}

.step-item:hover .ops {
  display: inline-flex;
}

.add-step {
  margin-top: 8px;
  width: 100%;
}

.detail-pane {
  flex: 1;
  min-width: 0;
}
</style>
