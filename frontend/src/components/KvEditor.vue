<script setup lang="ts">
import { Delete, Plus } from '@element-plus/icons-vue'
import { ref, watch } from 'vue'

import { toKvRows } from '@/types/dsl'

import type { KvRow } from '@/types/dsl'

/**
 * KV 编辑器(headers/params/变量共用):dict ↔ 行数组。
 * 行内编辑直接改 dict(就地改键/值,不重建,保留未触碰值的原始类型);
 * 增删行重建行数组;dict 引用变化(外部载入)时整体重建行。
 */
const model = defineModel<Record<string, unknown>>({ required: true })

const props = defineProps<{
  keyPlaceholder?: string
  valuePlaceholder?: string
}>()

const rows = ref<KvRow[]>(toKvRows(model.value))

watch(model, () => {
  rows.value = toKvRows(model.value)
})

function editKey(row: KvRow, newKey: string) {
  if (row.key && row.key !== newKey) delete model.value[row.key]
  row.key = newKey
  if (newKey) model.value[newKey] = row.value
}

function editValue(row: KvRow, newValue: string) {
  row.value = newValue
  if (row.key) model.value[row.key] = newValue
}

function addRow() {
  rows.value = [...rows.value, { key: '', value: '' }]
}

function removeRow(index: number) {
  const row = rows.value[index]
  if (row?.key) delete model.value[row.key]
  rows.value = rows.value.filter((_, i) => i !== index)
}
</script>

<template>
  <div class="kv-editor">
    <div v-if="!rows.length" class="empty">暂无条目</div>
    <div v-for="(row, index) in rows" :key="index" class="row">
      <el-input
        :model-value="row.key"
        :placeholder="props.keyPlaceholder ?? '名称'"
        class="key"
        @update:model-value="editKey(row, $event)"
      />
      <el-input
        :model-value="row.value"
        :placeholder="props.valuePlaceholder ?? '值'"
        class="value"
        @update:model-value="editValue(row, $event)"
      />
      <el-button :icon="Delete" text type="danger" @click="removeRow(index)" />
    </div>
    <el-button :icon="Plus" text type="primary" class="add" @click="addRow">添加</el-button>
  </div>
</template>

<style scoped>
.row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}

.key {
  width: 200px;
}

.value {
  flex: 1;
}

.empty {
  color: var(--el-text-color-secondary);
  padding: 2px 0 6px;
  font-size: 12px;
}
</style>
