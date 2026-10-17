<script setup lang="ts">
import { useRoute } from 'vue-router'

import ProjectSelector from '@/components/ProjectSelector.vue'

const route = useRoute()
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="brand">接口自动化测试平台</div>
      <el-menu :default-active="route.path" router>
        <el-menu-item index="/cases">用例管理</el-menu-item>
        <el-menu-item index="/environments">环境与变量</el-menu-item>
        <el-menu-item index="/runs">执行报告</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <ProjectSelector />
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}

.aside {
  border-right: 1px solid var(--el-border-color-light);
}

.brand {
  font-weight: 600;
  padding: 16px;
}

.header {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--el-border-color-light);
}
</style>
