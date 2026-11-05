<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, frameImageURL } from '../../api/client'
import { EVENT_STATUS_TEXT, type EventStatus, type RockEvent } from '../../api/types'

const items = ref<RockEvent[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const filter = ref<'ALL' | EventStatus>('ALL')
const loading = ref(false)
const message = ref<string | null>(null)

const editing = ref<RockEvent | null>(null)
const draftNote = ref('')
const draftStatus = ref<EventStatus>('OPEN')
const saving = ref(false)

const preview = ref<RockEvent | null>(null)

let timer: number | null = null

async function load() {
  loading.value = true
  try {
    const result = await api.rockEvents(pageSize, (page.value - 1) * pageSize, filter.value)
    items.value = result.items
    total.value = result.total
    message.value = null
  } catch (error) {
    message.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 10_000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})

function openEdit(event: RockEvent) {
  editing.value = event
  draftNote.value = event.note ?? ''
  draftStatus.value = event.status
}

async function save() {
  if (!editing.value) return
  saving.value = true
  try {
    await api.updateRockEvent(editing.value.id, {
      note: draftNote.value,
      status: draftStatus.value,
    })
    editing.value = null
    await load()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '保存失败'
  } finally {
    saving.value = false
  }
}

const statusTag = computed(() => (status: EventStatus) => {
  switch (status) {
    case 'RESOLVED': return 'success'
    case 'IGNORED': return 'info'
    case 'ACKNOWLEDGED': return 'warning'
    default: return 'danger'
  }
})

function timeText(ns: number): string {
  return new Date(ns / 1e6).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="events">
    <el-alert v-if="message" type="error" :closable="false" show-icon
              :title="message" style="margin-bottom: 12px" />

    <div class="card head">
      <div class="left">
        <span class="title">落石事件</span>
        <span class="muted">共 {{ total }} 条 · 每 10 秒刷新</span>
      </div>
      <el-radio-group v-model="filter" size="small" @change="page = 1; load()">
        <el-radio-button label="ALL">全部</el-radio-button>
        <el-radio-button label="OPEN">待处理</el-radio-button>
        <el-radio-button label="ACKNOWLEDGED">已确认</el-radio-button>
        <el-radio-button label="RESOLVED">已处理</el-radio-button>
        <el-radio-button label="IGNORED">已忽略</el-radio-button>
      </el-radio-group>
    </div>

    <el-table :data="items" v-loading="loading" size="small" stripe>
      <el-table-column label="截图" width="120">
        <template #default="{ row }">
          <el-image
            v-if="row.frame_id"
            :src="frameImageURL(row.frame_id)"
            fit="cover"
            class="thumb"
            @click="preview = row"
          >
            <template #error>
              <div class="thumb-missing">已清理</div>
            </template>
          </el-image>
        </template>
      </el-table-column>

      <el-table-column label="发生时间" width="170">
        <template #default="{ row }">{{ timeText(row.ts_ns) }}</template>
      </el-table-column>

      <el-table-column label="风险等级" width="110">
        <template #default="{ row }">
          <el-tag :type="row.risk_level === 'HIGH' ? 'danger'
            : row.risk_level === 'MEDIUM' ? 'warning' : 'info'" size="small">
            {{ row.risk_level }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="rock_count" label="落石数" width="90" />

      <el-table-column label="处理状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)" size="small" effect="plain">
            {{ EVENT_STATUS_TEXT[row.status as EventStatus] }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="note" label="备注" min-width="180">
        <template #default="{ row }">
          <span v-if="row.note">{{ row.note }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">处理</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="!items.length && !loading" class="card empty">
      暂无落石事件。出现落石时系统会自动登记一条，可在备注里补记现场情况。
    </div>

    <el-pagination
      v-if="total > pageSize"
      style="margin-top: 12px"
      layout="total, prev, pager, next"
      :total="total" :page-size="pageSize" :current-page="page"
      @current-change="(value: number) => { page = value; load() }"
    />

    <!-- 处理弹窗 -->
    <el-dialog v-model="editing" :title="editing ? `事件 #${editing.id}` : ''" width="420px">
      <div v-if="editing" class="dialog-body">
        <div class="row">
          <span class="k">时间</span>
          <span class="mono">{{ timeText(editing.ts_ns) }}</span>
        </div>
        <div class="row">
          <span class="k">风险</span>
          <el-tag size="small">{{ editing.risk_level }}</el-tag>
        </div>
        <div class="row">
          <span class="k">处理状态</span>
          <el-select v-model="draftStatus" size="small" style="width: 160px">
            <el-option label="待处理" value="OPEN" />
            <el-option label="已确认" value="ACKNOWLEDGED" />
            <el-option label="已处理" value="RESOLVED" />
            <el-option label="已忽略" value="IGNORED" />
          </el-select>
        </div>
        <div class="row note-row">
          <span class="k">备注</span>
          <el-input v-model="draftNote" type="textarea" :rows="3"
                    placeholder="现场处置情况、复核结论等" />
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="editing = null">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 大图预览 -->
    <el-dialog v-model="preview" title="事件截图" width="720px">
      <img v-if="preview" :src="frameImageURL(preview.frame_id)" class="preview-img" />
    </el-dialog>
  </div>
</template>

<style scoped>
.events { display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.title { font-weight: 600; margin-right: 8px; }
.muted { color: var(--muted); font-size: 12px; }
.thumb { width: 96px; height: 72px; border-radius: 6px; cursor: zoom-in; background: #0b0f14; }
.thumb-missing { width: 96px; height: 72px; display: flex; align-items: center;
  justify-content: center; color: #6b7280; font-size: 11px; background: #0b0f14; }
.empty { color: var(--muted); font-size: 13px; }
.dialog-body { display: flex; flex-direction: column; gap: 12px; }
.row { display: flex; align-items: center; gap: 12px; }
.note-row { align-items: flex-start; }
.k { width: 72px; color: var(--muted); font-size: 12px; }
.preview-img { width: 100%; border-radius: 8px; }
</style>
