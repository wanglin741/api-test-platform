<script setup lang="ts">
/**
 * 计划详情页的「CI 接入」面板(issue #42,前端设计 §6 / §5):
 * Token 列表(名称/scopes/过期/最近使用/撤销)、创建弹窗、一次性明文
 * (只读 + 复制 + 强提示,关闭或卸载即清空,绝不进入 store)、
 * 按当前项目/计划/环境生成 curl 片段(不含 trigger_mode,含幂等键占位)。
 */
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import { useApiTokenStore, type ApiTokenCreated, type ApiTokenOut } from '@/stores/apiToken'

const props = defineProps<{
  projectId: number
  planId: number
  /** 计划配置的默认环境:curl 片段与面板环境选择器的初始值 */
  planEnvironmentId?: number | null
}>()

const store = useApiTokenStore()

const environments = ref<{ id: number; name: string }[]>([])
// curl 片段里显式指定的环境;清空即沿用计划默认环境(不写 environment_id)。
// 可清除 select 清空时给 undefined,因此类型含 undefined,判空用 != null
const environmentId = ref<number | null | undefined>(props.planEnvironmentId ?? null)

async function loadEnvironments() {
  const { data } = await api.GET('/api/v1/projects/{project_id}/environments', {
    params: { path: { project_id: props.projectId } },
  })
  environments.value = data ?? []
}

async function loadAll() {
  await Promise.all([store.load(props.projectId), loadEnvironments()])
}

onMounted(loadAll)
watch(
  () => props.projectId,
  () => {
    store.reset()
    environmentId.value = props.planEnvironmentId ?? null
    void loadAll()
  },
)
watch(
  () => props.planEnvironmentId,
  (value) => {
    environmentId.value = value ?? null
  },
)

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

// ── 创建 + 一次性明文 ────────────────────────────────────────────
const createDialog = ref(false)
const saving = ref(false)
const form = ref<{ name: string; expiresAt: Date | null }>({ name: '', expiresAt: null })
const created = ref<ApiTokenCreated | null>(null)
const plaintextDialog = ref(false)

function openCreate() {
  form.value = { name: '', expiresAt: null }
  createDialog.value = true
}

async function submitCreate() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写 Token 名称')
    return
  }
  saving.value = true
  try {
    const data = await store.create(props.projectId, {
      name: form.value.name.trim(),
      scopes: ['run:trigger', 'report:read'],
      expires_at: form.value.expiresAt ? form.value.expiresAt.toISOString() : null,
    })
    if (data) {
      createDialog.value = false
      created.value = data
      plaintextDialog.value = true
    }
  } catch {
    // 错误已由全局拦截器弹出
  } finally {
    saving.value = false
  }
}

/** 明文不落 store:关闭弹窗/卸载组件即从内存清掉。 */
function clearPlaintext() {
  created.value = null
}

watch(plaintextDialog, (open) => {
  if (!open) clearPlaintext()
})
onBeforeUnmount(clearPlaintext)

async function revoke(token: ApiTokenOut) {
  try {
    await ElMessageBox.confirm(`确定撤销 Token「${token.name}」?撤销后立即失效。`, '撤销确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await store.revoke(props.projectId, token.id)
    ElMessage.success('已撤销')
  } catch {
    // 错误已由全局拦截器弹出
  }
}

// ── 复制(明文 / curl):不可用时明确提示 ───────────────────────────
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败,请手动选择')
  }
}

// ── curl 片段:服务端判定 trigger_mode,片段里不自报 ──────────────────
const curlSnippet = computed(() => {
  const body = [`"plan_id": ${props.planId}`]
  if (environmentId.value != null) body.push(`"environment_id": ${environmentId.value}`)
  const url = `${window.location.origin}/api/v1/projects/${props.projectId}/runs`
  return [
    `curl -X POST "${url}" \\`,
    '  -H "Authorization: Bearer $APITEST_TOKEN" \\',
    '  -H "Content-Type: application/json" \\',
    '  -H "Idempotency-Key: $BUILD_ID" \\',
    `  -d '{${body.join(', ')}}'`,
  ].join('\n')
})
</script>

<template>
  <el-card header="CI 接入" class="ci-card">
    <div class="panel-actions">
      <el-button type="primary" class="create-btn" @click="openCreate">新建 Token</el-button>
    </div>

    <el-empty v-if="store.items.length === 0" description="暂无 API Token" :image-size="60" />
    <div v-for="token in store.items" :key="token.id" class="token-row">
      <span class="token-name">{{ token.name }}</span>
      <span class="scopes">{{ token.scopes.join(' · ') }}</span>
      <span class="meta">过期:{{ formatTime(token.expires_at) }}</span>
      <span class="meta">
        最近使用:{{ token.last_used_at ? formatTime(token.last_used_at) : '从未使用' }}
      </span>
      <span class="spacer" />
      <el-button size="small" type="danger" plain class="revoke-btn" @click="revoke(token)">
        撤销
      </el-button>
    </div>

    <div class="curl-block">
      <div class="curl-head">
        <span class="curl-title">触发命令(curl)</span>
        <el-select
          v-model="environmentId"
          clearable
          placeholder="使用计划默认环境"
          class="curl-env"
        >
          <el-option v-for="env in environments" :key="env.id" :label="env.name" :value="env.id" />
        </el-select>
        <el-button size="small" class="copy-curl" @click="copyText(curlSnippet)"
          >复制 curl</el-button
        >
      </div>
      <pre class="curl-snippet">{{ curlSnippet }}</pre>
      <div class="curl-hint">
        Token 明文只显示一次;请在流水线里设置 `APITEST_TOKEN`,幂等键用构建号/流水线 ID。
      </div>
    </div>

    <el-dialog v-model="createDialog" title="新建 API Token" width="440px" class="create-dialog">
      <el-form label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="128" class="token-name-input" />
        </el-form-item>
        <el-form-item label="过期时间">
          <el-date-picker
            v-model="form.expiresAt"
            type="datetime"
            placeholder="留空表示永不过期"
            class="expires-picker"
          />
        </el-form-item>
        <el-form-item label="权限">
          <span>run:trigger · report:read(固定最小权限)</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="plaintextDialog"
      title="Token 已创建"
      width="560px"
      class="plaintext-dialog"
    >
      <el-alert
        class="plaintext-warning"
        type="warning"
        :closable="false"
        title="明文只显示一次,请立即复制并妥善保存。关闭本弹窗后将无法再次查看。"
      />
      <el-input v-if="created" :model-value="created.token" readonly class="plaintext-input" />
      <template #footer>
        <el-button class="copy-plaintext" type="primary" @click="copyText(created?.token ?? '')">
          复制
        </el-button>
        <el-button @click="plaintextDialog = false">我已保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.ci-card {
  margin-bottom: 16px;
}

.panel-actions {
  margin-bottom: 8px;
}

.token-row {
  align-items: center;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 6px;
  padding: 6px 10px;
}

.token-name {
  font-weight: 600;
}

.scopes {
  font-family: monospace;
  font-size: 12px;
}

.meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.spacer {
  flex: 1;
}

.curl-block {
  margin-top: 12px;
}

.curl-head {
  align-items: center;
  display: flex;
  gap: 10px;
  margin-bottom: 6px;
}

.curl-title {
  font-weight: 600;
}

.curl-env {
  width: 180px;
}

.curl-snippet {
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  margin: 0;
  overflow-x: auto;
  padding: 10px;
  white-space: pre-wrap;
}

.curl-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 6px;
}

.plaintext-input {
  margin-top: 12px;
}
</style>
