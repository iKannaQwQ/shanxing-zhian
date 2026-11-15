<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { frameImageURL } from '../../api/client'
import { minRemainingWidth, normalizeAlternative, normalizePassability, rowToMessage } from '../../api/mappers'
import { worstPassability } from '../../api/verdict'
import {
  ALTERNATIVE_TEXT, PASSABILITY_TEXT, RISK_TEXT,
  type AlternativeStatus, type Passability, type RiskLevel,
} from '../../api/types'
import { useStatusStream } from '../../composables/useStatusStream'
import { useSystemStatus } from '../../composables/useSystemStatus'

const { connected, last, received, lastLiveAt, error: wsError } = useStatusStream()
const { status, error: statusError, fetchedAt } = useSystemStatus(3000)

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
const reasons = computed(() => current.value?.reasons ?? [])

/** 双向各自剩余的通行宽度（米）。几何模式下这是最关键的量化指标。 */
const directions = computed(() => current.value?.directions ?? [])
const widthOf = (dir: 'A_TO_B' | 'B_TO_A'): number | null => {
  const hit = directions.value.find((d) => d.direction === dir)
  return typeof hit?.remaining_width_m === 'number' ? hit.remaining_width_m : null
}
const widthAToB = computed(() => widthOf('A_TO_B'))
const widthBToA = computed(() => widthOf('B_TO_A'))
const minWidth = computed(() => minRemainingWidth(directions.value))
const minWidthText = computed(() =>
  minWidth.value === null ? '—' : `${(minWidth.value * 100).toFixed(1)} cm`)

/** 障碍与可通行区的证据来源：不能让人误以为是深度学习检测器给的。 */
const evidenceSource = computed(() => {
  const codes = reasons.value
  if (codes.includes('OBSTACLE_SOURCE_GEOMETRIC')) return '深度几何'
  if (codes.includes('ROCK_DETECTOR_UNAVAILABLE')) return '不可用'
  return rockCount.value > 0 ? '检测器' : '—'
})
const corridorSource = computed(() =>
  reasons.value.includes('CORRIDOR_FROM_PLANE') ? '平整地面' : '道路分割')
const coverage = computed(() => {
  const value = current.value?.road_coverage
  return value == null ? null : (value * 100).toFixed(1)
})

const rockWarning = computed(() => {
  if (reasons.value.includes('ROCK_DETECTOR_UNAVAILABLE')) {
    return { text: '检测能力不可用', detail: '未部署落石检测器，此结论不构成通行依据', tone: 'unknown' }
  }
  if (rockCount.value > 0) {
    return { text: `发现 ${rockCount.value} 处障碍`, detail: '按侵入范围评估，见风险等级', tone: riskClass.value }
  }
  if (reasons.value.includes('OBSTACLE_SOURCE_GEOMETRIC')) {
    return { text: '未检出凸起障碍', detail: '仅能发现高出路面约 8 mm 以上的物体', tone: 'safe' }
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
  <div class="dash">
    <!-- 顶栏 -->
    <header class="dash-head">
      <div class="dash-title">
        <span class="live-badge" :class="{ on: connected }"><span class="led" /></span>
        <strong>山区公路落石预警</strong>
        <span class="dash-sub">实时监控</span>
      </div>
      <div class="dash-meta">
        <span v-if="connected" class="dash-live">实时 · 已收 {{ received }} 条</span>
        <span v-else-if="status" class="muted">离线，显示最近一次结果</span>
        <span v-else class="muted">正在连接…</span>
        <span class="stamp mono">{{ updatedAt }} · {{ ageText }}</span>
      </div>
    </header>

    <!-- 告警条 -->
    <el-alert
      v-if="statusError"
      type="error" :closable="false" show-icon
      title="后端不可达"
      :description="`${statusError}。请确认服务已启动，然后再刷新。`"
      style="margin-bottom: 8px"
    />
    <el-alert
      v-if="isDemo"
      type="warning" :closable="false" show-icon
      title="DEMO 数据"
      description="当前展示的是注入的模拟检测结果，不是真实推理输出。仅用于界面联调。"
      style="margin-bottom: 8px"
    />
    <el-alert
      v-if="stale"
      type="warning" :closable="false" show-icon
      title="数据已过期"
      :description="`最近一次判定在 ${ageText}，之后没有新数据。画面与判定均为历史结果，不代表现在的路况。`"
      style="margin-bottom: 8px"
    />
    <el-alert
      v-if="wsError"
      type="info" :closable="false" show-icon
      :title="`实时通道异常：${wsError}`"
      description="页面会自动重连；期间结果来自轮询。"
      style="margin-bottom: 8px"
    />

    <div class="dash-body">
      <!-- 左：实时画面 -->
      <section class="viewer">
        <div class="viewer-frame" :class="{ 'is-stale': stale }">
          <img
            v-if="imageURL && !imageBroken"
            :src="imageURL" alt="最新采集画面"
            @error="imageBroken = true"
          />
          <div v-else class="image-empty">
            {{ imageURL ? '影像不可用（可能已超出保留窗口）' : '暂无画面' }}
          </div>

          <div class="hud hud-tl">
            <span class="hud-chip" :class="riskClass">{{ risk }}</span>
            <span class="hud-text">{{ riskText }}</span>
          </div>
          <div class="hud hud-tr mono">{{ updatedAt }}</div>
          <div class="hud hud-bl">
            <span>最窄剩余 <strong>{{ minWidthText }}</strong></span>
            <span class="hud-sep">·</span>
            <span><strong>{{ rockCount }}</strong> 处障碍</span>
          </div>

          <div v-if="stale" class="image-overlay">数据已过期</div>
        </div>
        <div class="viewer-foot">
          <span class="mono">证据 {{ evidenceSource }} · 可通行区 {{ corridorSource }}</span>
          <span class="mono">道路覆盖 {{ coverage ?? '—' }}% · 掩码 {{ current?.road_status ?? '—' }}</span>
        </div>
      </section>

      <!-- 右：判定数据 -->
      <aside class="panel">
        <section class="hero" :class="riskClass">
          <div class="hero-label">道路总体状态</div>
          <div class="hero-value">{{ overallPassText }}</div>
          <div class="hero-row">
            <span class="hero-pill">{{ risk }}</span>
            <span class="hero-risk">风险：{{ riskText }}</span>
            <span v-if="minWidth !== null" class="hero-risk">最窄剩余：{{ minWidthText }}</span>
          </div>
          <p class="hero-note">{{ note }}</p>
          <div v-if="reasons.length" class="hero-codes">
            理由：<span v-for="code in reasons" :key="code" class="code">{{ code }}</span>
          </div>
        </section>

        <div class="two">
          <div class="card">
            <div class="card-label">A → B 方向</div>
            <div class="card-value" :class="`tone-${passAToB.toLowerCase()}`">
              {{ PASSABILITY_TEXT[passAToB] }}
            </div>
            <div class="card-sub mono">
              {{ passAToB }}
              <span v-if="widthAToB !== null"> · 剩余 {{ (widthAToB * 100).toFixed(1) }} cm</span>
            </div>
          </div>
          <div class="card">
            <div class="card-label">B → A 方向</div>
            <div class="card-value" :class="`tone-${passBToA.toLowerCase()}`">
              {{ PASSABILITY_TEXT[passBToA] }}
            </div>
            <div class="card-sub mono">
              {{ passBToA }}
              <span v-if="widthBToA !== null"> · 剩余 {{ (widthBToA * 100).toFixed(1) }} cm</span>
            </div>
          </div>
        </div>

        <div class="two">
          <div class="card">
            <div class="card-label">
              <span>障碍预警</span>
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
              覆盖率 {{ coverage ?? '—' }}% · 掩码 {{ current?.road_status ?? '—' }}
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-label">替代通行空间评估</div>
          <div class="card-value" :class="alternative === 'AVAILABLE' ? 'tone-safe' : 'tone-unknown'">
            {{ ALTERNATIVE_TEXT[alternative] ?? alternative }}
          </div>
          <div class="card-sub mono">{{ alternative }}</div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.dash { display: flex; flex-direction: column; gap: 12px; }

/* 顶栏 */
.dash-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-bottom: 4px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.25);
}
.dash-title { display: flex; align-items: center; gap: 8px; font-size: 15px; }
.dash-sub { color: var(--muted); font-size: 12px; letter-spacing: 0.04em; }
.dash-meta { display: flex; align-items: center; gap: 12px; font-size: 12px; }
.dash-live { color: #16a34a; font-weight: 600; }
.stamp { color: var(--muted); }
.muted { color: var(--muted); }

/* 主区：左画面 右数据 */
.dash-body {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(330px, 1fr);
  gap: 16px;
  align-items: start;
}

/* 左：画面 */
.viewer { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.viewer-frame {
  position: relative;
  border-radius: 14px;
  overflow: hidden;
  background: #06090d;
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 10px 30px rgba(2, 6, 23, 0.18);
}
.viewer-frame img { display: block; width: 100%; height: auto; }
.viewer-frame.is-stale img { opacity: 0.45; filter: grayscale(0.55); }

.hud {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 8px;
  background: rgba(2, 6, 23, 0.62);
  color: #e5e7eb;
  font-size: 12px;
  backdrop-filter: blur(3px);
}
.hud-tl { top: 12px; left: 12px; }
.hud-tr { top: 12px; right: 12px; font-size: 11px; opacity: 0.9; }
.hud-bl { bottom: 12px; left: 12px; }
.hud-bl strong { font-size: 14px; }
.hud-sep { opacity: 0.5; }
.hud-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 2px 8px;
  border-radius: 6px;
  background: #6b7280;
  color: #fff;
}
.hud-chip.safe { background: #16a34a; }
.hud-chip.low { background: #ca8a04; }
.hud-chip.medium { background: #ea580c; }
.hud-chip.high { background: #dc2626; }
.hud-chip.unknown { background: #6b7280; }
.hud-text { font-weight: 600; }

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
  padding: 90px 12px;
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
}

.viewer-foot {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 11px;
  color: var(--muted);
}

/* 右：数据面板 */
.panel { display: flex; flex-direction: column; gap: 12px; min-width: 0; }

.hero {
  border-radius: 14px;
  padding: 18px 18px 16px;
  color: #fff;
}
.hero.safe { background: linear-gradient(135deg, #15803d, #16a34a); }
.hero.low { background: linear-gradient(135deg, #a16207, #ca8a04); }
.hero.medium { background: linear-gradient(135deg, #c2410c, #ea580c); }
.hero.high { background: linear-gradient(135deg, #991b1b, #dc2626); }
.hero.unknown { background: linear-gradient(135deg, #374151, #6b7280); }

.hero-label { font-size: 12px; opacity: 0.85; letter-spacing: 0.06em; }
.hero-value { font-size: 36px; font-weight: 700; line-height: 1.15; margin: 4px 0 8px; }
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

@media (max-width: 1080px) {
  .dash-body { grid-template-columns: 1fr; }
  .viewer-frame { min-height: 240px; }
}
@media (max-width: 560px) {
  .two { grid-template-columns: 1fr; }
  .hero-value { font-size: 30px; }
}
</style>
