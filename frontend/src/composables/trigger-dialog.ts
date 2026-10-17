import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import { useEnvironmentStore } from '@/stores/environment'
import { useProjectStore } from '@/stores/project'

/**
 * 执行弹窗接线(用例详情「执行」与报告列表「再次执行」共用):
 * open 前确保环境列表已加载(弹窗默认环境依赖它),202 后跳新报告。
 */
export function useTriggerDialog() {
  const router = useRouter()
  const projectStore = useProjectStore()
  const environmentStore = useEnvironmentStore()

  const triggerVisible = ref(false)
  const triggerCaseId = ref<number | null>(null)
  const triggerEnvId = ref<number | null>(null)

  async function open(caseId: number, environmentId: number | null = null): Promise<void> {
    if (projectStore.currentId === null) return
    await environmentStore.load(projectStore.currentId, api)
    triggerCaseId.value = caseId
    triggerEnvId.value = environmentId
    triggerVisible.value = true
  }

  function onTriggered(runId: string): void {
    router.push(`/runs/${runId}`)
  }

  return { triggerVisible, triggerCaseId, triggerEnvId, open, onTriggered }
}
