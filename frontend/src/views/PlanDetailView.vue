<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import draggable from 'vuedraggable'

import { api } from '@/api/client'
import SchedulePanel from '@/components/SchedulePanel.vue'
import { usePlanStore } from '@/stores/plan'
import { useProjectStore } from '@/stores/project'
import { useRunStore } from '@/stores/run'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const { currentId } = storeToRefs(projectStore)
const planStore = usePlanStore()
const { current, planItems } = storeToRefs(planStore)

const planId = computed(() => Number(route.params.id))
const loading = ref(true)

// ── 配置编辑(表单草稿,保存时才 PATCH)──────────────────────────
const form = ref<{
  name: string
  description: string
  environment_id: number | null
  execute_mode: 'serial' | 'parallel'
  concurrency: number
  stop_on_fail: boolean
}>({
  name: '',
  description: '',
  environment_id: null,
  execute_mode: 'serial',
  concurrency: 1,
  stop_on_fail: false,
})
const saving = ref(false)

const environments = ref<{ id: number; name: string }[]>([])

async function loadAll() {
  loading.value = true
  try {
    if (currentId.value !== null) {
      const { data } = await api.GET('/api/v1/projects/{project_id}/environments', {
        params: { path: { project_id: currentId.value } },
      })
      environments.value = data ?? []
    }
    const plan = await planStore.loadDetail(planId.value, api)
    if (plan) {
      form.value = {
        name: plan.name,
        description: plan.description ?? '',
        environment_id: plan.environment_id,
        execute_mode: plan.execute_mode === 'parallel' ? 'parallel' : 'serial',
        concurrency: plan.concurrency,
        stop_on_fail: plan.stop_on_fail,
      }
    }
    await loadRecentRuns()
  } finally {
    loading.value = false
  }
}

// ── 最近执行(#33):plan_id 筛选的报告列表,点击进入报告详情 ──
interface RunRow {
  run_id: string
  status: string
  created_at: string
  duration_ms: number | null
  summary: { total?: number; passed?: number } | null
}
const recentRuns = ref<RunRow[]>([])

async function loadRecentRuns() {
  const { data } = await api.GET('/api/v1/projects/{project_id}/runs', {
    params: {
      path: { project_id: currentId.value ?? 0 },
      query: { limit: 5, plan_id: planId.value },
    },
  })
  recentRuns.value = (data?.items ?? []) as RunRow[]
}

function openRun(runId: string) {
  void router.push(`/runs/${runId}`)
}

function passRate(run: RunRow): string {
  // 键名与 engine_tasks 落库一致(total),对齐报告列表页
  if (!run.summary || typeof run.summary.total !== 'number') return '-'
  const passed = typeof run.summary.passed === 'number' ? run.summary.passed : 0
  return `${passed}/${run.summary.total}`
}

onMounted(loadAll)
watch(planId, loadAll)

async function saveConfig() {
  if (!form.value.name.trim()) return
  saving.value = true
  try {
    await planStore.update(planId.value, {
      name: form.value.name.trim(),
      description: form.value.description.trim() || null,
      environment_id: form.value.environment_id,
      execute_mode: form.value.execute_mode,
      concurrency: form.value.concurrency,
      stop_on_fail: form.value.stop_on_fail,
    })
    ElMessage.success('已保存')
  } catch {
    // 错误已由全局拦截器弹出
  } finally {
    saving.value = false
  }
}

async function removePlan() {
  const plan = current.value
  if (!plan || currentId.value === null) return
  try {
    await ElMessageBox.confirm(`确定删除计划「${plan.name}」?`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  await planStore.remove(currentId.value, plan.id)
  void router.push('/plans')
}

// ── 触发动线(前端设计 §6.4):弹窗选环境(默认计划配置),成功跳报告 ──
const runStore = useRunStore()
const triggerDialog = ref(false)
const triggerEnvId = ref<number | null>(null)
const triggering = ref(false)

function openTrigger() {
  triggerEnvId.value = form.value.environment_id
  triggerDialog.value = true
}

async function confirmTrigger() {
  if (currentId.value === null) return
  if (triggerEnvId.value === null) {
    ElMessage.warning('请选择执行环境')
    return
  }
  triggering.value = true
  try {
    const runId = await runStore.triggerPlan(currentId.value, planId.value, triggerEnvId.value)
    if (runId !== null) {
      runStore.rememberEnv(currentId.value, triggerEnvId.value)
      triggerDialog.value = false
      void router.push(`/runs/${runId}`)
    }
  } catch {
    // 错误已由全局拦截器弹出(如清单为空 / 并行未支持)
  } finally {
    triggering.value = false
  }
}

// ── 用例清单编排:左池右列,右列拖拽排序 ─────────────────────────
type CaseRow = { id: number; name: string }
const cases = ref<CaseRow[]>([])

const inPlan = computed(() => new Set(planItems.value.map((i) => i.case_id)))
const pickedIds = computed(() => planItems.value.map((i) => i.case_id))

const casePool = computed(() => cases.value.filter((c) => !inPlan.value.has(c.id)))

function addItem(caseRow: CaseRow) {
  planItems.value = [
    ...planItems.value,
    {
      id: -caseRow.id,
      item_type: 'case',
      case_id: caseRow.id,
      case_name: caseRow.name,
      sort_order: planItems.value.length,
    },
  ]
}

function removeItem(index: number) {
  planItems.value = planItems.value.filter((_, i) => i !== index)
}

const savingItems = ref(false)

async function saveItems() {
  savingItems.value = true
  try {
    await planStore.saveItems(planId.value, pickedIds.value)
    ElMessage.success('清单已保存')
  } catch {
    // 错误已由全局拦截器弹出
  } finally {
    savingItems.value = false
  }
}

onMounted(async () => {
  if (currentId.value === null) return
  const { data } = await api.GET('/api/v1/projects/{project_id}/cases', {
    params: { path: { project_id: currentId.value }, query: { page: 1, page_size: 200 } },
  })
  cases.value = (data?.items ?? []) as CaseRow[]
})
</script>

<template>
  <div v-if="loading" v-loading="true" class="loading" />
  <el-empty v-else-if="!current" description="计划不存在或已删除" />
  <div v-else class="page">
    <div class="bar">
      <el-button @click="router.push('/plans')">返回列表</el-button>
      <h3 class="title">{{ current.name }}</h3>
      <span class="spacer" />
      <el-button type="primary" @click="openTrigger">触发执行</el-button>
      <el-button type="danger" plain @click="removePlan">删除计划</el-button>
    </div>

    <el-dialog v-model="triggerDialog" title="触发执行" width="420px">
      <el-form label-width="90px">
        <el-form-item label="环境" required>
          <el-select v-model="triggerEnvId" placeholder="选择执行环境" class="env-select">
            <el-option
              v-for="env in environments"
              :key="env.id"
              :label="env.name"
              :value="env.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="执行方式">
          <span>
            {{ current.execute_mode === 'parallel' ? '并行' : '串行' }} · 共
            {{ planItems.length }} 条用例
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="triggerDialog = false">取消</el-button>
        <el-button type="primary" :loading="triggering" @click="confirmTrigger">触发执行</el-button>
      </template>
    </el-dialog>

    <el-card header="执行配置" class="config-card">
      <el-form label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="128" class="name-input" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="环境">
          <el-select
            v-model="form.environment_id"
            clearable
            placeholder="执行时选择"
            class="env-select"
          >
            <el-option
              v-for="env in environments"
              :key="env.id"
              :label="env.name"
              :value="env.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="执行模式">
          <el-radio-group v-model="form.execute_mode">
            <el-radio value="serial">串行</el-radio>
            <el-radio value="parallel">并行</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.execute_mode === 'parallel'" label="最大并发">
          <el-input-number v-model="form.concurrency" :min="1" :max="32" />
        </el-form-item>
        <el-form-item label="失败即停">
          <el-switch v-model="form.stop_on_fail" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveConfig">保存配置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <SchedulePanel v-if="currentId !== null" :project-id="currentId" :plan-id="planId" />

    <el-card header="最近执行" class="recent-card">
      <el-empty v-if="recentRuns.length === 0" description="还没有执行记录" :image-size="60" />
      <div v-for="run in recentRuns" :key="run.run_id" class="recent-row" @click="openRun(run.run_id)">
        <span class="run-id">{{ run.run_id }}</span>
        <span class="status">{{ run.status }}</span>
        <span>{{ passRate(run) }}</span>
        <span>{{ run.created_at }}</span>
      </div>
    </el-card>

    <el-card header="用例清单(拖拽调整执行顺序)" class="items-card">
      <div class="items-layout">
        <div class="pool">
          <div class="pool-title">可选用例</div>
          <el-empty v-if="casePool.length === 0" description="没有可添加的用例" :image-size="60" />
          <div v-for="c in casePool" :key="c.id" class="pool-item">
            <span class="case-name">{{ c.name }}</span>
            <el-button size="small" @click="addItem(c)">添加</el-button>
          </div>
        </div>
        <div class="picked">
          <div class="pool-title">执行顺序(共 {{ planItems.length }} 条)</div>
          <el-empty v-if="planItems.length === 0" description="从左侧添加用例" :image-size="60" />
          <draggable
            v-else
            v-model="planItems"
            item-key="case_id"
            handle=".drag-handle"
            class="drag-list"
          >
            <template #item="{ element, index }">
              <div class="picked-item">
                <span class="drag-handle">⠿</span>
                <span class="order">{{ index + 1 }}</span>
                <span class="case-name">{{ element.case_name }}</span>
                <el-button size="small" type="danger" plain @click="removeItem(index)"
                  >移除</el-button
                >
              </div>
            </template>
          </draggable>
          <el-button
            v-if="planItems.length > 0"
            type="primary"
            class="save-items"
            :loading="savingItems"
            @click="saveItems"
          >
            保存清单
          </el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.bar {
  align-items: center;
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}

.title {
  margin: 0;
}

.spacer {
  flex: 1;
}

.config-card {
  margin-bottom: 16px;
}

.name-input {
  max-width: 360px;
}

.env-select {
  width: 240px;
}

.items-layout {
  display: flex;
  gap: 24px;
}

.pool,
.picked {
  flex: 1;
}

.pool-title {
  font-weight: 600;
  margin-bottom: 8px;
}

.pool-item,
.picked-item {
  align-items: center;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  padding: 6px 10px;
}

.picked-item {
  background: var(--el-fill-color-lighter);
  cursor: default;
}

.drag-handle {
  color: var(--el-text-color-secondary);
  cursor: grab;
}

.order {
  color: var(--el-text-color-secondary);
  min-width: 20px;
  text-align: right;
}

.case-name {
  flex: 1;
}

.recent-card {
  margin-bottom: 16px;
}

.recent-row {
  align-items: center;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  gap: 12px;
  margin-bottom: 6px;
  padding: 6px 10px;
}

.recent-row:hover {
  background: var(--el-fill-color-light);
}

.recent-row .run-id {
  flex: 1;
  font-family: monospace;
  font-size: 12px;
}

.save-items {
  margin-top: 8px;
}

.loading {
  min-height: 300px;
}
</style>
