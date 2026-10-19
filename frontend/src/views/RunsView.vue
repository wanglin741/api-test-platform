<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

import '@/assets/list-page.css'

import { api } from '@/api/client'
import { useTriggerDialog } from '@/composables/trigger-dialog'
import TriggerDialog from '@/components/TriggerDialog.vue'
import { useEnvironmentStore } from '@/stores/environment'
import { usePlanStore } from '@/stores/plan'
import { useProjectStore } from '@/stores/project'
import { useRunStore } from '@/stores/run'

const router = useRouter()
const projectStore = useProjectStore()
const { currentId } = storeToRefs(projectStore)
const runStore = useRunStore()
const { runItems, nextCursor, listLoading } = storeToRefs(runStore)
const environmentStore = useEnvironmentStore()
const { items: environments } = storeToRefs(environmentStore)
const planStore = usePlanStore()
const { items: plans } = storeToRefs(planStore)

// ── 按计划筛选(#33):与计划详情「最近执行」共用 plan_id 查询参数 ──
const filterPlanId = ref<number | null>(null)

watch(
  currentId,
  async (projectId) => {
    if (projectId === null) return
    filterPlanId.value = null // 切项目后旧计划的筛选失去意义,一并复位
    await Promise.all([
      runStore.listRuns(projectId, api),
      environmentStore.load(projectId, api),
      planStore.load(projectId, api),
    ])
  },
  { immediate: true },
)

watch(filterPlanId, (planId) => {
  if (currentId.value === null) return
  void runStore.listRuns(currentId.value, api, null, 20, planId)
})

function envName(environmentId: number): string {
  return environments.value.find((env) => env.id === environmentId)?.name ?? `#${environmentId}`
}

function passRate(item: (typeof runItems.value)[number]): string {
  const summary = item.summary
  // 键名与 engine_tasks 落库一致(total,非 total_steps),对齐报告详情页统计卡
  if (!summary || typeof summary.total !== 'number') return '-'
  const passed = typeof summary.passed === 'number' ? summary.passed : 0
  return `${passed}/${summary.total}`
}

function openRun(runId: string) {
  router.push(`/runs/${runId}`)
}

async function nextPage() {
  if (currentId.value === null || nextCursor.value === null) return
  await runStore.listRuns(currentId.value, api, nextCursor.value)
}

// ── 再次执行:复用执行弹窗,带原 run 的用例与环境 ─────────────────
const rerunTarget = ref<{ caseId: number; environmentId: number } | null>(null)
const triggerVisible = ref(false)

async function openRerun(caseId: number, environmentId: number) {
  rerunTarget.value = { caseId, environmentId }
  triggerVisible.value = true
}

const { onTriggered } = useTriggerDialog()
const rerunCaseId = computed(() => rerunTarget.value?.caseId ?? null)
const rerunEnvId = computed(() => rerunTarget.value?.environmentId ?? null)
</script>

<template>
  <div v-if="currentId === null" class="empty">
    <el-empty description="请先在顶部选择或创建项目" />
  </div>
  <div v-else class="page" v-loading="listLoading">
    <div class="filter-bar">
      <el-select
        v-model="filterPlanId"
        clearable
        placeholder="按计划筛选"
        class="plan-filter"
      >
        <el-option v-for="plan in plans" :key="plan.id" :label="plan.name" :value="plan.id" />
      </el-select>
    </div>
    <div class="head">
      <span>Run</span>
      <span>状态</span>
      <span>环境</span>
      <span>用例</span>
      <span>通过率</span>
      <span>耗时</span>
      <span>触发时间</span>
      <span></span>
    </div>
    <template v-if="!listLoading">
      <div v-if="!runItems.length" class="none">暂无执行记录</div>
      <div v-for="item in runItems" :key="item.run_id" class="row" @click="openRun(item.run_id)">
        <span class="run-id">{{ item.run_id }}</span>
        <span class="status">{{ item.status }}</span>
        <span>{{ envName(item.environment_id) }}</span>
        <!-- 计划执行无单用例归属:显示计划号,重跑入口随 #31 -->
        <span v-if="item.plan_id != null">计划 #{{ item.plan_id }}</span>
        <span v-else>#{{ item.case_id }}(v{{ item.case_version }})</span>
        <span>{{ passRate(item) }}</span>
        <span>{{ item.duration_ms === null ? '-' : `${item.duration_ms}ms` }}</span>
        <span>{{ item.created_at }}</span>
        <el-button
          v-if="item.case_id != null"
          class="rerun-btn"
          size="small"
          text
          type="primary"
          @click.stop="openRerun(item.case_id, item.environment_id)"
        >
          再次执行
        </el-button>
      </div>
    </template>

    <div class="pager">
      <span>{{ runItems.length }} 条本页</span>
      <!-- 游标分页只支持下一页(API设计 §分页,有意不支持跳页) -->
      <el-button class="next" size="small" :disabled="nextCursor === null" @click="nextPage">
        下一页
      </el-button>
    </div>

    <TriggerDialog
      v-if="rerunCaseId !== null"
      v-model="triggerVisible"
      :case-id="rerunCaseId"
      :environment-id="rerunEnvId"
      @triggered="onTriggered"
    />
  </div>
</template>

<style scoped>
.filter-bar {
  margin-bottom: 12px;
}

.plan-filter {
  width: 220px;
}

.head,
.row {
  grid-template-columns: 1.2fr 90px 110px 1fr 80px 80px 180px 100px;
}

.run-id {
  font-family: monospace;
  font-size: 12px;
}

.status {
  font-size: 13px;
}
</style>
