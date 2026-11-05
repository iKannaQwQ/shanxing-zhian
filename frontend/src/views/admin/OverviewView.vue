<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api/client'
import { normalizePassability, rowToMessage } from '../../api/mappers'
import { PASSABILITY_TEXT, RISK_TEXT, type EventRow, type Passability, type RiskLevel } from '../../api/types'
import { worstPassability } from '../../api/verdict'
import { useStatusStream } from '../../composables/useStatusStream'
import { useSystemStatus } from '../../composables/useSystemStatus'

const { connected, last } = useStatusStream()
const { status, error: statusError } = useSystemStatus(2000)

const events = ref<EventRow[]>([])
let eventTimer: number | null = null

async function loadEvents() {
  try {
    events.value = (await api.events(8)).items
  } catch {
    /* 保留上一次结果 */
  }
}

onMounted(() => {
  loadEvents()
  eventTimer = window.setInterval(loadEvents, 10_000)
})
onUnmounted(() => {
  if (eventTimer !== null) window.clearInterval(eventTimer)
})

const current = computed(() => {
  if (last.value) return last.value
  const latest = status.value?.latest
  return latest ? rowToMessage(latest) : null
})

const risk = computed<RiskLevel>(() => (current.value?.overall_risk as RiskLevel) ?? 'UNKNOWN')
const riskClass = computed(() => risk.value.toLowerCase())
const passability = computed<Passability>(() => worstPassability(
  normalizePassability(current.value?.passability?.A_TO_B),
  normalizePassability(current.value?.passability?.B_TO_A),
))

const aiFps = computed(() => status.value?.ai_fps ?? 0)
const system = computed(() => status.value?.system)
const onlineDevices = computed(() => system.value?.devices_online ?? 0)
const totalDevices = computed(() => system.value?.devices_total ?? 0)

const aiStateText = computed(() => {
  const state = system.value?.ai_worker
  return state === 'ONLINE' ? '在线' : state === 'STALLED' ? '停滞' : '空闲'
})
const aiStateTone = computed(() => {
  const state = system.value?.ai_worker
  return state === 'ONLINE' ? 'success' : state === 'STALLED' ? 'danger' : 'info'
})

const lastResultText = computed(() => {
  const ms = system.value?.ai_last_result_ms
  if (ms == null) return '从未产出'
  if (ms < 1000) return '刚刚'
  if (ms < 60_000) return `${(ms / 1000).toFixed(0)} 秒前`
  return `${(ms / 60_000).toFixed(1)} 分钟前`
})

const stale = computed(() => Boolean(status.value?.stale))
const isDemo = computed(() => status.value?.latest?.source === 'SIMULATED')
const artifacts = computed(() => status.value?.artifacts)

function mb(bytes: number | undefined): string {
  if (bytes == null) return '—'
  return bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${(bytes / 1024).toFixed(0)} KB`
}

function timeText(ns: number): string {
  return new Date(ns / 1e6).toLocaleString('zh-CN', { hour12: false })
}

function eventTone(level: string): string {
  if (level === 'CRITICAL' || level === 'ERROR') return 'danger'
  if (level === 'WARNING') return 'warning'
  return 'info'
}
</script>

<template>
  <div class="overview">
    <el-alert
      v-if="statusError"
      type="error" :closable="false" show-icon
      title="后端不可达" :description="statusError"
      style="margin-bottom: 12px"
    />
    <el-alert
      v-if="isDemo"
      type="warning" :closable="false" show-icon
      title="DEMO 数据"
      description="当前判定来自注入的模拟结果，不是真实推理输出。"
      style="margin-bottom: 12px"
    />
    <el-alert
      v-if="stale"
      type="warning" :closable="false" show-icon
      title="数据已过期"
      description="最新判定已超过阈值，以下数字反映的是历史状态。"
      style="margin-bottom: 12px"
    />

    <!-- 四个关键指标 -->
    <div class="stats">
      <div class="stat card">
        <div class="card-label">当前道路状态</div>
        <div class="stat-value" :class="`tone-${passability.toLowerCase()}`">
          {{ PASSABILITY_TEXT[passability] }}
        </div>
        <div class="card-sub mono">{{ passability }}</div>
      </div>

      <div class="stat card">
        <div class="card-label">当前风险等级</div>
        <div class="stat-value">
          <span class="pill" :class="riskClass">{{ risk }}</span>
        </div>
        <div class="card-sub">{{ RISK_TEXT[risk] }}</div>
      </div>

      <div class="stat card">
        <div class="card-label">AI 推理 FPS</div>
        <div class="stat-value mono">{{ aiFps.toFixed(1) }}</div>
        <div class="card-sub">最近 10 秒完成 {{ (aiFps * 10).toFixed(0) }} 帧</div>
      </div>

      <div class="stat card">
        <div class="card-label">在线设备</div>
        <div class="stat-value mono">{{ onlineDevices }} / {{ totalDevices }}</div>
        <div class="card-sub">
          <span class="live-badge" :class="{ on: connected }">
            <span class="led" />{{ connected ? 'WebSocket 已连接' : 'WebSocket 断开' }}
          </span>
        </div>
      </div>
    </div>

    <!-- 系统运行状态 -->
    <div class="card section">
      <div class="card-label"><span>系统运行状态</span></div>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="后端">
          <el-tag type="success" size="small">正常</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="AI Worker">
          <el-tag :type="aiStateTone" size="small">{{ aiStateText }}</el-tag>
          <span class="muted" style="margin-left: 8px">最近结果 {{ lastResultText }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="待处理帧">
          {{ system?.pending_frames ?? 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="失败 / 过期帧">
          {{ system?.frames_failed ?? 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="WebSocket 客户端">
          {{ system?.ws_clients ?? 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="原始文件存储">
          {{ artifacts?.files ?? 0 }} 个 · {{ mb(artifacts?.bytes) }}
        </el-descriptions-item>
        <el-descriptions-item label="累计判定">
          {{ status?.counters.processed ?? 0 }} 帧
        </el-descriptions-item>
        <el-descriptions-item label="数据库事件">
          {{ status?.db.events ?? 0 }} 条
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <!-- 最新预警 -->
    <div class="card section">
      <div class="card-label">
        <span>最新预警</span>
        <span class="muted">每 10 秒刷新</span>
      </div>
      <div v-if="!events.length" class="muted empty">暂无事件</div>
      <div v-for="event in events" :key="event.id" class="event">
        <span class="mono when">{{ timeText(event.ts_ns) }}</span>
        <el-tag :type="eventTone(event.level)" size="small" effect="dark">
          {{ event.level }}
        </el-tag>
        <span class="code mono">{{ event.code }}</span>
        <span class="msg">{{ event.message ?? '' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overview { display: flex; flex-direction: column; gap: 12px; }

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.stat-value { font-size: 26px; font-weight: 650; line-height: 1.3; }

.muted { color: var(--muted); font-size: 12px; }

.section { padding: 14px 16px; }

.empty { padding: 8px 0; }

.event {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--line);
  font-size: 13px;
}
.event:last-child { border-bottom: none; }
.when { color: var(--muted); min-width: 150px; }
.code { color: #374151; min-width: 190px; }
.msg { color: var(--muted); }
</style>
