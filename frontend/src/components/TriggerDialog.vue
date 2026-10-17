<script setup lang="ts">
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import { useEnvironmentStore } from '@/stores/environment'
import { useProjectStore } from '@/stores/project'
import { useRunStore } from '@/stores/run'

/**
 * 执行弹窗:选环境 → 触发执行。
 * 默认环境优先级:调用方指定的(再次执行带原报告的环境)> 上次执行用的 > is_default > 第一个
 * (前端设计 §6)。环境列表由父页面负责加载;202 后 emit triggered(runId),跳转由父页面做。
 */
const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ caseId: number; environmentId?: number | null }>()
const emit = defineEmits<{ triggered: [runId: string] }>()

const projectStore = useProjectStore()
const { currentId: projectId } = storeToRefs(projectStore)
const environmentStore = useEnvironmentStore()
const { items: environments, selectedId } = storeToRefs(environmentStore)
const runStore = useRunStore()

watch(
  visible,
  (open) => {
    if (!open) return
    const pinned =
      props.environmentId !== null && props.environmentId !== undefined
        ? environments.value.find((env) => env.id === props.environmentId)
        : undefined
    const last = projectId.value === null ? null : runStore.lastEnvId(projectId.value)
    const fallback =
      pinned ??
      (last !== null ? environments.value.find((env) => env.id === last) : undefined) ??
      environments.value.find((env) => env.is_default) ??
      environments.value[0]
    selectedId.value = fallback?.id ?? null
  },
  { immediate: true },
)

const submitting = ref(false)

async function submit() {
  if (projectId.value === null || selectedId.value === null) return
  submitting.value = true
  try {
    const runId = await runStore.trigger(projectId.value, props.caseId, selectedId.value)
    if (runId) {
      runStore.rememberEnv(projectId.value, selectedId.value)
      visible.value = false
      emit('triggered', runId)
    }
  } catch {
    // 环境被删等错误已由全局拦截器弹出;弹窗保持打开
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="执行用例" width="420px">
    <el-form label-width="80px">
      <el-form-item label="环境">
        <el-select v-model="selectedId" placeholder="选择环境" class="env-select">
          <el-option v-for="env in environments" :key="env.id" :label="env.name" :value="env.id" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button class="submit-btn" type="primary" :loading="submitting" @click="submit">
        执行
      </el-button>
    </template>
  </el-dialog>
</template>
