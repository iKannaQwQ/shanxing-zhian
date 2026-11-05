<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { frameImageURL } from '../../api/client'
import { normalizePassability, rowToMessage } from '../../api/mappers'
import {
  ALTERNATIVE_TEXT, PASSABILITY_TEXT, RISK_TEXT,
  type AlternativeStatus, type Passability, type RiskLevel,
} from '../../api/types'
import { worstPassability } from '../../api/verdict'
import { useStatusStream } from '../../composables/useStatusStream'
import { useSystemStatus } from '../../composables/useSystemStatus'

const { connected, last } = useStatusStream()
const { status } = useSystemStatus(2000)

const current = computed(() => {
  if (last.value) return last.value
  const latest = status.value?.latest
  return latest ? rowToMessage(latest) : null
})
const frameId = computed(() => current.value?.frame_id ?? null)

const risk = computed<RiskLevel>(() => (current.value?.overall_risk as RiskLevel) ?? 'UNKNOWN')
const passability = computed<Passability>(() => worstPassability(
  normalizePassability(current.value?.passability?.A_TO_B),
  normalizePassability(current.value?.passability?.B_TO_A),
))
const alternative = computed(() => (current.value?.alternative ?? 'UNKNOWN') as AlternativeStatus)
const isDemo = computed(() => status.value?.latest?.source === 'SIMULATED')

/* ---------------- 资源加载 ---------------- */

const rgbCanvas = ref<HTMLCanvasElement | null>(null)
const depthCanvas = ref<HTMLCanvasElement | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const showMask = ref(true)
const showBoxes = ref(true)

const perception = ref<Record<string, unknown> | null>(null)
const depthRange = ref<{ min: number; max: number; validRatio: number } | null>(null)

let rgbImage: HTMLImageElement | null = null
let maskImage: HTMLImageElement | null = null
let depthValues: Float32Array | null = null
let depthSize = { width: 0, height: 0 }
let lastLoadAt = 0
let pendingTimer: number | null = null

function loadImage(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => resolve(null)
    image.src = url
  })
}

async function loadFrame(id: string) {
  loading.value = true
  loadError.value = null
  try {
    rgbImage = await loadImage(frameImageURL(id))
    maskImage = await loadImage(`/api/v1/frames/${id}/road_mask`)

    perception.value = await fetch(`/api/v1/frames/${id}/perception`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)

    const depthBuffer = await fetch(`/api/v1/frames/${id}/depth`)
      .then((r) => (r.ok ? r.arrayBuffer() : null))
      .catch(() => null)

    const meta = await fetch(`/api/v1/frames/${id}`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)

    if (depthBuffer && meta) {
      const metaOfDepth = await fetch(`/api/v1/frames/${id}/metadata`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
      const width = metaOfDepth?.depth?.width ?? 256
      const height = metaOfDepth?.depth?.height ?? 192
      const floats = new Float32Array(depthBuffer)
      // 行可能有 padding，按实际长度推算每行浮点数
      const perRow = Math.floor(floats.length / height)
      const clipped = new Float32Array(width * height)
      for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
          clipped[y * width + x] = floats[y * perRow + x]
        }
      }
      depthValues = clipped
      depthSize = { width, height }
      computeDepthRange()
    } else {
      depthValues = null
    }

    lastLoadAt = Date.now()
    composeRGB()
    composeDepth()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function computeDepthRange() {
  if (!depthValues) { depthRange.value = null; return }
  const valid: number[] = []
  for (const value of depthValues) {
    if (Number.isFinite(value) && value > 0) valid.push(value)
  }
  if (!valid.length) { depthRange.value = null; return }
  valid.sort((a, b) => a - b)
  const lo = valid[Math.floor(valid.length * 0.02)]
  const hi = valid[Math.floor(valid.length * 0.98)]
  depthRange.value = {
    min: lo,
    max: hi > lo ? hi : lo + 0.01,
    validRatio: valid.length / depthValues.length,
  }
}

/* ---------------- 绘制 ---------------- */

function composeRGB() {
  const canvas = rgbCanvas.value
  if (!canvas || !rgbImage) return
  canvas.width = rgbImage.naturalWidth
  canvas.height = rgbImage.naturalHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(rgbImage, 0, 0)

  if (showMask.value && maskImage) {
    // 掩码着色：先画成绿色，再用 source-in 保留形状
    const tint = document.createElement('canvas')
    tint.width = canvas.width
    tint.height = canvas.height
    const tintCtx = tint.getContext('2d')
    if (tintCtx) {
      tintCtx.drawImage(maskImage, 0, 0, canvas.width, canvas.height)
      tintCtx.globalCompositeOperation = 'source-in'
      tintCtx.fillStyle = '#22c55e'
      tintCtx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.globalAlpha = 0.32
      ctx.drawImage(tint, 0, 0)
      ctx.globalAlpha = 1
    }
  }

  if (showBoxes.value) {
    const rocks = (perception.value?.rockfalls ?? []) as Array<Record<string, unknown>>
    ctx.lineWidth = Math.max(2, canvas.width / 400)
    ctx.font = `${Math.max(12, canvas.width / 60)}px ui-monospace, monospace`
    for (const rock of rocks) {
      const box = rock.bbox as number[] | undefined
      if (!box || box.length !== 4) continue
      const [x1, y1, x2, y2] = box
      ctx.strokeStyle = '#f97316'
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
      const label = `${rock.rock_id ?? 'rock'} ${(Number(rock.confidence ?? 0) * 100).toFixed(0)}%`
      const width = ctx.measureText(label).width
      ctx.fillStyle = 'rgba(249, 115, 22, 0.9)'
      ctx.fillRect(x1, Math.max(0, y1 - 20), width + 10, 20)
      ctx.fillStyle = '#111827'
      ctx.fillText(label, x1 + 5, Math.max(14, y1 - 6))
    }
  }
}

function colorAt(t: number): [number, number, number] {
  const stops: Array<[number, number, number]> = [
    [37, 99, 235], [6, 182, 212], [34, 197, 94], [250, 204, 21], [239, 68, 68],
  ]
  const position = Math.min(0.999, Math.max(0, t)) * (stops.length - 1)
  const index = Math.floor(position)
  const fraction = position - index
  const a = stops[index]
  const b = stops[Math.min(index + 1, stops.length - 1)]
  return [
    a[0] + (b[0] - a[0]) * fraction,
    a[1] + (b[1] - a[1]) * fraction,
    a[2] + (b[2] - a[2]) * fraction,
  ]
}

function composeDepth() {
  const canvas = depthCanvas.value
  const range = depthRange.value
  if (!canvas) return
  canvas.width = depthSize.width || 256
  canvas.height = depthSize.height || 192
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, canvas.width, canvas.height)

  if (!depthValues || !range) {
    ctx.fillStyle = '#111827'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    return
  }

  const image = ctx.createImageData(canvas.width, canvas.height)
  for (let i = 0; i < depthValues.length; i += 1) {
    const value = depthValues[i]
    const offset = i * 4
    if (!Number.isFinite(value) || value <= 0) {
      image.data[offset] = 17
      image.data[offset + 1] = 24
      image.data[offset + 2] = 39
      image.data[offset + 3] = 255
      continue
    }
    const [r, g, b] = colorAt((value - range.min) / (range.max - range.min))
    image.data[offset] = r
    image.data[offset + 1] = g
    image.data[offset + 2] = b
    image.data[offset + 3] = 255
  }
  ctx.putImageData(image, 0, 0)
}

watch([showMask, showBoxes], () => composeRGB())

/* 新帧到来时节流刷新（10fps 全量加载会压垮浏览器与后端） */
watch(frameId, (id) => {
  if (!id) return
  if (pendingTimer !== null) return
  const wait = Math.max(0, 1000 - (Date.now() - lastLoadAt))
  pendingTimer = window.setTimeout(() => {
    pendingTimer = null
    const latestId = frameId.value
    if (latestId) loadFrame(latestId)
  }, wait)
})

onMounted(() => {
  if (frameId.value) loadFrame(frameId.value)
})
onUnmounted(() => {
  if (pendingTimer !== null) window.clearTimeout(pendingTimer)
})

function timeText(ns: number | undefined | null): string {
  if (!ns) return '—'
  return new Date(ns / 1e6).toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="monitor">
    <div class="bar">
      <div class="left">
        <span class="live-badge" :class="{ on: connected }">
          <span class="led" />{{ connected ? '实时' : '离线' }}
        </span>
        <span class="mono muted">帧 {{ frameId ? frameId.slice(0, 8) : '—' }}</span>
        <span class="mono muted">{{ timeText(current?.ts_ns) }}</span>
        <el-tag v-if="isDemo" type="warning" size="small" effect="dark">DEMO</el-tag>
        <el-tag v-if="loading" type="info" size="small">加载中…</el-tag>
      </div>
      <div class="right">
        <el-switch v-model="showMask" active-text="道路分割" size="small" />
        <el-switch v-model="showBoxes" active-text="检测框" size="small" />
      </div>
    </div>

    <el-alert
      v-if="loadError" type="error" :closable="false" show-icon
      :title="`加载失败：${loadError}`" style="margin-bottom: 10px"
    />

    <div class="panels">
      <div class="panel">
        <div class="panel-head">
          <span>RGB 图像</span>
          <span class="mono muted">{{ perception?.road_mask_status ?? '—' }}</span>
        </div>
        <canvas ref="rgbCanvas" class="view" />
        <div v-if="!rgbImage" class="placeholder">暂无画面</div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span>LiDAR 深度</span>
          <span class="mono muted">
            <template v-if="depthRange">
              {{ depthRange.min.toFixed(2) }}–{{ depthRange.max.toFixed(2) }} m ·
              有效 {{ (depthRange.validRatio * 100).toFixed(0) }}%
            </template>
            <template v-else>无深度数据</template>
          </span>
        </div>
        <canvas ref="depthCanvas" class="view depth" />
        <div class="legend">
          <span class="gradient" />
          <span class="mono muted">近</span>
          <span class="mono muted">远</span>
        </div>
      </div>
    </div>

    <div class="card result">
      <div class="card-label"><span>当前风险评估</span></div>
      <div class="result-grid">
        <div>
          <div class="k">风险等级</div>
          <div class="v"><span class="pill" :class="risk.toLowerCase()">{{ risk }}</span></div>
          <div class="muted">{{ RISK_TEXT[risk] }}</div>
        </div>
        <div>
          <div class="k">通行状态</div>
          <div class="v" :class="`tone-${passability.toLowerCase()}`">
            {{ PASSABILITY_TEXT[passability] }}
          </div>
          <div class="muted mono">{{ passability }}</div>
        </div>
        <div>
          <div class="k">替代通行</div>
          <div class="v">{{ ALTERNATIVE_TEXT[alternative] ?? alternative }}</div>
          <div class="muted mono">{{ alternative }}</div>
        </div>
        <div>
          <div class="k">落石数量</div>
          <div class="v">{{ current?.rock_count ?? 0 }}</div>
          <div class="muted">道路覆盖率
            {{ current?.road_coverage != null ? (current.road_coverage * 100).toFixed(1) : '—' }}%
          </div>
        </div>
      </div>
      <div v-if="current?.reasons?.length" class="reasons">
        理由：<span v-for="code in current.reasons" :key="code" class="code">{{ code }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.monitor { display: flex; flex-direction: column; gap: 12px; }

.bar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap;
}
.bar .left, .bar .right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.muted { color: var(--muted); font-size: 12px; }

.panels { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

.panel {
  background: #0b0f14;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 12px 12px;
}
.panel-head {
  display: flex; align-items: center; justify-content: space-between;
  color: #e5e7eb; font-size: 12px; margin-bottom: 8px;
}
.panel-head .muted { color: #9ca3af; }

.view { display: block; width: 100%; height: auto; border-radius: 8px; }
.depth { image-rendering: pixelated; }
.placeholder {
  padding: 60px 0; text-align: center; color: #6b7280; font-size: 13px;
}

.legend {
  display: flex; align-items: center; gap: 8px; margin-top: 8px;
}
.gradient {
  flex: 1; height: 8px; border-radius: 4px;
  background: linear-gradient(90deg, #2563eb, #06b6d4, #22c55e, #facc15, #ef4444);
}

.result { padding: 14px 16px; }
.result-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 14px;
}
.k { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.v { font-size: 20px; font-weight: 650; }

.reasons { margin-top: 10px; font-size: 12px; color: var(--muted); }
.code {
  font-family: ui-monospace, monospace; background: #f3f4f6;
  padding: 1px 6px; border-radius: 4px; margin-right: 4px;
}

@media (max-width: 900px) {
  .panels { grid-template-columns: 1fr; }
}
</style>
