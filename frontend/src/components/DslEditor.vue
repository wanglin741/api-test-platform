<script setup lang="ts">
import { basicSetup, EditorView } from 'codemirror'
import { json, jsonParseLinter } from '@codemirror/lang-json'
import { linter } from '@codemirror/lint'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * DSL 编辑器:CodeMirror 6 + JSON 语言(语法错误由 jsonParseLinter 画标线)。
 * 只负责文本编辑;业务校验(结构/步骤字段)由后端 parse_dsl 返回 422 回显。
 */
const model = defineModel<string>({ required: true })

const host = ref<HTMLDivElement>()
let view: EditorView | null = null

onMounted(() => {
  view = new EditorView({
    doc: model.value,
    extensions: [
      basicSetup,
      json(),
      linter(jsonParseLinter()),
      EditorView.lineWrapping,
      EditorView.updateListener.of((update) => {
        if (update.docChanged) model.value = update.state.doc.toString()
      }),
    ],
    parent: host.value,
  })
})

// 外部改值(如载入用例详情)时整篇替换;跳过编辑器自身的回写
watch(model, (value) => {
  if (view && value !== view.state.doc.toString()) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } })
  }
})

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})

function getView(): EditorView | null {
  return view
}
defineExpose({ getView })
</script>

<template>
  <div ref="host" class="dsl-editor" />
</template>

<style scoped>
.dsl-editor {
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
}

.dsl-editor :deep(.cm-editor) {
  min-height: 420px;
  font-size: 13px;
}

.dsl-editor :deep(.cm-gutters) {
  border-right: 1px solid var(--el-border-color-lighter);
}
</style>
