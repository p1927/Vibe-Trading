import type { AgentBoardWealthCurvePoint } from "./api";

/**
 * Wealth-curve points whose "Agent actual" value is the previous cumulative carried forward because
 * that closed trade's agent P&L is not reconciled yet (`agent_actual_reconciled === false`). A point
 * from an older API that omits the flag is not counted.
 */
export function carriedForwardActualCount(points: AgentBoardWealthCurvePoint[]): number {
  return points.filter((p) => p.agent_actual_reconciled === false).length;
}
