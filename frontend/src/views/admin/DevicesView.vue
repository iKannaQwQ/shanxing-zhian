<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api/client'
import { useSystemStatus } from '../../composables/useSystemStatus'
import type { DeviceRow } from '../../api/types'

const { status } = useSystemStatus(3000)

const devices = ref<DeviceRow[]>([])
const loading = ref(false)
const message = ref<string | null>(null)
let timer: number | null = null

async function load() {
  loading.value = true
  try {
    devices.value = (await api.devices()).items
    message.value = null
  } catch (error) {
    message.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 5000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})

const onlineCount = computed(() => devices.value.filter((d) => d.online).length)
const totalFrames = computed(() =>
  devices.value.reduce((sum, d) => sum + (d.frames ?? 0), 0))
const totalDropped = computed(() =>
  devices.value.reduce((sum, d) => sum + (d.dropped_recent ?? 0), 0))

const aiState = computed(() => status.value?.system?.ai_worker ?? 'IDLE')
const aiText = computed(() => ({
  ONLINE: '在线', IDLE: '空闲', STALLED: '停滞',
} as Record<string, string>)[aiState.value] ?? '未知')
const aiTone = computed(() => ({
  ONLINE: 'success', IDLE: 'info', STALLED: 'danger',
} as Record<string, string>)[aiState.value] ?? 'info')
const offlineSeconds = computed(() =>
  (status.value as { device_offline_s?: number } | null)?.device_offline_s ?? 15)

const aiLast = computed(() => {
  const ms = status.value?.system?.ai_last_result_ms
  if (ms == null) return '从未产出'
  if (ms < 1000) return '刚刚'
  if (ms < 60_000) return `${(ms / 1000).toFixed(0)} 秒前`
  return `${(ms / 60_000).toFixed(1)} 分钟前`
})

function timeText(ns: number | null | undefined): string {
  if (!ns) return '—'
  return new Date(ns / 1e6).toLocaleString('zh-CN', { hour12: false })
}

function latencyText(device: DeviceRow): string {
  if (device.latency_ms == null) return '未上报'
  return `${device.latency_ms.toFixed(0)} ms`
}

function fpsTone(fps: number): string {
  if (fps <= 0) return 'muted'
  if (fps >= 8) return 'ok'
  if (fps >= 3) return 'warn'
  return 'bad'
}

function thermalText(device: DeviceRow): string {
  return device.thermal ?? '未知'
}
</script>

<template>
  <div class="devices">
    <el-alert v-if="message" type="error" :closable="false" show-icon
              :title="message" style="margin-bottom: 12px" />

    <div class="stats">
      <div class="card">
        <div class="card-label">在线采集端</div>
        <div class="stat-value mono">{{ onlineCount }} / {{ devices.length }}</div>
        <div class="card-sub">超过 {{ offlineSeconds }} 秒无数据判为离线</div>
      </div>
      <div class="card">
        <div class="card-label">AI 服务状态</div>
        <div class="stat-value">
          <el-tag :type="aiTone" size="large">{{ aiText }}</el-tag>
        </div>
        <div class="card-sub">最近结果 {{ aiLast }}</div>
      </div>
      <div class="card">
        <div class="card-label">累计上传帧</div>
        <div class="stat-value mono">{{ totalFrames }}</div>
        <div class="card-sub">所有设备历史累计</div>
      </div>
      <div class="card">
        <div class="card-label">近 5 分钟丢帧</div>
        <div class="stat-value mono" :class="{ bad: totalDropped > 0 }">
          {{ totalDropped }}
        </div>
        <div class="card-sub">客户端队列溢出累计</div>
      </div>
    </div>

    <div class="card">
      <div class="card-label">
        <span>采集端列表</span>
        <span class="muted">每 5 秒刷新</span>
      </div>

      <div class="table-scroll">
      <el-table :data="devices" v-loading="loading" size="small" stripe>
        <el-table-column label="设备" min-width="190">
          <template #default="{ row }">
            <div class="device-id mono">{{ row.device_id }}</div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.online ? 'success' : 'info'" size="small">
              {{ row.online ? '在线' : '离线' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="最后上传" width="180">
          <template #default="{ row }">
            {{ timeText(row.last_frame_ns ?? row.last_seen_ns) }}
          </template>
        </el-table-column>

        <el-table-column label="FPS" width="90">
          <template #default="{ row }">
            <span class="mono" :class="fpsTone(row.fps ?? 0)">
              {{ (row.fps ?? 0).toFixed(1) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="网络延迟" width="110">
          <template #default="{ row }">
            <span class="mono" :class="{ muted: row.latency_ms == null }">
              {{ latencyText(row) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="丢帧" width="90">
          <template #default="{ row }">
            <span class="mono" :class="{ bad: (row.dropped_recent ?? 0) > 0 }">
              {{ row.dropped_recent ?? 0 }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="热状态" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.thermal"
                    :type="row.thermal === '正常' ? 'success'
                      : row.thermal === '偏热' ? 'warning' : 'danger'"
                    size="small" effect="plain">
              {{ thermalText(row) }}
            </el-tag>
            <span v-else class="muted">未知</span>
          </template>
        </el-table-column>

        <el-table-column label="目标 FPS" width="100">
          <template #default="{ row }">
            <span class="mono">{{ row.target_fps ?? '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="frames" label="累计帧" width="100" />
      </el-table>
      </div>

      <div v-if="!devices.length && !loading" class="muted empty">
        还没有设备上报。iPhone 开始采集后会自动出现在这里。
      </div>
    </div>

    <div class="card hint">
      <div class="card-label">口径说明</div>
      <ul>
        <li><b>FPS</b>：服务端最近 10 秒收到的帧数 ÷ 10，反映实际到达速率，不是设备采集帧率。</li>
        <li><b>网络延迟</b>：由采集端在上报心跳时附带，未上报时显示「未上报」——不猜测。</li>
        <li><b>丢帧</b>：近 5 分钟内客户端上报的队列溢出累计数。</li>
        <li><b>热状态</b>：设备降频会直接影响 FPS，出现「过热」时应结合目标 FPS 一起看。</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.devices { display: flex; flex-direction: column; gap: 12px; }

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.stat-value { font-size: 24px; font-weight: 650; }

.muted { color: var(--muted); font-size: 12px; }
.device-id { font-size: 12px; word-break: break-all; }

.ok { color: var(--risk-safe); }
.warn { color: var(--risk-low); }
.bad { color: var(--risk-high); }

.empty { padding: 20px 0; text-align: center; }

.hint ul { margin: 6px 0 0; padding-left: 18px; color: var(--muted); font-size: 12px; line-height: 1.9; }
.hint b { color: var(--ink-soft); }
</style>
