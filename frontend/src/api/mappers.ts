import type { AssessmentMessage, AssessmentRow, Passability } from './types'

/** 后端 RESTRICTED -> 产品词表 PARTIALLY_BLOCKED，其余原样。 */
export function normalizePassability(raw: unknown): Passability {
  switch (raw) {
    case 'PASSABLE':
      return 'PASSABLE'
    case 'RESTRICTED':
    case 'PARTIALLY_BLOCKED':
      return 'PARTIALLY_BLOCKED'
    case 'BLOCKED':
      return 'BLOCKED'
    default:
      return 'UNKNOWN'
  }
}

export function normalizeAlternative(raw: unknown): string {
  return typeof raw === 'string' && raw.length > 0 ? raw : 'UNKNOWN'
}

function reasonsFromJson(raw: unknown): string[] {
  if (typeof raw !== 'string') return []
  try {
    const parsed = JSON.parse(raw) as { reasons?: Array<{ code?: string }> }
    return Array.isArray(parsed.reasons)
      ? parsed.reasons.map((r) => r?.code).filter((c): c is string => typeof c === 'string').slice(0, 4)
      : []
  } catch {
    return []
  }
}

/** 数据库行 -> 与 WebSocket 推送一致的消息结构。 */
export function rowToMessage(row: AssessmentRow | Record<string, unknown>): AssessmentMessage {
  const r = row as Record<string, unknown>
  return {
    type: 'assessment',
    frame_id: String(r.frame_id ?? ''),
    device_id: String(r.device_id ?? ''),
    seq: null,
    ts_ns: Number(r.ts_assessed_ns ?? 0),
    overall_risk: (r.overall_risk as AssessmentMessage['overall_risk']) ?? 'UNKNOWN',
    system_status: String(r.system_status ?? '-'),
    passability: {
      A_TO_B: normalizePassability(r.a_to_b),
      B_TO_A: normalizePassability(r.b_to_a),
    },
    alternative: normalizeAlternative(r.alternative_status),
    road_status: (r.road_status as string) ?? null,
    road_coverage: (r.road_coverage as number) ?? null,
    rock_count: Number(r.rock_count ?? 0),
    processing_ms: Number(r.processing_ms ?? 0),
    dropped_since_last: 0,
    reasons: reasonsFromJson(r.json),
  }
}
