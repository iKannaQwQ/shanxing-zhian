<script setup lang="ts">
import { computed } from 'vue'
import { BUILD_TAG } from '../build'
import { useViewport } from '../composables/useViewport'
import { wsURL } from '../api/client'

const { width } = useViewport()
const apiBase = computed(() => location.origin)
const socket = computed(() => {
  try {
    return wsURL()
  } catch {
    return '-'
  }
})
</script>

<template>
  <div class="build-footer mono">
    {{ BUILD_TAG }} · 视口 {{ width }}px · API {{ apiBase }} · WS {{ socket }}
  </div>
</template>

<style scoped>
.build-footer {
  font-size: 10px;
  color: #9ca3af;
  padding: 10px 16px 14px;
  word-break: break-all;
  line-height: 1.6;
}
</style>
