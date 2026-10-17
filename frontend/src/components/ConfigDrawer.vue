<script setup lang="ts">
import KvEditor from './KvEditor.vue'

import type { CaseDsl } from '@/types/dsl'

/**
 * 全局配置抽屉:用例级变量、单步超时、失败即停。
 * 就地编辑 dsl.config(同 StepDetail 的缝),DSL 是事实来源。
 */
const visible = defineModel<boolean>({ required: true })

// 就地编辑 config 字段(dsl 是事实来源);dsl 可能尚未加载,null 时不渲染内容
const dsl = defineModel<CaseDsl | null>('dsl', { required: true })
</script>

<template>
  <el-drawer v-model="visible" title="全局配置" size="420px">
    <div v-if="dsl" class="config-form">
      <div class="row">
        <span class="label">单步超时(ms)</span>
        <el-input-number v-model="dsl.config.timeout_ms" :min="1" :step="1000" />
      </div>
      <div class="row">
        <span class="label">失败即停</span>
        <el-switch v-model="dsl.config.fail_fast" />
      </div>
      <div class="section-label">用例变量(步骤内可用 &#123;&#123;变量名&#125;&#125; 引用)</div>
      <KvEditor v-model="dsl.config.variables" key-placeholder="变量名" value-placeholder="值" />
    </div>
  </el-drawer>
</template>

<style scoped>
.config-form .row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.config-form .label {
  width: 110px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.section-label {
  font-size: 13px;
  font-weight: 600;
  margin: 16px 0 8px;
}
</style>
