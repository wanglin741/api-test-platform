<script setup lang="ts">
import { Hide, View } from '@element-plus/icons-vue'
import { ref } from 'vue'

/** 加密值输入:默认打码,点眼睛临时明文(前端设计 §1)。 */
const model = defineModel<string>({ default: '' })
const visible = ref(false)
</script>

<template>
  <el-input
    v-model="model"
    :type="visible ? 'text' : 'password'"
    placeholder="值"
    autocomplete="off"
  >
    <template #suffix>
      <el-icon class="eye" @click="visible = !visible">
        <View v-if="visible" />
        <Hide v-else />
      </el-icon>
    </template>
  </el-input>
</template>

<style scoped>
.eye {
  cursor: pointer;
}
</style>
