<script setup lang="ts">
import { Delete, Plus } from '@element-plus/icons-vue'

import SecretInput from './SecretInput.vue'

import type { components } from '@/api/schema'

type VariableIn = components['schemas']['VariableIn']

/**
 * 变量编辑表格(手写行布局:可编辑网格用 el-table 在测试环境渲染不出单元格)。
 * 行内编辑直接改对象;增删行发新数组。「保存」由父组件整体 PUT(前端设计 §1)。
 */
const items = defineModel<VariableIn[]>({ required: true })

function addRow() {
  items.value = [...items.value, { name: '', value: '', is_secret: false, description: null }]
}

function removeRow(index: number) {
  items.value = items.value.filter((_, i) => i !== index)
}
</script>

<template>
  <div class="var-table">
    <div v-if="!items.length" class="empty">暂无变量</div>
    <div v-for="(row, index) in items" :key="index" class="row">
      <el-input v-model="row.name" placeholder="变量名" class="name" />
      <SecretInput v-model="row.value" class="value" />
      <div class="secret">
        <span class="secret-label">加密</span>
        <el-switch v-model="row.is_secret" />
      </div>
      <el-input v-model="row.description" placeholder="描述(可选)" class="desc" />
      <el-button :icon="Delete" text type="danger" @click="removeRow(index)" />
    </div>
  </div>
  <div class="toolbar">
    <el-button :icon="Plus" text type="primary" @click="addRow">添加变量</el-button>
    <slot name="actions" />
  </div>
</template>

<style scoped>
.row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}

.name {
  width: 180px;
}

.value {
  flex: 1;
}

.secret {
  display: flex;
  align-items: center;
  gap: 4px;
}

.secret-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.desc {
  width: 180px;
}

.empty {
  color: var(--el-text-color-secondary);
  padding: 8px 0;
}

.toolbar {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
</style>
