<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AdminMenu from '../components/AdminMenu.vue'
import BuildFooter from '../components/BuildFooter.vue'
import { useViewport } from '../composables/useViewport'

const route = useRoute()
const { isNarrow } = useViewport()
const drawer = ref(false)

const title = computed(() => {
  const map: Record<string, string> = {
    '/admin': '运行总览',
    '/admin/monitor': '实时监控',
    '/admin/events': '事件管理',
    '/admin/history': '历史数据',
    '/admin/devices': '设备管理',
  }
  return map[route.path] ?? '管理后台'
})

watch(() => route.path, () => { drawer.value = false })
</script>

<template>
  <el-container class="admin-shell">
    <!-- 宽屏：固定侧栏 -->
    <el-aside v-if="!isNarrow" width="208px" class="side">
      <div class="side-brand">
        <span class="dot" />
        <div>
          <div class="title">落石监测</div>
          <div class="sub">管理后台</div>
        </div>
      </div>
      <AdminMenu class="grow" />
      <div class="side-foot">
        <router-link to="/">← 返回用户端</router-link>
      </div>
    </el-aside>

    <!-- 窄屏：抽屉 -->
    <el-drawer v-else v-model="drawer" direction="ltr" size="230px" :with-header="false"
               class="menu-drawer">
      <div class="side-brand">
        <span class="dot" />
        <div>
          <div class="title">落石监测</div>
          <div class="sub">管理后台</div>
        </div>
      </div>
      <AdminMenu @navigate="drawer = false" />
      <div class="side-foot">
        <router-link to="/">← 返回用户端</router-link>
      </div>
    </el-drawer>

    <el-container>
      <el-header class="topbar">
        <div class="left">
          <button v-if="isNarrow" class="hamburger" aria-label="菜单" @click="drawer = true">
            ☰
          </button>
          <span class="crumb">{{ title }}</span>
        </div>
        <span class="tag">实验性系统 · 结果仅供研究</span>
      </el-header>
      <el-main class="admin-main">
        <router-view />
        <BuildFooter />
      </el-main>
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
  display: flex; align-items: center; gap: 10px;
  color: #f9fafb; padding: 0 18px 16px;
}
.side-brand .dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: #22c55e; box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.15);
}
.side-brand .title { font-weight: 600; font-size: 14px; }
.side-brand .sub { font-size: 11px; color: #9ca3af; }
.grow { flex: 1; }

.side-foot {
  padding: 12px 18px 0;
  border-top: 1px solid #1f2937;
  font-size: 12px;
}
.side-foot a { color: #9ca3af; text-decoration: none; }
.side-foot a:hover { color: #e5e7eb; }

.topbar {
  display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid var(--line);
  gap: 10px;
}
.topbar .left { display: flex; align-items: center; gap: 10px; }
.crumb { font-weight: 600; }
.tag { font-size: 12px; color: var(--muted); white-space: nowrap; }

.hamburger {
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 6px;
  width: 32px; height: 30px;
  font-size: 15px; line-height: 1;
  cursor: pointer;
}

.admin-main { background: var(--page); padding: 16px; }

@media (max-width: 768px) {
  .topbar { padding: 0 10px; }
  .tag { display: none; }
  .admin-main { padding: 10px; }
}
</style>

<style>
/* 抽屉内的菜单需要深色背景，scoped 覆盖不到挂到 body 的抽屉 */
.menu-drawer .el-drawer__body {
  background: #111827;
  padding: 16px 0;
}
</style>
