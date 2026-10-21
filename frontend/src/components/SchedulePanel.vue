<script setup lang="ts">
/**
 * 计划详情页的「定时调度」卡片(issue #36,前端设计 §6):
 * 列表(名称/cron/时区/下次执行/启停开关)、创建与编辑弹窗(cron 预设 + 自定义、
 * IANA 时区选择、可选覆盖环境)、删除二次确认。前端只做 5 段粗校验,服务端为权威。
 */
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import { useScheduleStore, type ScheduleOut } from '@/stores/schedule'

const props = defineProps<{ projectId: number; planId: number }>()

const store = useScheduleStore()

// 时区优先系统可用列表,jsdom/老浏览器回退常用列表(spec 决策:前端粗校验、服务端权威)
const COMMON_TIMEZONES = [
  'Asia/Shanghai',
  'UTC',
  'Asia/Tokyo',
  'Asia/Hong_Kong',
  'Singapore',
  'Europe/London',
  'America/New_York',
]
const timezones = computed<string[]>(() => {
  try {
    const all = Intl.supportedValuesOf('timeZone') as string[]
    if (all.length > 0) return all
  } catch {
    // supportedValuesOf 不可用,回退常用列表
  }
  return COMMON_TIMEZONES
})

const CRON_PRESETS = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每天 02:00', value: '0 2 * * *' },
  { label: '工作日 08:30', value: '30 8 * * 1-5' },
]

const environments = ref<{ id: number; name: string }[]>([])

async function loadAll() {
  await Promise.all([
    store.load(props.projectId, props.planId),
    (async () => {
      const { data } = await api.GET('/api/v1/projects/{project_id}/environments', {
        params: { path: { project_id: props.projectId } },
      })
      environments.value = data ?? []
    })(),
  ])
}
onMounted(loadAll)
watch(() => [props.projectId, props.planId], loadAll)

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

// ── 创建 / 编辑弹窗 ──────────────────────────────────────────────
const dialogOpen = ref(false)
const editing = ref<ScheduleOut | null>(null)
const saving = ref(false)
const form = ref({
  name: '',
  cron_expression: '0 2 * * *',
  timezone: 'Asia/Shanghai',
  environment_id: null as number | null,
})

function openCreate() {
  editing.value = null
  form.value = { name: '', cron_expression: '0 2 * * *', timezone: 'Asia/Shanghai', environment_id: null }
  dialogOpen.value = true
}

function openEdit(schedule: ScheduleOut) {
  editing.value = schedule
  form.value = {
    name: schedule.name,
    cron_expression: schedule.cron_expression,
    timezone: schedule.timezone,
    environment_id: schedule.environment_id,
  }
  dialogOpen.value = true
}

/** 5 段粗校验:段数即闸门,合法性由服务端裁决。 */
function coarseCronCheck(expression: string): boolean {
  return expression.trim().split(/\s+/).length === 5
}

async function submitDialog() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写调度名称')
    return
  }
  if (!coarseCronCheck(form.value.cron_expression)) {
    ElMessage.warning('cron 需为 5 段表达式(分 时 日 月 周)')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      // 只发变化的字段:仅改名/改环境时服务端不重算下次执行
      const payload: Record<string, unknown> = {}
      if (form.value.name !== editing.value.name) payload.name = form.value.name.trim()
      if (form.value.cron_expression !== editing.value.cron_expression)
        payload.cron_expression = form.value.cron_expression
      if (form.value.timezone !== editing.value.timezone) payload.timezone = form.value.timezone
      if (form.value.environment_id !== editing.value.environment_id)
        payload.environment_id = form.value.environment_id
      await store.update(editing.value.id, payload)
    } else {
      await store.create(props.projectId, {
        name: form.value.name.trim(),
        plan_id: props.planId,
        cron_expression: form.value.cron_expression,
        timezone: form.value.timezone,
        environment_id: form.value.environment_id,
      })
    }
    ElMessage.success('已保存')
    dialogOpen.value = false
  } catch {
    // 错误已由全局拦截器弹出(cron_invalid / timezone_invalid 等)
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(schedule: ScheduleOut) {
  try {
    await store.setEnabled(schedule.id, !schedule.enabled)
  } catch {
    // 错误已由全局拦截器弹出
  }
}

async function removeSchedule(schedule: ScheduleOut) {
  try {
    await ElMessageBox.confirm(`确定删除调度「${schedule.name}」?删除后不再自动执行。`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await store.remove(props.projectId, schedule.id)
    ElMessage.success('已删除')
  } catch {
    // 错误已由全局拦截器弹出
  }
}
</script>

<template>
  <el-card header="定时调度" class="schedule-card">
    <div class="panel-actions">
      <el-button type="primary" @click="openCreate">新建调度</el-button>
    </div>

    <el-empty v-if="store.items.length === 0" description="暂无定时调度" :image-size="60" />
    <div v-for="s in store.items" :key="s.id" class="schedule-row">
      <span class="name">{{ s.name }}</span>
      <el-tag :type="s.enabled ? 'success' : 'info'" size="small">
        {{ s.enabled ? '已启用' : '已停用' }}
      </el-tag>
      <code class="cron">{{ s.cron_expression }}</code>
      <span class="tz">{{ s.timezone }}</span>
      <span class="next">下次执行:{{ formatTime(s.next_run_at) }}</span>
      <span class="spacer" />
      <el-switch :model-value="s.enabled" @click="toggleEnabled(s)" />
      <el-button size="small" class="edit-btn" @click="openEdit(s)">编辑</el-button>
      <el-button size="small" type="danger" plain class="delete-btn" @click="removeSchedule(s)">
        删除
      </el-button>
    </div>

    <el-dialog
      v-model="dialogOpen"
      :title="editing ? '编辑调度' : '新建调度'"
      width="480px"
    >
      <el-form label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="128" class="schedule-name" />
        </el-form-item>
        <el-form-item label="cron 预设">
          <el-select
            :model-value="form.cron_expression"
            class="preset-select"
            @update:model-value="form.cron_expression = String($event)"
          >
            <el-option v-for="p in CRON_PRESETS" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="cron 表达式" required>
          <el-input v-model="form.cron_expression" class="cron-input" placeholder="分 时 日 月 周" />
        </el-form-item>
        <el-form-item label="时区" required>
          <el-select
            v-model="form.timezone"
            filterable
            class="tz-select"
            @update:model-value="form.timezone = String($event)"
          >
            <el-option v-for="tz in timezones" :key="tz" :label="tz" :value="tz" />
          </el-select>
        </el-form-item>
        <el-form-item label="覆盖环境">
          <el-select
            v-model="form.environment_id"
            clearable
            placeholder="默认用计划配置的环境"
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
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitDialog">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.schedule-card {
  margin-bottom: 16px;
}

.panel-actions {
  margin-bottom: 8px;
}

.schedule-row {
  align-items: center;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  display: flex;
  gap: 10px;
  margin-bottom: 6px;
  padding: 6px 10px;
}

.name {
  font-weight: 600;
}

.cron {
  font-family: monospace;
}

.tz,
.next {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.spacer {
  flex: 1;
}
</style>
