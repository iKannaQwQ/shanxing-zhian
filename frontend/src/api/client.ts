import type { AssessmentRow, DeviceRow, EventRow, StatusPayload } from './types'

export type { AssessmentRow, DeviceRow, EventRow, StatusPayload } from './types'
export type {
  AlternativeStatus, AssessmentMessage, InferenceSource, Passability, RiskLevel,
} from './types'

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

/** 最新一帧的 RGB 地址（后端按 frame_id 提供）。 */
export function frameImageURL(frameId: string): string {
  return `${BASE}/api/v1/frames/${frameId}/rgb`
}

export function wsURL(): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${location.host}/ws/status`
}
