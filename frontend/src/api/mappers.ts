import type { AssessmentMessage, Passability } from './client'

/** 把数据库行转成与 WebSocket 推送一致的消息结构。 */
export function rowToMessage(row: Record<string, unknown>): AssessmentMessage {
  return {
    type: 'assessment',
    frame_id: String(row.frame_id ?? ''),
    device_id: String(row.device_id ?? ''),
    seq: null,
    ts_ns: Number(row.ts_assessed_ns ?? 0),
    overall_risk: (row.overall_risk as AssessmentMessage['overall_risk']) ?? 'UNKNOWN',
    system_status: String(row.system_status ?? '-'),
    passability: {
      A_TO_B: (row.a_to_b as Passability) ?? 'UNKNOWN',
      B_TO_A: (row.b_to_a as Passability) ?? 'UNKNOWN',
    },
    alternative: (row.alternative_status as AssessmentMessage['alternative']) ?? 'UNKNOWN',
    road_status: (row.road_status as string) ?? null,
    road_coverage: (row.road_coverage as number) ?? null,
    rock_count: Number(row.rock_count ?? 0),
    processing_ms: Number(row.processing_ms ?? 0),
    dropped_since_last: 0,
    reasons: [],
  }
}
