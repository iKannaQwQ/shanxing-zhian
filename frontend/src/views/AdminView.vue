<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type AssessmentRow, type DeviceRow, type EventRow, type StatusPayload } from '../api/client'

const activeTab = ref('overview')
const status = ref<StatusPayload | null>(null)
const assessments = ref<AssessmentRow[]>([])
const events = ref<EventRow[]>([])
const devices = ref<DeviceRow[]>([])

const page = ref(1)
const pageSize = 20
const total = ref(0)
const loading = ref(false)
const message = ref<string | null>(null)

let timer: number | null = null

async function loadStatus() {
  try {
    status.value = await api.status()
    message.value = null
  } catch (error) {
    message.value = error instanceof Error ? error.message : '后端不可达'
  }
}

async function loadAssessments() {
  loading.value = true
  try {
    const result = await api.assessments(pageSize, (page.value - 1) * pageSize)
    assessments.value = result.items
    total.value = status.value?.db.assessments ?? result.items.length
  } catch (error) {
    message.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadEvents() {
  try {
    events.value = (await api.events(50)).items
  } catch {
    /* 忽略 */
  }
}

async function loadDevices() {
  try {
    devices.value = (await api.devices()).items
  } catch {
    /* 忽略 */
  }
}

async function refreshAll() {
  await loadStatus()
  if (activeTab.value === 'assessments') await loadAssessments()
  if (activeTab.value === 'events') await loadEvents()
  if (activeTab.value === 'devices') await loadDevices()
}

onMounted(async () => {
  await refreshAll()
  await loadAssessments()
  timer = window.setInterval(loadStatus, 5_000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})

const riskTagType = computed(() => {
  const risk = status.value?.latest?.overall_risk
  if (risk === 'SAFE') return 'success'
  if (risk === 'LOW') return 'warning'
  if (risk === 'MEDIUM' || risk === 'HIGH') return 'danger'
  return 'info'
})

function timeText(ns: number): string {
  return new Date(ns / 1_000_000).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="admin-page">
    <el-alert
      v-if="message"
      type="error"
      :closable="false"
      show-icon
      :title="`后端不可达：${message}`"
      style="margin-bottom: 14px"
    />

    <el-tabs v-model="activeTab" @tab-change="refreshAll">
      <el-tab-pane label="概览" name="overview">
        <div class="grid">
          <el-card shadow="never">
            <div class="card-title"><span>最近判定</span></div>
            <div class="big-value">
              <el-tag :type="riskTagType" size="large">
                {{ status?.latest?.overall_risk ?? '—' }}
              </el-tag>
            </div>
            <div class="muted" style="margin-top: 8px">
              系统状态 {{ status?.latest?.system_status ?? '—' }} ·
              处理 {{ status?.latest?.processing_ms?.toFixed(0) ?? '—' }} ms
            </div>
          </el-card>

          <el-card shadow="never">
            <div class="card-title"><span>处理队列</span></div>
            <div class="big-value mono">
              {{ status?.queue.depth ?? 0 }} / {{ status?.queue.capacity ?? 0 }}
            </div>
            <div class="muted" style="margin-top: 8px">
              已处理 {{ status?.counters.processed ?? 0 }} ·
              失败 {{ status?.counters.failed ?? 0 }} ·
              丢弃 {{ status?.counters.dropped ?? 0 }}
            </div>
          </el-card>

          <el-card shadow="never">
            <div class="card-title"><span>数据库</span></div>
            <div class="big-value mono">{{ status?.db.assessments ?? 0 }}</div>
            <div class="muted" style="margin-top: 8px">
              帧 {{ status?.db.frames ?? 0 }} · 事件 {{ status?.db.events ?? 0 }} ·
              失败 {{ status?.db.failed ?? 0 }}
            </div>
          </el-card>

          <el-card shadow="never">
            <div class="card-title"><span>连接</span></div>
            <div class="big-value mono">{{ status?.ws_clients ?? 0 }}</div>
            <div class="muted" style="margin-top: 8px">WebSocket 客户端 · 单帧处理
              {{ status?.last_processing_ms?.toFixed(0) ?? '—' }} ms</div>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="历史判定" name="assessments">
        <el-table :data="assessments" v-loading="loading" size="small" stripe style="width: 100%">
          <el-table-column prop="frame_id" label="帧" width="120">
            <template #default="{ row }">
              <span class="mono">{{ String(row.frame_id).slice(0, 8) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="overall_risk" label="风险" width="110" />
          <el-table-column prop="system_status" label="系统状态" width="130" />
          <el-table-column prop="a_to_b" label="A→B" width="110" />
          <el-table-column prop="b_to_a" label="B→A" width="110" />
          <el-table-column prop="rock_count" label="落石" width="80" />
          <el-table-column prop="processing_ms" label="耗时" width="100">
            <template #default="{ row }">{{ row.processing_ms?.toFixed(0) }} ms</template>
          </el-table-column>
          <el-table-column label="时间" min-width="170">
            <template #default="{ row }">{{ timeText(row.ts_assessed_ns) }}</template>
          </el-table-column>
        </el-table>

        <el-pagination
          style="margin-top: 12px"
          layout="total, prev, pager, next"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="(value: number) => { page = value; loadAssessments() }"
        />
      </el-tab-pane>

      <el-tab-pane label="事件流水" name="events">
        <el-table :data="events" size="small" stripe style="width: 100%">
          <el-table-column label="时间" width="180">
            <template #default="{ row }">{{ timeText(row.ts_ns) }}</template>
          </el-table-column>
          <el-table-column prop="level" label="级别" width="110" />
          <el-table-column prop="code" label="代码" width="200" />
          <el-table-column prop="message" label="说明" min-width="240" />
          <el-table-column label="帧" width="110">
            <template #default="{ row }">
              <span class="mono">{{ row.frame_id ? String(row.frame_id).slice(0, 8) : '—' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="设备" name="devices">
        <el-table :data="devices" size="small" stripe style="width: 100%">
          <el-table-column prop="device_id" label="设备" min-width="200" />
          <el-table-column prop="frames" label="帧数" width="100" />
          <el-table-column prop="last_status" label="最近状态" width="150" />
          <el-table-column label="首次出现" width="180">
            <template #default="{ row }">{{ timeText(row.first_seen_ns) }}</template>
          </el-table-column>
          <el-table-column label="最后出现" width="180">
            <template #default="{ row }">{{ timeText(row.last_seen_ns) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
