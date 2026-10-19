<script setup lang="ts">
import { ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import { usePlanStore, type PlanCreate } from '@/stores/plan'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const projectStore = useProjectStore()
const { currentId } = storeToRefs(projectStore)
const planStore = usePlanStore()
const { items: plans, loading } = storeToRefs(planStore)

watch(
  currentId,
  async (projectId) => {
    if (projectId === null) return
    await planStore.load(projectId, api)
  },
  { immediate: true },
)

const MODE_LABEL: Record<string, string> = {
  serial: '串行',
  parallel: '并行',
}

function openDetail(planId: number) {
  void router.push(`/plans/${planId}`)
}

// ── 新建计划弹窗 ─────────────────────────────────────────────────
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ name: '', description: '' })

function openCreate() {
  form.name = ''
  form.description = ''
  dialogVisible.value = true
}

async function submit() {
  if (!form.name.trim() || currentId.value === null) return
  submitting.value = true
  try {
    const payload: PlanCreate = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      execute_mode: 'serial',
      concurrency: 1,
      stop_on_fail: false,
    }
    const created = await planStore.create(currentId.value, payload)
    dialogVisible.value = false
    if (created) openDetail(created.id) // 新建即进详情挑用例
  } catch {
    // 错误已由全局拦截器弹出;弹窗保持打开让用户改
  } finally {
    submitting.value = false
  }
}

async function removePlan(planId: number, name: string) {
  if (currentId.value === null) return
  try {
    await ElMessageBox.confirm(`确定删除计划「${name}」?`, '删除确认', { type: 'warning' })
  } catch {
    return // 用户取消
  }
  await planStore.remove(currentId.value, planId)
}
</script>

<template>
  <div v-if="currentId === null" class="empty">
    <el-empty description="请先在顶部选择或创建项目" />
  </div>
  <div v-else class="page">
    <div class="bar">
      <h3 class="title">测试计划</h3>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建计划</el-button>
    </div>

    <el-table
      v-loading="loading"
      :data="plans"
      @row-click="(row: { id: number }) => openDetail(row.id)"
    >
      <el-table-column prop="name" label="名称" min-width="200" />
      <el-table-column label="执行模式" width="100">
        <template #default="{ row }">{{
          MODE_LABEL[row.execute_mode] ?? row.execute_mode
        }}</template>
      </el-table-column>
      <el-table-column prop="item_count" label="用例数" width="90" />
      <el-table-column prop="updated_at" label="更新时间" min-width="160" />
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button size="small" @click.stop="openDetail(row.id)">编辑</el-button>
          <el-button size="small" type="danger" plain @click.stop="removePlan(row.id, row.name)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="新建计划" width="460px">
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="128" placeholder="如:每晚回归" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bar {
  align-items: center;
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
}

.title {
  margin: 0;
}

.empty {
  padding-top: 80px;
}
</style>
