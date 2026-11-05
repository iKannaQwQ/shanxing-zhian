<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <el-container class="admin-shell">
    <el-header class="admin-header">
      <div class="brand">
        <span class="dot" />
        <span>山区公路落石监测 · 管理后台</span>
      </div>
      <el-menu :default-active="route.path" mode="horizontal" router class="nav">
        <el-menu-item index="/">用户端</el-menu-item>
        <el-menu-item index="/admin">后台</el-menu-item>
      </el-menu>
    </el-header>
    <el-main><router-view /></el-main>
  </el-container>
</template>
