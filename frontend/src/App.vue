<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => (route.meta.title as string) ?? '山区公路落石监测')
</script>

<template>
  <el-container class="app-shell">
    <el-header class="app-header">
      <div class="brand">
        <span class="dot" />
        <span class="name">山区公路落石监测</span>
        <el-tag size="small" type="info" effect="plain">实验性辅助评估</el-tag>
      </div>
      <el-menu :default-active="route.path" mode="horizontal" router class="nav">
        <el-menu-item index="/">实时状态</el-menu-item>
        <el-menu-item index="/admin">管理后台</el-menu-item>
      </el-menu>
    </el-header>

    <el-main class="app-main">
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </el-main>

    <el-footer class="app-footer">
      <span>{{ title }} · 本系统输出为实验性辅助评估结果，不作为真实道路驾驶指令</span>
    </el-footer>
  </el-container>
</template>
