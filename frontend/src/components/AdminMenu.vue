<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const emit = defineEmits<{ (e: 'navigate'): void }>()
const route = useRoute()

const menu = [
  { path: '/admin', label: '运行总览', ready: true },
  { path: '/admin/monitor', label: '实时监控', ready: true },
  { path: '/admin/events', label: '事件管理', ready: true },
  { path: '/admin/history', label: '历史数据', ready: true },
  { path: '/admin/devices', label: '设备管理', ready: true },
]

const active = computed(() => route.path)
</script>

<template>
  <el-menu :default-active="active" router class="admin-menu" @select="emit('navigate')">
    <el-menu-item v-for="item in menu" :key="item.path" :index="item.path">
      <span>{{ item.label }}</span>
    </el-menu-item>
  </el-menu>
</template>

<style scoped>
.admin-menu { border-right: none !important; background: transparent !important; }
.admin-menu :deep(.el-menu-item) { color: #d1d5db; }
.admin-menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background: rgba(34, 197, 94, 0.16);
}
</style>
