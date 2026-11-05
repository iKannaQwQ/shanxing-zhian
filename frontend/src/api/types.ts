/** 与后端契约对齐的枚举与实体。 */

/** 风险等级 */
export type RiskLevel = 'SAFE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN'

/**
 * 通行状态。
 *
 * 注意：后端（C++ 引擎与契约）目前使用 RESTRICTED 表示"部分受阻"，
 * 产品侧统一叫 PARTIALLY_BLOCKED。两者在 `mappers.ts` 里做一次映射，
 * 避免枚举词表在页面里各写一套。
 */
export type Passability = 'PASSABLE' | 'PARTIALLY_BLOCKED' | 'BLOCKED' | 'UNKNOWN'

/** 替代通行空间 */
export type AlternativeStatus = 'AVAILABLE' | 'UNAVAILABLE' | 'UNKNOWN' | 'NOT_EVALUATED'

/** 结果来源：LIVE=真实推理，SIMULATED=注入的模拟数据 */
export type InferenceSource = 'LIVE' | 'SIMULATED' | string | null

export interface AssessmentRow {
  frame_id: string
  device_id: string
  ts_assessed_ns: number
  overall_risk: RiskLevel
  system_status: string
  a_to_b: string | null
  b_to_a: string | null
  alternative_status: string | null
  road_detected: number
  road_status: string | null
  road_coverage: number | null
  rock_count: number | null
  processing_ms: number | null
  source: InferenceSource
  json: string
}

export interface EventRow {
  id: number
  ts_ns: number
  frame_id: string | null
  device_id: string | null
  level: 'INFO' | 'WARNING' | 'CRITICAL' | 'ERROR'
  code: string
  message: string | null
}

export interface DeviceRow {
  device_id: string
  first_seen_ns: number
  last_seen_ns: number
  frames: number
  online?: boolean
  silent_ms?: number
  thermal?: string | null
  target_fps?: number | null
  last_status?: string | null
}

export interface StatusPayload {
  ready: boolean
  server_ns?: number
  latest: AssessmentRow | null
  age_ms?: number | null
  stale?: boolean
  stale_after_ms?: number
  queue: { depth: number; capacity: number | null }
  counters: { queued: number; processed: number; failed: number; dropped: number }
  devices?: DeviceRow[]
  db: { frames: number; processed: number; failed: number; dropped: number;
        assessments: number; events: number }
  artifacts?: { files: number; bytes: number }
  ws_clients: number
  last_processing_ms: number
  ai_fps?: number
  system?: {
    backend: string
    ai_worker: 'ONLINE' | 'IDLE' | 'STALLED'
    ai_last_result_ms: number | null
    pending_frames: number
    devices_online: number
    devices_total: number
    ws_clients: number
    frames_failed: number
  }
}

/** WebSocket 推送的实时判定。 */
export interface AssessmentMessage {
  type: 'assessment'
  frame_id: string
  device_id: string
  seq: number | null
  ts_ns: number
  overall_risk: RiskLevel
  system_status: string
  passability: Record<string, string>
  alternative: string
  road_status: string | null
  road_coverage: number | null
  rock_count: number
  processing_ms: number
  dropped_since_last: number
  reasons: string[]
}

export const RISK_TEXT: Record<RiskLevel, string> = {
  SAFE: '未见侵入',
  LOW: '附近有落石',
  MEDIUM: '部分侵入',
  HIGH: '严重阻断',
  UNKNOWN: '无法判定',
}

export const PASSABILITY_TEXT: Record<Passability, string> = {
  PASSABLE: '可通行',
  PARTIALLY_BLOCKED: '部分受阻',
  BLOCKED: '阻断',
  UNKNOWN: '未知',
}

export const ALTERNATIVE_TEXT: Record<AlternativeStatus, string> = {
  AVAILABLE: '可确认可用',
  UNAVAILABLE: '确认不可用',
  UNKNOWN: '无法确认',
  NOT_EVALUATED: '无需替代',
}
