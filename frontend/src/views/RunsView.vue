<script setup lang="ts">
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import TriggerDialog from '@/components/TriggerDialog.vue'
import { useEnvironmentStore } from '@/stores/environment'
import { useProjectStore } from '@/stores/project'
import { useRunStore } from '@/stores/run'

const PAGE_SIZE = 20

const router = useRouter()
const projectStore = useProjectStore()
const { currentId } = storeToRefs(projectStore)
const runStore = useRunStore()
const { runItems, total, offset } = storeToRefs(runStore)

watch(
  currentId,
  async (projectId) => {
    if (projectId !== null) await runStore.listRuns(projectId, api)
  },
  { immediate: true },
)

function openRun(runId: string) {
  router.push(`/runs/${runId}`)
}

async function turnPage(delta: number) {
  if (currentId.value === null) return
  await runStore.listRuns(currentId.value, api, offset.value + delta * PAGE_SIZE)
}

// ── 再次执行:复用执行弹窗,带原 run 的用例 ───────────────────────
const rerunCaseId = ref<number | null>(null)
const triggerVisible = ref(false)
const environmentStore = useEnvironmentStore()

async function openRerun(caseId: number) {
  if (currentId.value === null) return
  // 弹窗默认环境依赖环境列表;打开前确保已加载
  await environmentStore.load(currentId.value, api)
  rerunCaseId.value = caseId
  triggerVisible.value = true
}

function onTriggered(runId: string) {
  router.push(`/runs/${runId}`)
}
</script>

<template>
  <div v-if="currentId === null" class="empty">
    <el-empty description="请先在顶部选择或创建项目" />
  </div>
  <div v-else class="page">
    <div class="head">
      <span>Run</span>
      <span>状态</span>
      <span>用例</span>
      <span>耗时</span>
      <span>触发时间</span>
      <span></span>
    </div>
    <div v-if="!runItems.length" class="none">暂无执行记录</div>
    <div v-for="item in runItems" :key="item.run_id" class="row" @click="openRun(item.run_id)">
      <span class="run-id">{{ item.run_id }}</span>
      <span class="status">{{ item.status }}</span>
      <span>#{{ item.case_id }}(v{{ item.case_version }})</span>
      <span>{{ item.duration_ms === null ? '-' : `${item.duration_ms}ms` }}</span>
      <span>{{ item.created_at }}</span>
      <el-button
        class="rerun-btn"
        size="small"
        text
        type="primary"
        @click.stop="openRerun(item.case_id)"
      >
        再次执行
      </el-button>
    </div>

    <div class="pager">
      <el-button size="small" :disabled="offset === 0" @click="turnPage(-1)">上一页</el-button>
      <span>共 {{ total }} 条</span>
      <el-button
        size="small"
        :disabled="offset + PAGE_SIZE >= total"
        @click="turnPage(1)"
      >
        下一页
      </el-button>
    </div>

    <TriggerDialog
      v-if="rerunCaseId !== null"
      v-model="triggerVisible"
      :case-id="rerunCaseId"
      @triggered="onTriggered"
    />
  </div>
</template>

<style scoped>
.head,
.row {
  display: grid;
  grid-template-columns: 1.4fr 100px 1fr 90px 200px 100px;
  gap: 8px;
  padding: 8px 12px;
  align-items: center;
}

.head {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.row {
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
}

.row:hover {
  background: var(--el-fill-color-light);
}

.run-id {
  font-family: monospace;
  font-size: 12px;
}

.status {
  font-size: 13px;
}

.none {
  color: var(--el-text-color-secondary);
  padding: 16px 12px;
}

.pager {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.empty {
  padding-top: 80px;
}
</style>
