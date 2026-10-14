import { describe, expect, it } from "vitest";

import type { AgentBoardWealthCurvePoint } from "../api";
import { carriedForwardActualCount } from "../agentBoardWealth";

const point = (over: Partial<AgentBoardWealthCurvePoint> = {}): AgentBoardWealthCurvePoint => ({
  exit_at: "2026-10-14T06:00:00+00:00",
  cumulative_shadow_pnl_inr: 10,
  cumulative_agent_actual_pnl_inr: 0,
  ...over,
});

describe("carriedForwardActualCount", () => {
  it("counts only points flagged unreconciled", () => {
    const points = [
      point({ agent_actual_reconciled: true }),
      point({ agent_actual_reconciled: false }),
      point({ agent_actual_reconciled: false }),
    ];
    expect(carriedForwardActualCount(points)).toBe(2);
  });

  it("does not count points from an API that omits the flag", () => {
    expect(carriedForwardActualCount([point(), point()])).toBe(0);
  });

  it("is zero for an empty curve", () => {
    expect(carriedForwardActualCount([])).toBe(0);
  });
});
