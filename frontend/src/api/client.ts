/** 后端接口类型与极简请求封装。 */

export type RiskLevel = 'SAFE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN'
export type Passability = 'PASSABLE' | 'RESTRICTED' | 'BLOCKED' | 'UNKNOWN'
export type AlternativeStatus = 'AVAILABLE' | 'UNAVAILABLE' | 'UNKNOWN' | 'NOT_EVALUATED'

export interface AssessmentRow {
  frame_id: string
  device_id: string
  ts_assessed_ns: number
  overall_risk: RiskLevel
  system_status: string
  a_to_b: Passability | null
  b_to_a: Passability | null
  alternative_status: AlternativeStatus | null
  road_detected: number
  road_status: string | null
  road_coverage: number | null
  rock_count: number | null
  processing_ms: number | null
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
  last_status: string | null
}

export interface StatusPayload {
  ready: boolean
  latest: AssessmentRow | null
  queue: { depth: number; capacity: number }
  counters: { queued: number; processed: number; failed: number; dropped: number }
  db: { frames: number; processed: number; failed: number; dropped: number; assessments: number; events: number }
  ws_clients: number
  last_processing_ms: number
}

/** WebSocket 推送的单帧判定。 */
export interface AssessmentMessage {
  type: 'assessment'
  frame_id: string
  device_id: string
  seq: number | null
  ts_ns: number
  overall_risk: RiskLevel
  system_status: string
  passability: Record<string, Passability>
  alternative: AlternativeStatus
  road_status: string | null
  road_coverage: number | null
  rock_count: number
  processing_ms: number
  dropped_since_last: number
  reasons: string[]
}

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`)
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`)
  }
  return (await response.json()) as T
}

export const api = {
  status: () => getJSON<StatusPayload>('/api/v1/status'),
  assessments: (limit = 20, offset = 0) =>
    getJSON<{ items: AssessmentRow[]; limit: number; offset: number }>(
      `/api/v1/assessments?limit=${limit}&offset=${offset}`,
    ),
  events: (limit = 20) => getJSON<{ items: EventRow[] }>(`/api/v1/events?limit=${limit}`),
  devices: () => getJSON<{ items: DeviceRow[] }>('/api/v1/devices'),
}

export function wsURL(): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${location.host}/ws/status`
}
