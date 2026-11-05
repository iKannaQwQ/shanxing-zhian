import { onMounted, onUnmounted, ref } from 'vue'
import { wsURL } from '../api/client'
import { rowToMessage } from '../api/mappers'
import type { AssessmentMessage } from '../api/types'

/** 订阅后端 /ws/status，断线自动重连。 */
export function useStatusStream() {
  const connected = ref(false)
  const last = ref<AssessmentMessage | null>(null)
  const received = ref(0)
  const lastAt = ref<Date | null>(null)
  const error = ref<string | null>(null)

  let socket: WebSocket | null = null
  let retryTimer: number | null = null
  let stopped = false

  function connect() {
    if (stopped) return
    try {
      socket = new WebSocket(wsURL())
    } catch {
      scheduleReconnect()
      return
    }
    socket.onopen = () => {
      connected.value = true
      error.value = null
    }
    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string)
        if (data.type === 'assessment') {
          last.value = data as AssessmentMessage
          received.value += 1
          lastAt.value = new Date()
        } else if (data.type === 'snapshot') {
          // 快照是补发的历史值，只用于首屏兜底，不算“实时收到”
          if (data.latest) {
            const mapped = rowToMessage(data.latest)
            if (!last.value) last.value = mapped
          }
        } else if (data.type === 'error') {
          error.value = `${data.code ?? 'ERROR'}${data.frame_id ? ` (${String(data.frame_id).slice(0, 8)})` : ''}`
        }
      } catch {
        /* 忽略无法解析的消息 */
      }
    }
    socket.onclose = () => {
      connected.value = false
      scheduleReconnect()
    }
    socket.onerror = () => {
      error.value = 'WebSocket 连接失败'
      socket?.close()
    }
  }

  function scheduleReconnect() {
    if (stopped || retryTimer !== null) return
    retryTimer = window.setTimeout(() => {
      retryTimer = null
      connect()
    }, 2000)
  }

  onMounted(connect)
  onUnmounted(() => {
    stopped = true
    if (retryTimer !== null) window.clearTimeout(retryTimer)
    socket?.close()
  })

  return { connected, last, received, lastAt, error }
}
