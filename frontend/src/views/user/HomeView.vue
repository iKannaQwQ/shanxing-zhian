<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { frameImageURL } from '../../api/client'
import { normalizeAlternative, normalizePassability, rowToMessage } from '../../api/mappers'
import { worstPassability } from '../../api/verdict'
import {
  ALTERNATIVE_TEXT, PASSABILITY_TEXT, RISK_TEXT,
  type AlternativeStatus, type Passability, type RiskLevel,
} from '../../api/types'
import { useStatusStream } from '../../composables/useStatusStream'
import { useSystemStatus } from '../../composables/useSystemStatus'

const { connected, last, received, lastLiveAt, error: wsError } = useStatusStream()
const { status, error: statusError, fetchedAt } = useSystemStatus(3000)

const BUILD_TAG = 'web m2.2'

/**
 * 每秒自增的时钟。
 * 过期是**时间函数**，不能用「轮询返回的布尔值」直接显示——
 * 那样最长会晚 3 秒才更新，而且依赖 computed 恰好被重新求值。
 */
const now = ref(Date.now())
let ticker: number | null = null
onMounted(() => { ticker = window.setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => { if (ticker !== null) window.clearInterval(ticker) })

const current = computed(() => {
  if (last.value) return last.value
  const latest = status.value?.latest
  return latest ? rowToMessage(latest) : null
})

const risk = computed<RiskLevel>(() => (current.value?.overall_risk as RiskLevel) ?? 'UNKNOWN')
const riskClass = computed(() => risk.value.toLowerCase())
const riskText = computed(() => RISK_TEXT[risk.value] ?? risk.value)

const passAToB = computed<Passability>(() =>
  normalizePassability(current.value?.passability?.A_TO_B))
const passBToA = computed<Passability>(() =>
  normalizePassability(current.value?.passability?.B_TO_A))

const overallPassability = computed<Passability>(() =>
  worstPassability(passAToB.value, passBToA.value))
const overallPassText = computed(() => PASSABILITY_TEXT[overallPassability.value])

const alternative = computed<AlternativeStatus>(
  () => normalizeAlternative(current.value?.alternative) as AlternativeStatus)

const rockCount = computed(() => current.value?.rock_count ?? 0)
const coverage = computed(() => {
  const value = current.value?.road_coverage
  return value == null ? null : (value * 100).toFixed(1)
})
const reasons = computed(() => current.value?.reasons ?? [])

const rockWarning = computed(() => {
  if (reasons.value.includes('ROCK_DETECTOR_UNAVAILABLE')) {
    return { text: '检测能力不可用', detail: '未部署落石检测器，此结论不构成通行依据', tone: 'unknown' }
  }
  if (rockCount.value > 0) {
    return { text: `发现 ${rockCount.value} 处落石`, detail: '按侵入范围评估，见风险等级', tone: riskClass.value }
  }
  return { text: '未发现落石', detail: '仅在本次观测范围内有效', tone: 'safe' }
})

const STALE_MS = computed(() => status.value?.stale_after_ms ?? 5000)

/**
 * 数据年龄（毫秒）。全部用**客户端相对时间**计算，不比较两端的绝对时钟。
 * - 有实时推送：从收到那一刻算起；
 * - 只有历史快照：服务端给的 age_ms + 拉取后经过的时间。
 */
const ageMs = computed(() => {
  if (lastLiveAt.value !== null) return Math.max(0, now.value - lastLiveAt.value)
  const base = status.value?.age_ms
  if (base == null) return null
  const fetched = fetchedAt.value?.getTime() ?? now.value
  return base + Math.max(0, now.value - fetched)
})

const stale = computed(() => ageMs.value != null && ageMs.value > STALE_MS.value)

const ageText = computed(() => {
  const ms = ageMs.value
  if (ms == null) return '—'
  if (ms < 1000) return '刚刚'
  if (ms < 60_000) return `${(ms / 1000).toFixed(0)} 秒前`
  return `${(ms / 60_000).toFixed(1)} 分钟前`
})

const isDemo = computed(() => status.value?.latest?.source === 'SIMULATED')

const imageURL = computed(() =>
  current.value?.frame_id ? frameImageURL(current.value.frame_id) : null)
const imageBroken = ref(false)
watch(imageURL, () => { imageBroken.value = false })

function timeText(ns: number | undefined | null): string {
  if (!ns) return '—'
  return new Date(ns / 1e6).toLocaleTimeString('zh-CN', { hour12: false })
}

const updatedAt = computed(() => {
  if (last.value?.ts_ns) return timeText(last.value.ts_ns)
  return timeText(status.value?.latest?.ts_assessed_ns)
})

const riskNote: Record<RiskLevel, string> = {
  SAFE: '本次观测未发现侵入道路的落石。这不代表道路绝对安全——结论只覆盖镜头能看到的范围。',
  LOW: '道路附近存在落石，但未发现明显侵入通行区域。',
  MEDIUM: '落石已部分侵入道路，剩余通行宽度受限。',
  HIGH: '落石明显占据通行区域，构成严重阻断。',
  UNKNOWN: '证据不足：可能是没有检测能力、道路分割失败、深度缺失，或跟踪不可靠。系统不放行任何结论。',
}
const note = computed(() => riskNote[risk.value] ?? '')
</script>

<template>
  <div class="home">
    <!-- 连接与时间 -->
    <div class="topline">
      <div class="live-badge" :class="{ on: connected }">
        <span class="led" />
        <span v-if="connected">实时 · 已收 {{ received }} 条</span>
        <span v-else-if="status" class="muted">离线，显示最近一次结果</span>
        <span v-else class="muted">正在连接…</span>
      </div>
      <div class="stamp mono">更新 {{ updatedAt }} · {{ ageText }}</div>
    </div>

    <el-alert
      v-if="statusError"
      type="error" :closable="false" show-icon
      title="后端不可达"
      :description="`${statusError}。请确认服务已启动，然后再刷新。`"
      style="margin-bottom: 12px"
    />

    <el-alert
      v-if="isDemo"
      type="warning" :closable="false" show-icon
      title="DEMO 数据"
      description="当前展示的是注入的模拟检测结果，不是真实推理输出。仅用于界面联调。"
      style="margin-bottom: 12px"
    />

    <el-alert
      v-if="stale"
      type="warning" :closable="false" show-icon
      title="数据已过期"
      :description="`最近一次判定在 ${ageText}，之后没有新数据。下方是历史结果，不代表现在的路况。`"
      style="margin-bottom: 12px"
    />

    <el-alert
      v-if="wsError"
      type="info" :closable="false" show-icon
      :title="`实时通道异常：${wsError}`"
      description="页面会自动重连；期间结果来自轮询。"
      style="margin-bottom: 12px"
    />

    <div class="build mono">{{ BUILD_TAG }}</div>

    <!-- 总体状态 -->
    <section class="hero" :class="riskClass">
      <div class="hero-label">道路总体状态</div>
      <div class="hero-value">{{ overallPassText }}</div>
      <div class="hero-row">
        <span class="hero-pill">{{ risk }}</span>
        <span class="hero-risk">风险：{{ riskText }}</span>
      </div>
      <p class="hero-note">{{ note }}</p>
      <div v-if="reasons.length" class="hero-codes">
        理由：<span v-for="code in reasons" :key="code" class="code">{{ code }}</span>
      </div>
    </section>

    <!-- 方向 -->
    <div class="two">
      <div class="card">
        <div class="card-label">A → B 方向</div>
        <div class="card-value" :class="`tone-${passAToB.toLowerCase()}`">
          {{ PASSABILITY_TEXT[passAToB] }}
        </div>
        <div class="card-sub mono">{{ passAToB }}</div>
      </div>
      <div class="card">
        <div class="card-label">B → A 方向</div>
        <div class="card-value" :class="`tone-${passBToA.toLowerCase()}`">
          {{ PASSABILITY_TEXT[passBToA] }}
        </div>
        <div class="card-sub mono">{{ passBToA }}</div>
      </div>
    </div>

    <!-- 落石 / 受阻 -->
    <div class="two">
      <div class="card">
        <div class="card-label">
          <span>落石预警</span>
          <span class="pill" :class="rockWarning.tone">{{ rockCount }} 处</span>
        </div>
        <div class="card-value" :class="`tone-${rockWarning.tone}`">{{ rockWarning.text }}</div>
        <div class="card-sub">{{ rockWarning.detail }}</div>
      </div>
      <div class="card">
        <div class="card-label">道路受阻情况</div>
        <div class="card-value" :class="`tone-${overallPassability.toLowerCase()}`">
          {{ overallPassText }}
        </div>
        <div class="card-sub">
          道路覆盖率 {{ coverage ?? '—' }}% · 掩码 {{ current?.road_status ?? '—' }}
        </div>
      </div>
    </div>

    <!-- 替代通行 -->
    <div class="card">
      <div class="card-label">替代通行空间评估</div>
      <div class="card-value" :class="alternative === 'AVAILABLE' ? 'tone-safe' : 'tone-unknown'">
        {{ ALTERNATIVE_TEXT[alternative] ?? alternative }}
      </div>
      <div class="card-sub mono">{{ alternative }}</div>
    </div>

    <!-- 最新画面 -->
    <div class="card image-card">
      <div class="card-label">
        <span>最新画面</span>
        <span class="mono muted">{{ updatedAt }}</span>
      </div>
      <div v-if="imageURL && !imageBroken" class="image-wrap" :class="{ dim: stale }">
        <img :src="imageURL" alt="最新采集画面" @error="imageBroken = true" />
        <div v-if="stale" class="image-overlay">数据已过期</div>
      </div>
      <div v-else class="image-empty">
        {{ imageURL ? '影像不可用（可能已超出保留窗口）' : '暂无画面' }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.home { display: flex; flex-direction: column; gap: 12px; }

.topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 12px;
}

.stamp { color: var(--muted); }
.muted { color: var(--muted); }
.build { font-size: 10px; color: #9ca3af; text-align: right; margin-top: -6px; }

/* 总体状态 */
.hero {
  border-radius: 14px;
  padding: 20px 20px 18px;
  color: #fff;
}
.hero.safe { background: linear-gradient(135deg, #15803d, #16a34a); }
.hero.low { background: linear-gradient(135deg, #a16207, #ca8a04); }
.hero.medium { background: linear-gradient(135deg, #c2410c, #ea580c); }
.hero.high { background: linear-gradient(135deg, #991b1b, #dc2626); }
.hero.unknown { background: linear-gradient(135deg, #374151, #6b7280); }

.hero-label { font-size: 12px; opacity: 0.85; letter-spacing: 0.06em; }
.hero-value { font-size: 40px; font-weight: 700; line-height: 1.15; margin: 4px 0 8px; }
.hero-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hero-pill {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.18);
  padding: 2px 9px;
  border-radius: 6px;
}
.hero-risk { font-size: 13px; opacity: 0.95; }
.hero-note { margin: 10px 0 0; font-size: 13px; line-height: 1.65; opacity: 0.92; }
.hero-codes { margin-top: 8px; font-size: 11px; opacity: 0.9; }
.hero-codes .code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: rgba(255, 255, 255, 0.16);
  padding: 1px 6px;
  border-radius: 5px;
  margin-right: 4px;
}

.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

/* 图像 */
.image-card { padding-bottom: 10px; }
.image-wrap { position: relative; border-radius: 10px; overflow: hidden; background: #0b0f14; }
.image-wrap img { display: block; width: 100%; height: auto; }
.image-wrap.dim img { opacity: 0.45; filter: grayscale(0.5); }
.image-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.1em;
  background: rgba(17, 24, 39, 0.35);
}
.image-empty {
  padding: 34px 12px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
  background: #0b0f14;
  border-radius: 10px;
}

@media (max-width: 560px) {
  .two { grid-template-columns: 1fr; }
  .hero-value { font-size: 32px; }
}
</style>
