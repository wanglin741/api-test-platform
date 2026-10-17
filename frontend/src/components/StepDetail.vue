<script setup lang="ts">
import { Delete, Plus } from '@element-plus/icons-vue'
import { computed } from 'vue'

import KvEditor from './KvEditor.vue'

import { ASSERT_OPERATORS, ASSERT_TYPES, BODY_TYPES, EXTRACT_TYPES, HTTP_METHODS } from '@/types/dsl'

import type { AssertSpec, BodyType, ExtractSpec, StepSpec } from '@/types/dsl'

/**
 * 步骤详情面板(编排视图右侧):请求行 + Params/Headers/Body 子 tab,
 * 提取/断言/重试平铺其下。就地编辑传入的 step 对象(dsl 是事实来源,
 * 视图深度 watch 会同步源码文本);校验交给后端 parse_dsl。
 */
const step = defineModel<StepSpec>({ required: true })

const bodyType = computed<BodyType | 'none'>({
  get: () => step.value.request.body?.type ?? 'none',
  set(type) {
    if (type === 'none') {
      step.value.request.body = null
      return
    }
    if (step.value.request.body?.type !== type) {
      // 类型切换时重置 content:文本型给空串,KV 型给空 dict
      step.value.request.body = {
        type,
        content: type === 'form-data' || type === 'urlencoded' ? {} : '',
      }
    }
  },
})

const isKvBody = computed(
  () =>
    step.value.request.body !== null &&
    step.value.request.body !== undefined &&
    (step.value.request.body.type === 'form-data' || step.value.request.body.type === 'urlencoded'),
)

const isTextBody = computed(
  () =>
    step.value.request.body !== null &&
    step.value.request.body !== undefined &&
    (step.value.request.body.type === 'json' || step.value.request.body.type === 'raw'),
)

const bodyText = computed<string>({
  get() {
    const content = step.value.request.body?.content
    if (typeof content === 'string') return content
    if (content === null || content === undefined) return ''
    return JSON.stringify(content, null, 2)
  },
  set(text) {
    const body = step.value.request.body
    if (body) body.content = text === '' ? null : text
  },
})

const bodyKv = computed<Record<string, unknown>>({
  get() {
    // normalizeDsl/bodyType 保证 KV 型 body 的 content 是 dict
    const content = step.value.request.body?.content
    return typeof content === 'object' && content !== null && !Array.isArray(content)
      ? (content as Record<string, unknown>)
      : {}
  },
  set(value) {
    if (step.value.request.body) step.value.request.body.content = value
  },
})

// ── 提取 / 断言 ──────────────────────────────────────────────────
function addExtract() {
  const row: ExtractSpec = { name: '', type: 'jsonpath', expression: '', required: true }
  step.value.extract = [...(step.value.extract ?? []), row]
}

function removeExtract(index: number) {
  step.value.extract = (step.value.extract ?? []).filter((_, i) => i !== index)
}

function addAssert() {
  const row: AssertSpec = { type: 'status_code', operator: 'eq', expected: '' }
  step.value.assert = [...(step.value.assert ?? []), row]
}

function removeAssert(index: number) {
  step.value.assert = (step.value.assert ?? []).filter((_, i) => i !== index)
}

function expectedText(spec: AssertSpec): string {
  const expected = spec.expected
  return typeof expected === 'string'
    ? expected
    : expected === null || expected === undefined
      ? ''
      : String(expected)
}

function setExpected(spec: AssertSpec, text: string) {
  spec.expected = text
}
</script>

<template>
  <div v-if="step" class="step-detail">
    <div class="request-row">
      <el-input v-model="step.name" placeholder="步骤名称" class="name" />
      <el-switch v-model="step.enabled" />
      <span class="hint">启用</span>
      <el-select
        :model-value="step.request.method"
        class="method"
        @update:model-value="step.request.method = $event"
      >
        <el-option v-for="m in HTTP_METHODS" :key="m" :label="m" :value="m" />
      </el-select>
      <el-input
        v-model="step.request.url"
        placeholder="URL,支持 {{var}} 引用"
        class="url"
      />
    </div>

    <el-tabs class="request-tabs">
      <el-tab-pane label="Params">
        <KvEditor v-model="step.request.params" key-placeholder="参数名" value-placeholder="值" />
      </el-tab-pane>
      <el-tab-pane label="Headers">
        <KvEditor v-model="step.request.headers" key-placeholder="Header 名" value-placeholder="值" />
      </el-tab-pane>
      <el-tab-pane label="Body">
        <div class="body-row">
          <el-select v-model="bodyType" class="body-type">
            <el-option label="无" value="none" />
            <el-option v-for="t in BODY_TYPES" :key="t" :label="t" :value="t" />
          </el-select>
          <el-input
            v-if="isTextBody"
            v-model="bodyText"
            type="textarea"
            :rows="6"
            placeholder="请求体内容(json 可直接写 JSON 文本)"
            class="body-content"
          />
          <KvEditor
            v-else-if="isKvBody"
            v-model="bodyKv"
            key-placeholder="字段名"
            value-placeholder="值"
            class="body-content"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <div class="section">
      <div class="section-title">
        提取
        <el-button :icon="Plus" text type="primary" size="small" @click="addExtract"
          >添加提取</el-button
        >
      </div>
      <div v-for="(row, j) in step.extract ?? []" :key="j" class="spec-row">
        <el-input v-model="row.name" placeholder="变量名" class="w-name" />
        <el-select v-model="row.type" class="w-type">
          <el-option v-for="t in EXTRACT_TYPES" :key="t" :label="t" :value="t" />
        </el-select>
        <el-input
          v-if="row.type !== 'status_code'"
          v-model="row.expression"
          placeholder="表达式"
          class="w-expr"
        />
        <el-switch v-model="row.required" />
        <span class="hint">必需</span>
        <el-button :icon="Delete" text type="danger" @click="removeExtract(j)" />
      </div>
    </div>

    <div class="section">
      <div class="section-title">
        断言
        <el-button :icon="Plus" text type="primary" size="small" @click="addAssert"
          >添加断言</el-button
        >
      </div>
      <div v-for="(row, j) in step.assert ?? []" :key="j" class="spec-row">
        <el-select v-model="row.type" class="w-type">
          <el-option v-for="t in ASSERT_TYPES" :key="t" :label="t" :value="t" />
        </el-select>
        <el-select v-model="row.operator" class="w-op">
          <el-option v-for="op in ASSERT_OPERATORS" :key="op" :label="op" :value="op" />
        </el-select>
        <el-input
          :model-value="expectedText(row)"
          placeholder="期望值"
          class="w-expr"
          @update:model-value="setExpected(row, $event)"
        />
        <el-input
          v-if="row.type === 'jsonpath'"
          v-model="row.expression"
          placeholder="JSONPath"
          class="w-expr"
        />
        <el-button :icon="Delete" text type="danger" @click="removeAssert(j)" />
      </div>
    </div>

    <div class="section">
      <div class="section-title">重试</div>
      <div class="spec-row">
        <span class="hint">次数</span>
        <el-input-number v-model="step.retry.times" :min="0" class="num" />
        <span class="hint">间隔(ms)</span>
        <el-input-number v-model="step.retry.interval_ms" :min="0" :step="500" class="num" />
        <span class="hint">退避</span>
        <el-select v-model="step.retry.backoff" class="w-type">
          <el-option label="fixed" value="fixed" />
          <el-option label="exponential" value="exponential" />
        </el-select>
      </div>
    </div>
  </div>
</template>

<style scoped>
.request-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.name {
  width: 200px;
}

.method {
  width: 110px;
  flex-shrink: 0;
}

.url {
  flex: 1;
}

.body-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.body-type {
  width: 150px;
}

.body-content {
  flex: 1;
}

.section {
  margin-top: 8px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.spec-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.num {
  width: 130px;
}

.w-name {
  width: 130px;
}

.w-type {
  width: 140px;
}

.w-op {
  width: 130px;
}

.w-expr {
  flex: 1;
  min-width: 100px;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
