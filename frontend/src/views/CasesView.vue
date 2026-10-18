<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

import '@/assets/list-page.css'

import { api } from '@/api/client'
import FolderTree from '@/components/FolderTree.vue'
import { UNCATEGORIZED_ID } from '@/components/folderTree'
import { useCaseStore } from '@/stores/case'
import { useFolderStore } from '@/stores/folder'
import { useProjectStore } from '@/stores/project'

import type { components } from '@/api/schema'

type CaseCreate = components['schemas']['CaseCreate']
type CaseOut = components['schemas']['CaseOut']

const PAGE_SIZE = 20
const PRIORITIES = ['P0', 'P1', 'P2', 'P3'] as const

const router = useRouter()
const projectStore = useProjectStore()
const { currentId } = storeToRefs(projectStore)
const caseStore = useCaseStore()
const { items, total, page } = storeToRefs(caseStore)
const folderStore = useFolderStore()

// 左树选中:null = 全部,0 = 未分类,N = 目录(含子目录)
const selectedFolder = ref<number | null>(null)

watch(
  currentId,
  async (projectId) => {
    if (projectId === null) return
    selectedFolder.value = null // 换项目即换树,旧选中不属于新项目
    await Promise.all([
      caseStore.load(projectId, api, 1, selectedFolder.value),
      folderStore.load(projectId, api),
    ])
  },
  { immediate: true },
)

function selectFolder(folderId: number | null) {
  selectedFolder.value = folderId
  if (currentId.value !== null) caseStore.load(currentId.value, api, 1, folderId)
}

function openCase(id: number) {
  router.push(`/cases/${id}`)
}

// ── 所属目录列 ───────────────────────────────────────────────────
const folderNames = computed(() => {
  const map = new Map<number, string>()
  for (const folder of folderStore.folders) map.set(folder.id, folder.name)
  return map
})

function folderName(item: CaseOut): string {
  if (item.folder_id === null || item.folder_id === undefined) return '未分类'
  return folderNames.value.get(item.folder_id) ?? '未分类'
}

// ── 新建用例弹窗 ─────────────────────────────────────────────────
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ name: '', priority: 'P1', folder: UNCATEGORIZED_ID as number })
const rules: FormRules = {
  name: [{ required: true, message: '请输入用例名称', trigger: 'blur' }],
}

function openCreateDialog() {
  form.name = ''
  form.priority = 'P1'
  form.folder = selectedFolder.value ?? UNCATEGORIZED_ID // 新建即归位到当前目录
  dialogVisible.value = true
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid || currentId.value === null) return
  submitting.value = true
  try {
    const payload: CaseCreate = {
      name: form.name.trim(),
      priority: form.priority as CaseCreate['priority'],
      folder_id: form.folder === UNCATEGORIZED_ID ? null : form.folder,
      // 后端要求 steps 至少 1 步;占位步骤默认禁用 —— 否则用户没改它就执行,
      // 每次跑批都会真发一个出网请求(httpbin.org)
      dsl: {
        dsl_version: 1,
        steps: [
          {
            id: 'step-1',
            name: '示例步骤',
            type: 'http',
            enabled: false,
            request: { method: 'GET', url: 'https://httpbin.org/get' },
          },
        ],
      },
    }
    const created = await caseStore.create(
      currentId.value,
      payload,
      api,
      selectedFolder.value, // 重载列表沿用当前目录过滤,树与表保持一致
    )
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
  await caseStore.load(currentId.value, api, page.value + delta, selectedFolder.value)
}
</script>

<template>
  <div v-if="currentId === null" class="empty">
    <el-empty description="请先在顶部选择或创建项目" />
  </div>
  <div v-else class="page layout">
    <div class="tree-pane">
      <FolderTree :selected="selectedFolder" @select="selectFolder" />
    </div>

    <div class="table-pane">
      <div class="bar">
        <el-button class="new-btn" type="primary" :icon="Plus" @click="openCreateDialog">
          新建用例
        </el-button>
      </div>

      <div class="head">
        <span>用例名称</span>
        <span>优先级</span>
        <span>版本</span>
        <span>所属目录</span>
        <span>更新时间</span>
      </div>
      <div v-if="!items.length" class="none">暂无用例</div>
      <div v-for="item in items" :key="item.id" class="row" @click="openCase(item.id)">
        <span class="name">{{ item.name }}</span>
        <span class="priority">{{ item.priority ?? '-' }}</span>
        <span class="version">v{{ item.current_version }}</span>
        <span class="folder">{{ folderName(item) }}</span>
        <span class="updated">{{ item.updated_at }}</span>
      </div>

      <div class="pager">
        <el-button class="prev" size="small" :disabled="page <= 1" @click="turnPage(-1)">
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
        <el-form-item label="所属目录">
          <el-select v-model="form.folder">
            <el-option label="未分类" :value="UNCATEGORIZED_ID" />
            <el-option v-for="f in folderStore.folders" :key="f.id" :label="f.name" :value="f.id" />
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
.layout {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.tree-pane {
  width: 240px;
  flex-shrink: 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  padding: 8px;
}

.table-pane {
  flex: 1;
  min-width: 0;
}

.bar {
  margin-bottom: 12px;
}

.head,
.row {
  grid-template-columns: 1fr 80px 80px 160px 220px;
}
</style>
