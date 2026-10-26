import { describe, expect, it } from "vitest";
import { computePnlOverTimeSamples } from "../tradePlanLegs";
import type { StrategyLeg } from "../strategyMath";

const leg = {
  id: "l1",
  segment: "OPT",
  side: "BUY",
  lots: 1,
  lotSize: 50,
  expiry: "28APR26",
  strike: 24000,
  optionType: "CE",
  price: 100,
  iv: 15,
  active: true,
} as unknown as StrategyLeg;

describe("computePnlOverTimeSamples charges", () => {
  it("unknown charges give a gross curve, not a net curve with zero charges", () => {
    const s = computePnlOverTimeSamples([leg], 24000, "28APR26");
    expect(s.length).toBeGreaterThan(1);
    expect(s.every((x) => x.net_pnl === undefined)).toBe(true);
  });

  it("known charges (including a real 0) are subtracted", () => {
    const gross = computePnlOverTimeSamples([leg], 24000, "28APR26", 0)[0]!;
    const net = computePnlOverTimeSamples([leg], 24000, "28APR26", 40)[0]!;
    expect(gross.net_pnl).toBe(gross.pnl);
    expect(net.net_pnl).toBeCloseTo((net.pnl ?? 0) - 40, 2);
  });
});
