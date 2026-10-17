<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import { useCaseStore } from '@/stores/case'
import { useProjectStore } from '@/stores/project'

import type { components } from '@/api/schema'

type CaseCreate = components['schemas']['CaseCreate']

const PAGE_SIZE = 20
const PRIORITIES = ['P0', 'P1', 'P2', 'P3'] as const

const router = useRouter()
const projectStore = useProjectStore()
const { currentId } = storeToRefs(projectStore)
const caseStore = useCaseStore()
const { items, total, page } = storeToRefs(caseStore)

watch(
  currentId,
  async (projectId) => {
    if (projectId !== null) await caseStore.load(projectId, api)
  },
  { immediate: true },
)

function openCase(id: number) {
  router.push(`/cases/${id}`)
}

// ── 新建用例弹窗 ─────────────────────────────────────────────────
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ name: '', priority: 'P1' })
const rules: FormRules = {
  name: [{ required: true, message: '请输入用例名称', trigger: 'blur' }],
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid || currentId.value === null) return
  submitting.value = true
  try {
    const payload: CaseCreate = {
      name: form.name.trim(),
      priority: form.priority as CaseCreate['priority'],
      dsl: { dsl_version: 1, steps: [] },
    }
    const created = await caseStore.create(currentId.value, payload)
    dialogVisible.value = false
    if (created) router.push(`/cases/${created.id}`)
  } catch {
    // 422/重名等错误已由全局拦截器弹出;弹窗保持打开
  } finally {
    submitting.value = false
  }
}

// ── 分页 ─────────────────────────────────────────────────────────
async function turnPage(delta: number) {
  if (currentId.value === null) return
  await caseStore.load(currentId.value, api, page.value + delta)
}
</script>

<template>
  <div v-if="currentId === null" class="empty">
    <el-empty description="请先在顶部选择或创建项目" />
  </div>
  <div v-else class="page">
    <div class="bar">
      <el-button class="new-btn" type="primary" :icon="Plus" @click="dialogVisible = true">
        新建用例
      </el-button>
    </div>

    <div class="head">
      <span>用例名称</span>
      <span>优先级</span>
      <span>版本</span>
      <span>更新时间</span>
    </div>
    <div v-if="!items.length" class="none">暂无用例</div>
    <div v-for="item in items" :key="item.id" class="row" @click="openCase(item.id)">
      <span class="name">{{ item.name }}</span>
      <span class="priority">{{ item.priority ?? '-' }}</span>
      <span class="version">v{{ item.current_version }}</span>
      <span class="updated">{{ item.updated_at }}</span>
    </div>

    <div class="pager">
      <el-button
        class="prev"
        size="small"
        :disabled="page <= 1"
        @click="turnPage(-1)"
      >
        上一页
      </el-button>
      <span>第 {{ page }} 页 / 共 {{ total }} 条</span>
      <el-button
        class="next"
        size="small"
        :disabled="page * PAGE_SIZE >= total"
        @click="turnPage(1)"
      >
        下一页
      </el-button>
    </div>

    <el-dialog v-model="dialogVisible" title="新建用例" width="420px" class="case-dialog">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority">
            <el-option v-for="p in PRIORITIES" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button class="submit-btn" type="primary" :loading="submitting" @click="submit">
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bar {
  margin-bottom: 12px;
}

.head,
.row {
  display: grid;
  grid-template-columns: 1fr 80px 80px 220px;
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
