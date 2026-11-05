<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, frameImageURL } from '../../api/client'
import type { AssessmentRow, TimelineBucket } from '../../api/types'

const minutes = ref(30)
const buckets = ref(30)
const timeline = ref<TimelineBucket[]>([])
const rows = ref<AssessmentRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 15
const loading = ref(false)
const message = ref<string | null>(null)
const preview = ref<AssessmentRow | null>(null)

let timer: number | null = null

async function loadTimeline() {
  try {
    const result = await api.timeline(minutes.value, buckets.value)
    timeline.value = result.buckets
    total.value = result.total
  } catch (error) {
    message.value = error instanceof Error ? error.message : '加载失败'
  }
}

async function loadRows() {
  loading.value = true
  try {
    const result = await api.assessments(pageSize, (page.value - 1) * pageSize)
    rows.value = result.items
  } catch (error) {
    message.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function reload() {
  await Promise.all([loadTimeline(), loadRows()])
}

onMounted(() => {
  reload()
  timer = window.setInterval(loadTimeline, 10_000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})
watch(minutes, () => { page.value = 1; reload() })

const RISK_COLOR: Record<string, string> = {
  SAFE: '#16a34a', LOW: '#ca8a04', MEDIUM: '#ea580c', HIGH: '#dc2626',
  UNKNOWN: '#9ca3af',
}

const summary = computed(() => {
  const filled = timeline.value.filter((b) => b.count > 0)
  const blocked = timeline.value.reduce((sum, b) => sum + b.blocked, 0)
  const unknown = timeline.value.reduce((sum, b) => sum + b.unknown, 0)
  return {
    activeBuckets: filled.length,
    total: timeline.value.reduce((sum, b) => sum + b.count, 0),
    blocked,
    unknown,
  }
})

function bucketTime(ns: number): string {
  return new Date(ns / 1e6).toLocaleTimeString('zh-CN', { hour12: false })
}

function bucketTooltip(bucket: TimelineBucket): string {
  if (!bucket.count) return `${bucketTime(bucket.start_ns)} 无数据`
  return `${bucketTime(bucket.start_ns)} · ${bucket.count} 帧 · 最不利 ${bucket.worst_risk}`
    + `${bucket.blocked ? ` · 阻断 ${bucket.blocked}` : ''}`
    + `${bucket.unknown ? ` · 未知 ${bucket.unknown}` : ''}`
}

function timeText(ns: number): string {
  return new Date(ns / 1e6).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="history">
    <el-alert v-if="message" type="error" :closable="false" show-icon
              :title="message" style="margin-bottom: 12px" />

    <div class="card head">
      <div class="left">
        <span class="title">历史数据</span>
        <span class="muted">窗口内共 {{ summary.total }} 帧判定</span>
      </div>
      <el-radio-group v-model="minutes" size="small">
        <el-radio-button :label="30">30 分钟</el-radio-button>
        <el-radio-button :label="180">3 小时</el-radio-button>
        <el-radio-button :label="720">12 小时</el-radio-button>
        <el-radio-button :label="1440">24 小时</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 风险时间轴 -->
    <div class="card">
      <div class="card-label">
        <span>风险等级时间轴</span>
        <span class="muted">{{ buckets }} 个分桶 · 每桶取最不利等级</span>
      </div>
      <div class="strip">
        <el-tooltip
          v-for="(bucket, index) in timeline" :key="index"
          :content="bucketTooltip(bucket)" placement="top" :show-after="120"
        >
          <div
            class="cell"
            :style="{ background: bucket.count ? RISK_COLOR[bucket.worst_risk ?? 'UNKNOWN'] : '#e5e7eb' }"
          />
        </el-tooltip>
      </div>
      <div class="axis">
        <span class="mono muted">{{ timeline.length ? bucketTime(timeline[0].start_ns) : '' }}</span>
        <span class="mono muted">
          {{ timeline.length ? bucketTime(timeline[timeline.length - 1].start_ns) : '' }}
        </span>
      </div>
      <div class="legend">
        <span v-for="(color, level) in RISK_COLOR" :key="level" class="legend-item">
          <span class="swatch" :style="{ background: color }" />{{ level }}
        </span>
        <span class="legend-item"><span class="swatch" style="background:#e5e7eb" />无数据</span>
      </div>
      <div class="stats">
        有数据的分桶 {{ summary.activeBuckets }} / {{ buckets }} ·
        出现阻断 {{ summary.blocked }} 帧 · 无法判定 {{ summary.unknown }} 帧
      </div>
    </div>

    <!-- 历史判定 -->
    <div class="card">
      <div class="card-label">
        <span>历史判定记录</span>
        <span class="muted">点击行查看图像</span>
      </div>
      <el-table :data="rows" v-loading="loading" size="small" stripe
                @row-click="(row: AssessmentRow) => preview = row">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ timeText(row.ts_assessed_ns) }}</template>
        </el-table-column>
        <el-table-column label="风险" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.overall_risk === 'HIGH' ? 'danger'
              : row.overall_risk === 'MEDIUM' ? 'warning'
              : row.overall_risk === 'UNKNOWN' ? 'info' : 'success'">
              {{ row.overall_risk }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="system_status" label="系统状态" width="130" />
        <el-table-column prop="a_to_b" label="A→B" width="120" />
        <el-table-column prop="b_to_a" label="B→A" width="120" />
        <el-table-column prop="rock_count" label="落石" width="80" />
        <el-table-column label="覆盖率" width="100">
          <template #default="{ row }">
            {{ row.road_coverage != null ? (row.road_coverage * 100).toFixed(1) + '%' : '—' }}
          </template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.source === 'SIMULATED'" type="warning" size="small" effect="dark">
              DEMO
            </el-tag>
            <span v-else class="muted">LIVE</span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        style="margin-top: 12px"
        layout="total, prev, pager, next"
        :total="Math.max(total, 1)" :page-size="pageSize" :current-page="page"
        @current-change="(value: number) => { page = value; loadRows() }"
      />
    </div>

    <el-dialog v-model="preview" title="历史图像" width="720px">
      <div v-if="preview">
        <img :src="frameImageURL(preview.frame_id)" class="preview-img" />
        <div class="muted" style="margin-top: 8px">
          {{ timeText(preview.ts_assessed_ns) }} · 风险 {{ preview.overall_risk }}
          · 落石 {{ preview.rock_count ?? 0 }}
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.history { display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.title { font-weight: 600; margin-right: 8px; }
.muted { color: var(--muted); font-size: 12px; }

.strip { display: flex; gap: 2px; height: 42px; }
.cell { flex: 1; border-radius: 2px; min-width: 2px; }

.axis { display: flex; justify-content: space-between; margin-top: 4px; }

.legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; }
.legend-item { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

.stats { margin-top: 8px; font-size: 12px; color: var(--muted); }
.preview-img { width: 100%; border-radius: 8px; }
</style>
