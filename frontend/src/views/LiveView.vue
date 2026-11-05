<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type AssessmentMessage, type EventRow, type StatusPayload } from '../api/client'
import { rowToMessage } from '../api/mappers'
import { useStatusStream } from '../composables/useStatusStream'

const { connected, last, received, lastAt } = useStatusStream()

const status = ref<StatusPayload | null>(null)
const events = ref<EventRow[]>([])
const fallback = ref<AssessmentMessage | null>(null)
const loadError = ref<string | null>(null)
let timer: number | null = null

const RISK_TEXT: Record<string, string> = {
  SAFE: '未见侵入',
  LOW: '附近有落石',
  MEDIUM: '部分侵入',
  HIGH: '严重阻断',
  UNKNOWN: '无法判定',
}

const RISK_NOTE: Record<string, string> = {
  SAFE: '当前观测质量满足要求，未发现侵入道路的落石。这不代表道路绝对安全——结论受观测范围限制。',
  LOW: '道路附近存在落石，但未发现明显侵入通行区域。',
  MEDIUM: '落石已部分侵入道路，剩余通行宽度受限。',
  HIGH: '落石明显占据通行区域，构成严重阻断。',
  UNKNOWN: '检测质量不足、道路分割失败或关键数据缺失，系统拒绝给出通行结论。',
}

const PASS_TEXT: Record<string, string> = {
  PASSABLE: '可通行',
  RESTRICTED: '受限',
  BLOCKED: '阻断',
  UNKNOWN: '未知',
}

const ALT_TEXT: Record<string, string> = {
  AVAILABLE: '可确认可用',
  UNAVAILABLE: '确认不可用',
  UNKNOWN: '无法确认',
  NOT_EVALUATED: '无需替代',
}

async function refresh() {
  try {
    const [snapshot, eventPage] = await Promise.all([api.status(), api.events(6)])
    status.value = snapshot
    events.value = eventPage.items
    fallback.value = snapshot.latest ? rowToMessage(snapshot.latest as unknown as Record<string, unknown>) : null
    loadError.value = null
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '无法连接后端'
  }
}

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 10_000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})

const current = computed<AssessmentMessage | null>(() => last.value ?? fallback.value)
const risk = computed<string>(() => current.value?.overall_risk ?? 'UNKNOWN')
const riskClass = computed(() => risk.value.toLowerCase())
const riskText = computed(() => RISK_TEXT[risk.value] ?? risk.value)
const riskNote = computed(() => RISK_NOTE[risk.value] ?? '')
const qualityOk = computed(() => current.value?.system_status === 'OK')
const reasons = computed(() => current.value?.reasons ?? [])

const aToB = computed(() => current.value?.passability?.A_TO_B ?? 'UNKNOWN')
const bToA = computed(() => current.value?.passability?.B_TO_A ?? 'UNKNOWN')
const alternative = computed(() => current.value?.alternative ?? 'UNKNOWN')
const coverage = computed(() =>
  current.value?.road_coverage != null ? (current.value.road_coverage * 100).toFixed(1) : '—',
)
const lastTime = computed(() =>
  lastAt.value ? lastAt.value.toLocaleTimeString('zh-CN', { hour12: false }) : '—',
)

function timeText(ns: number): string {
  const date = new Date(ns / 1_000_000)
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

function eventClass(level: string): string {
  if (level === 'CRITICAL' || level === 'ERROR') return 'high'
  if (level === 'WARNING') return 'medium'
  return 'safe'
}
</script>

<template>
  <div class="live-page">
    <div class="row-head">
      <div class="live-badge" :class="{ on: connected }">
        <span class="led" />
        <span v-if="connected">实时连接 · 已收 {{ received }} 条</span>
        <span v-else>未连接，使用 10 秒轮询</span>
      </div>
      <div class="muted mono">最后更新 {{ lastTime }}</div>
    </div>

    <el-alert
      v-if="loadError"
      type="error"
      :closable="false"
      show-icon
      :title="`后端不可达：${loadError}`"
      description="请确认后端已启动（bash scripts/start_backend.sh），且页面通过正确的地址访问。"
      style="margin-bottom: 14px"
    />

    <section class="hero" :class="riskClass">
      <div class="hero-label">总体通行风险</div>
      <div class="hero-value">{{ riskText }}</div>
      <div class="hero-sub mono">
        {{ risk }} · 系统状态 {{ current?.system_status ?? '—' }} ·
        处理 {{ current?.processing_ms ? current.processing_ms.toFixed(0) : '—' }} ms
      </div>
      <div class="hero-note">{{ riskNote }}</div>
      <div v-if="reasons.length" class="hero-codes">
        理由：
        <span v-for="code in reasons" :key="code" class="code-chip">{{ code }}</span>
      </div>
    </section>

    <div class="grid">
      <el-card shadow="never">
        <div class="card-title"><span>A → B 方向</span></div>
        <div class="big-value" :class="aToB.toLowerCase()">{{ PASS_TEXT[aToB] ?? aToB }}</div>
        <div class="muted mono">passability: {{ aToB }}</div>
      </el-card>

      <el-card shadow="never">
        <div class="card-title"><span>B → A 方向</span></div>
        <div class="big-value" :class="bToA.toLowerCase()">{{ PASS_TEXT[bToA] ?? bToA }}</div>
        <div class="muted mono">passability: {{ bToA }}</div>
      </el-card>

      <el-card shadow="never">
        <div class="card-title"><span>替代通行空间</span></div>
        <div class="big-value">{{ ALT_TEXT[alternative] ?? alternative }}</div>
        <div class="muted mono">alternative: {{ alternative }}</div>
      </el-card>

      <el-card shadow="never">
        <div class="card-title">
          <span>观测质量</span>
          <el-tag :type="qualityOk ? 'success' : 'warning'" size="small" effect="plain">
            {{ qualityOk ? '达标' : '降级' }}
          </el-tag>
        </div>
        <div class="big-value">{{ coverage }}%</div>
        <div class="muted">
          道路覆盖率 · 落石 {{ current?.rock_count ?? 0 }} 个 ·
          道路掩码 {{ current?.road_status ?? '—' }}
        </div>
      </el-card>
    </div>

    <el-card shadow="never">
      <div class="card-title"><span>最近事件</span></div>
      <div v-if="events.length === 0" class="muted">暂无事件记录</div>
      <div v-for="event in events" :key="event.id" class="event-row">
        <span class="event-time">{{ timeText(event.ts_ns) }}</span>
        <span class="pill" :class="eventClass(event.level)" style="font-size: 11px">
          {{ event.level }}
        </span>
        <span class="event-code">{{ event.code }}</span>
        <span class="muted">{{ event.message }}</span>
      </div>
    </el-card>

    <div class="disclaimer">
      本页面展示的是<strong>实验性辅助评估结果</strong>，不是真实道路通行指令。
      系统在证据不足时会输出「无法判定」，请勿把「没有告警」等同于「道路安全」。
    </div>
  </div>
</template>

<style scoped>
.row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.hero-codes {
  margin-top: 8px;
  font-size: 12px;
  opacity: 0.9;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.code-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: rgba(255, 255, 255, 0.18);
  padding: 1px 7px;
  border-radius: 5px;
}

.big-value.passable { color: var(--risk-safe); }
.big-value.restricted { color: var(--risk-medium); }
.big-value.blocked { color: var(--risk-high); }
.big-value.unknown { color: var(--risk-unknown); }
</style>
