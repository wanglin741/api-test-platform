<script setup lang="ts">
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { useProjectStore } from '@/stores/project'

const store = useProjectStore()
const { items, currentId } = storeToRefs(store)

const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ name: '', description: '' })
const rules: FormRules = {
  name: [{ required: true, message: '请输入项目名称', trigger: 'blur' }],
}

onMounted(() => {
  if (!items.value.length) void store.load()
})

function openDialog() {
  form.name = ''
  form.description = ''
  dialogVisible.value = true
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await store.create({
      name: form.name.trim(),
      description: form.description.trim() || undefined,
    })
    ElMessage.success('项目已创建')
    dialogVisible.value = false
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-select
    :model-value="currentId"
    class="selector"
    placeholder="选择项目"
    @update:model-value="store.select"
  >
    <el-option v-for="p in items" :key="p.id" :label="p.name" :value="p.id" />
    <template #footer>
      <el-button text type="primary" @click="openDialog">+ 新建项目</el-button>
    </template>
  </el-select>

  <el-dialog v-model="dialogVisible" title="新建项目" width="420px">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
      <el-form-item label="名称" prop="name">
        <el-input v-model="form.name" placeholder="项目名称" maxlength="64" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="form.description" type="textarea" :rows="2" placeholder="可选" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.selector {
  width: 220px;
}
</style>
