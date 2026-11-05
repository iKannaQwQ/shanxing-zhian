<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

/** 只有已完成的页面可点击；其余标注待开发，避免死链。 */
const menu = [
  { path: '/admin', label: '运行总览', ready: true },
  { path: '/admin/monitor', label: '实时监控', ready: true },
  { path: '/admin/events', label: '事件管理', ready: false },
  { path: '/admin/history', label: '历史数据', ready: false },
  { path: '/admin/devices', label: '设备管理', ready: false },
]

const active = computed(() => route.path)
</script>

<template>
  <el-container class="admin-shell">
    <el-aside width="208px" class="side">
      <div class="side-brand">
        <span class="dot" />
        <div>
          <div class="title">落石监测</div>
          <div class="sub">管理后台</div>
        </div>
      </div>

      <el-menu :default-active="active" router class="side-menu">
        <el-menu-item
          v-for="item in menu" :key="item.path" :index="item.path" :disabled="!item.ready"
        >
          <span>{{ item.label }}</span>
          <el-tag v-if="!item.ready" size="small" type="info" effect="plain" class="todo">
            待开发
          </el-tag>
        </el-menu-item>
      </el-menu>

      <div class="side-foot">
        <router-link to="/">← 返回用户端</router-link>
      </div>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <span class="crumb">{{ menu.find((m) => m.path === active)?.label ?? '管理后台' }}</span>
        <span class="tag">实验性系统 · 结果仅供研究</span>
      </el-header>
      <el-main class="admin-main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.admin-shell { min-height: 100vh; }

.side {
  background: #111827;
  display: flex;
  flex-direction: column;
  padding: 16px 0;
}

.side-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #f9fafb;
  padding: 0 18px 16px;
}
.side-brand .dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.15);
}
.side-brand .title { font-weight: 600; font-size: 14px; }
.side-brand .sub { font-size: 11px; color: #9ca3af; }

.side-menu {
  border-right: none !important;
  background: transparent !important;
  flex: 1;
}
.side-menu :deep(.el-menu-item) {
  color: #d1d5db;
  display: flex;
  justify-content: space-between;
}
.side-menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background: rgba(34, 197, 94, 0.16);
}
.side-menu :deep(.el-menu-item.is-disabled) { color: #6b7280; opacity: 1; }
.todo { transform: scale(0.85); }

.side-foot {
  padding: 12px 18px 0;
  border-top: 1px solid #1f2937;
  font-size: 12px;
}
.side-foot a { color: #9ca3af; text-decoration: none; }
.side-foot a:hover { color: #e5e7eb; }

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid var(--line);
}
.crumb { font-weight: 600; }
.tag { font-size: 12px; color: var(--muted); }

.admin-main { background: var(--page); }
</style>
