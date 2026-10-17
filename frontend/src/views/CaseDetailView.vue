<script setup lang="ts">
import { ArrowLeft } from '@element-plus/icons-vue'
import ElMessage from 'element-plus/es/components/message/index'
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '@/api/client'
import DslEditor from '@/components/DslEditor.vue'
import { useCaseStore } from '@/stores/case'

import type { components } from '@/api/schema'

type CaseDetail = components['schemas']['CaseDetail']

interface DslError {
  loc: (string | number)[]
  msg: string
}

const route = useRoute()
const router = useRouter()
const caseStore = useCaseStore()

const detail = ref<CaseDetail | null>(null)
const name = ref('')
const dslText = ref('')
const loading = ref(true)
const saving = ref(false)
const syntaxError = ref('')
const validationErrors = ref<DslError[]>([])

// route.params.id 可能是数组(重复段);用例 id 是数字,取不到就跳回列表
const caseId = Number(Array.isArray(route.params.id) ? route.params.id[0] : route.params.id)

onMounted(async () => {
  try {
    const got = await caseStore.loadOne(caseId, api)
    if (got) {
      detail.value = got
      name.value = got.name
      dslText.value = JSON.stringify(got.dsl, null, 2)
    }
  } finally {
    loading.value = false
  }
})

async function save() {
  validationErrors.value = []
  syntaxError.value = ''
  let dsl: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(dslText.value)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      syntaxError.value = 'DSL 必须是一个 JSON 对象'
      return
    }
    dsl = parsed as Record<string, unknown>
  } catch (error) {
    syntaxError.value = `JSON 语法错误:${error instanceof Error ? error.message : String(error)}`
    return
  }

  saving.value = true
  try {
    const saved = await caseStore.save(caseId, { dsl })
    if (saved) {
      detail.value = saved
      ElMessage.success(`已保存为 v${saved.current_version}`)
    }
  } catch (error) {
    // 业务校验(dsl_invalid)明细在编辑器上方回显;其余错误全局拦截器已提示
    if (error instanceof ApiError && error.code === 'dsl_invalid') {
      const details = error.details as { errors?: DslError[] } | undefined
      validationErrors.value = details?.errors ?? []
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="loading" class="loading" v-loading="true" />
  <div v-else-if="!detail" class="empty">
    <el-empty description="用例不存在">
      <el-button @click="router.push('/cases')">返回列表</el-button>
    </el-empty>
  </div>
  <div v-else class="page">
    <div class="header">
      <el-button :icon="ArrowLeft" text @click="router.push('/cases')" />
      <el-input v-model="name" class="name-input" maxlength="128" />
      <el-tag class="version" type="info">v{{ detail.current_version }}</el-tag>
      <el-button class="save-btn" type="primary" :loading="saving" @click="save">保存</el-button>
    </div>

    <el-alert v-if="syntaxError" class="syntax-error" type="error" :closable="false"
      :title="syntaxError" />
    <el-alert v-if="validationErrors.length" class="validation-errors" type="error"
      :title="`DSL 校验未通过(共 ${validationErrors.length} 处)`">
      <ul>
        <li v-for="(err, i) in validationErrors" :key="i">
          <code>{{ err.loc.join('.') }}</code>
          {{ err.msg }}
        </li>
      </ul>
    </el-alert>

    <DslEditor v-model="dslText" />
  </div>
</template>

<style scoped>
.loading {
  min-height: 300px;
}

.header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.name-input {
  width: 320px;
}

.version {
  flex-shrink: 0;
}

.syntax-error,
.validation-errors {
  margin-bottom: 12px;
}

.validation-errors ul {
  margin: 4px 0 0;
  padding-left: 18px;
}
</style>
