<script setup lang="ts">
import BuildFooter from '../components/BuildFooter.vue'
</script>

<template>
  <div class="user-shell">
    <header class="user-header">
      <div class="brand">
        <span class="dot" />
        <span>山区公路落石监测</span>
      </div>
      <span class="chip">实验性辅助评估</span>
    </header>

    <main class="user-main">
      <router-view />
    </main>

    <footer class="user-footer">
      <div>
        本页面展示的是实验性辅助评估结果，不是真实道路通行指令。
        系统在证据不足时会输出「无法判定」，请勿把「没有告警」等同于「道路安全」。
      </div>
      <BuildFooter />
    </footer>
  </div>
</template>
