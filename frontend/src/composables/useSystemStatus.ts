import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api/client'
import type { StatusPayload } from '../api/types'

/** 轮询 /api/v1/status 作为 WebSocket 的兜底与首屏数据源。 */
export function useSystemStatus(intervalMs = 3000) {
  const status = ref<StatusPayload | null>(null)
  const error = ref<string | null>(null)
  const fetchedAt = ref<Date | null>(null)
  let timer: number | null = null

  async function refresh() {
    try {
      status.value = await api.status()
      fetchedAt.value = new Date()
      error.value = null
    } catch (err) {
      error.value = err instanceof Error ? err.message : '后端不可达'
    }
  }

  onMounted(() => {
    refresh()
    timer = window.setInterval(refresh, intervalMs)
  })
  onUnmounted(() => {
    if (timer !== null) window.clearInterval(timer)
  })

  return { status, error, fetchedAt, refresh }
}
