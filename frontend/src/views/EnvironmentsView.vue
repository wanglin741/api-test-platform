<script setup lang="ts">
import { ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { computed, reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import { api } from '@/api/client'
import VariableTable from '@/components/VariableTable.vue'
import { useEnvironmentStore } from '@/stores/environment'
import { useProjectStore } from '@/stores/project'
import { useVariableStore } from '@/stores/variable'

import type { components } from '@/api/schema'

type EnvironmentCreate = components['schemas']['EnvironmentCreate']
type VariableIn = components['schemas']['VariableIn']

const projectStore = useProjectStore()
const { currentId } = storeToRefs(projectStore)
const environmentStore = useEnvironmentStore()
const { items: environments, selectedId } = storeToRefs(environmentStore)
const variableStore = useVariableStore()

const selectedEnv = computed(
  () => environments.value.find((e) => e.id === selectedId.value) ?? null,
)

watch(
  currentId,
  async (projectId) => {
    if (projectId === null) return
    await environmentStore.load(projectId, api)
    await Promise.all([
      variableStore.loadProjectVars(projectId, api),
      selectedId.value !== null
        ? variableStore.loadEnvVars(selectedId.value, api)
        : Promise.resolve(),
    ])
  },
  { immediate: true },
)

watch(selectedId, (envId) => {
  if (envId !== null) void variableStore.loadEnvVars(envId, api)
})

// ── 环境新建/编辑弹窗 ────────────────────────────────────────────
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const submitting = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ name: '', base_url: '', description: '' })
const rules: FormRules = {
  name: [{ required: true, message: '请输入环境名称', trigger: 'blur' }],
}

function openCreate() {
  editingId.value = null
  form.name = ''
  form.base_url = ''
  form.description = ''
  dialogVisible.value = true
}

function openEdit() {
  const env = selectedEnv.value
  if (!env) return
  editingId.value = env.id
  form.name = env.name
  form.base_url = env.base_url ?? ''
  form.description = env.description ?? ''
  dialogVisible.value = true
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid || currentId.value === null) return
  submitting.value = true
  try {
    if (editingId.value === null) {
      const payload: EnvironmentCreate = {
        name: form.name.trim(),
        base_url: form.base_url.trim() || null,
        description: form.description.trim() || null,
        is_default: false,
        sort_order: 0,
      }
      await environmentStore.create(currentId.value, payload)
    } else {
      await environmentStore.update(editingId.value, {
        name: form.name.trim(),
        base_url: form.base_url.trim() || null,
        description: form.description.trim() || null,
      })
    }
    dialogVisible.value = false
  } catch {
    // 409 重名等错误已由全局拦截器弹出;弹窗保持打开让用户改
  } finally {
    submitting.value = false
  }
}

async function removeEnv() {
  const env = selectedEnv.value
  if (!env || currentId.value === null) return
  try {
    await ElMessageBox.confirm(`确定删除环境「${env.name}」?`, '删除确认', { type: 'warning' })
  } catch {
    return // 用户取消
  }
  await environmentStore.remove(currentId.value, env.id)
}

// ── 变量保存 ─────────────────────────────────────────────────────
const activeTab = ref<'env' | 'project'>('env')

async function saveEnvVars(items: VariableIn[]) {
  if (selectedId.value === null) return
  await variableStore.saveEnvVars(selectedId.value, items)
}

async function saveProjectVars(items: VariableIn[]) {
  if (currentId.value === null) return
  await variableStore.saveProjectVars(currentId.value, items)
}
</script>

<template>
  <div v-if="currentId === null" class="empty">
    <el-empty description="请先在顶部选择或创建项目" />
  </div>
  <div v-else class="page">
    <div class="env-bar">
      <el-select v-model="selectedId" class="env-select" placeholder="选择环境">
        <el-option v-for="env in environments" :key="env.id" :label="env.name" :value="env.id" />
      </el-select>
      <el-button :icon="Plus" @click="openCreate">新建环境</el-button>
      <el-button :disabled="!selectedEnv" @click="openEdit">编辑</el-button>
      <el-button :disabled="!selectedEnv" type="danger" plain @click="removeEnv">删除</el-button>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="环境变量" name="env">
        <VariableTable v-if="selectedEnv" v-model="variableStore.envVars">
          <template #actions>
            <el-button type="primary" @click="saveEnvVars(variableStore.envVars)">保存</el-button>
          </template>
        </VariableTable>
        <el-empty v-else description="先选择一个环境" />
      </el-tab-pane>
      <el-tab-pane label="项目级变量" name="project">
        <VariableTable v-model="variableStore.projectVars">
          <template #actions>
            <el-button type="primary" @click="saveProjectVars(variableStore.projectVars)">
              保存
            </el-button>
          </template>
        </VariableTable>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId === null ? '新建环境' : '编辑环境'"
      width="460px"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.base_url" placeholder="http://dev.example.com" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.env-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.env-select {
  width: 220px;
}

.empty {
  padding-top: 80px;
}
</style>
