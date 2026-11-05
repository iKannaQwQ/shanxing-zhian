import type { Passability, RiskLevel } from './types'

/** UNKNOWN 视为最不利：任一方面未知，整体就不能声称可通行。 */
export const PASS_ORDER: Passability[] = ['PASSABLE', 'PARTIALLY_BLOCKED', 'BLOCKED', 'UNKNOWN']

export function worstPassability(a: Passability, b: Passability): Passability {
  return PASS_ORDER.indexOf(b) > PASS_ORDER.indexOf(a) ? b : a
}

const RISK_ORDER: RiskLevel[] = ['SAFE', 'LOW', 'MEDIUM', 'HIGH', 'UNKNOWN']

export function worstRisk(a: RiskLevel, b: RiskLevel): RiskLevel {
  return RISK_ORDER.indexOf(b) > RISK_ORDER.indexOf(a) ? b : a
}
