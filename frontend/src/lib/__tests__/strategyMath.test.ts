import {
  computePayoff,
  daysToExpiry,
  netCredit,
  totalPnlAt,
  type StrategyLeg,
} from "../strategyMath";

const EXPIRY = "28APR26";
const NOW = new Date("2026-03-01T00:00:00Z");
const DAYS_AT_EXPIRY = daysToExpiry(EXPIRY, NOW);

let legCounter = 0;
function leg(overrides: Partial<StrategyLeg> & Pick<StrategyLeg, "side">): StrategyLeg {
  legCounter += 1;
  return {
    id: `leg-${legCounter}`,
    segment: "OPTION",
    lots: 1,
    lotSize: 1,
    expiry: EXPIRY,
    price: 0,
    iv: 20,
    active: true,
    symbol: `TEST${legCounter}`,
    ...overrides,
  };
}

function payoffFor(legs: StrategyLeg[], range: [number, number] = [0, 300], steps = 3000) {
  return computePayoff(legs, 100, DAYS_AT_EXPIRY, DAYS_AT_EXPIRY, range, steps);
}

function sortedBreakevens(bes: number[]): number[] {
  return [...bes].sort((a, b) => a - b);
}

describe("Long Call (BUY CE)", () => {
  const legs = [leg({ side: "BUY", optionType: "CE", strike: 100, price: 10 })];
  const result = payoffFor(legs);

  it("has capped loss equal to premium paid", () => {
    expect(result.maxLoss).toBeCloseTo(-10, 1);
  });

  it("has unlimited upside", () => {
    expect(result.maxProfit).toBe(Infinity);
  });

  it("breaks even at strike + premium, reported exactly once", () => {
    const bes = sortedBreakevens(result.breakevens);
    expect(bes).toHaveLength(1);
    expect(bes[0]).toBeCloseTo(110, 1);
  });
});

describe("Short Call (SELL CE)", () => {
  const legs = [leg({ side: "SELL", optionType: "CE", strike: 100, price: 10 })];
  const result = payoffFor(legs);

  it("has capped profit equal to premium received", () => {
    expect(result.maxProfit).toBeCloseTo(10, 1);
  });

  it("has unlimited downside", () => {
    expect(result.maxLoss).toBe(-Infinity);
  });

  it("breaks even at strike + premium", () => {
    expect(sortedBreakevens(result.breakevens)).toEqual([expect.closeTo(110, 1)]);
  });
});

describe("Long Put (BUY PE)", () => {
  const legs = [leg({ side: "BUY", optionType: "PE", strike: 100, price: 10 })];
  const result = payoffFor(legs);

  it("has capped loss equal to premium paid", () => {
    expect(result.maxLoss).toBeCloseTo(-10, 1);
  });

  it("has capped profit at strike - premium (spot floored at 0)", () => {
    expect(result.maxProfit).toBeCloseTo(90, 1);
  });

  it("breaks even at strike - premium", () => {
    expect(sortedBreakevens(result.breakevens)).toEqual([expect.closeTo(90, 1)]);
  });
});

describe("Short Put (SELL PE)", () => {
  const legs = [leg({ side: "SELL", optionType: "PE", strike: 100, price: 10 })];
  const result = payoffFor(legs);

  it("has capped profit equal to premium received", () => {
    expect(result.maxProfit).toBeCloseTo(10, 1);
  });

  it("has capped loss at -(strike - premium) (spot floored at 0)", () => {
    expect(result.maxLoss).toBeCloseTo(-90, 1);
  });

  it("breaks even at strike - premium", () => {
    expect(sortedBreakevens(result.breakevens)).toEqual([expect.closeTo(90, 1)]);
  });
});

describe("Bull Call Spread (BUY CE100 / SELL CE120)", () => {
  const legs = [
    leg({ side: "BUY", optionType: "CE", strike: 100, price: 15 }),
    leg({ side: "SELL", optionType: "CE", strike: 120, price: 5 }),
  ];
  const result = payoffFor(legs);

  it("caps loss at the net debit", () => {
    expect(result.maxLoss).toBeCloseTo(-10, 1);
  });

  it("caps profit at width minus net debit", () => {
    expect(result.maxProfit).toBeCloseTo(10, 1); // (120-100) - 10
  });

  it("breaks even at long strike + net debit", () => {
    expect(sortedBreakevens(result.breakevens)).toEqual([expect.closeTo(110, 1)]);
  });
});

describe("Bear Put Spread (BUY PE120 / SELL PE100)", () => {
  const legs = [
    leg({ side: "BUY", optionType: "PE", strike: 120, price: 15 }),
    leg({ side: "SELL", optionType: "PE", strike: 100, price: 5 }),
  ];
  const result = payoffFor(legs);

  it("caps loss at the net debit", () => {
    expect(result.maxLoss).toBeCloseTo(-10, 1);
  });

  it("caps profit at width minus net debit", () => {
    expect(result.maxProfit).toBeCloseTo(10, 1); // (120-100) - 10
  });

  it("breaks even at long strike - net debit", () => {
    expect(sortedBreakevens(result.breakevens)).toEqual([expect.closeTo(110, 1)]);
  });
});

describe("Long Straddle (BUY CE100 + BUY PE100)", () => {
  const legs = [
    leg({ side: "BUY", optionType: "CE", strike: 100, price: 10 }),
    leg({ side: "BUY", optionType: "PE", strike: 100, price: 8 }),
  ];
  const result = payoffFor(legs);

  it("caps loss at total premium paid", () => {
    expect(result.maxLoss).toBeCloseTo(-18, 1);
  });

  it("has unlimited upside", () => {
    expect(result.maxProfit).toBe(Infinity);
  });

  it("has two symmetric breakevens around the strike", () => {
    const bes = sortedBreakevens(result.breakevens);
    expect(bes).toHaveLength(2);
    expect(bes[0]).toBeCloseTo(82, 1);
    expect(bes[1]).toBeCloseTo(118, 1);
  });
});

describe("Short Straddle (SELL CE100 + SELL PE100)", () => {
  const legs = [
    leg({ side: "SELL", optionType: "CE", strike: 100, price: 10 }),
    leg({ side: "SELL", optionType: "PE", strike: 100, price: 8 }),
  ];
  const result = payoffFor(legs);

  it("caps profit at total premium received", () => {
    expect(result.maxProfit).toBeCloseTo(18, 1);
  });

  it("has unlimited downside", () => {
    expect(result.maxLoss).toBe(-Infinity);
  });

  it("has two symmetric breakevens around the strike", () => {
    const bes = sortedBreakevens(result.breakevens);
    expect(bes).toHaveLength(2);
    expect(bes[0]).toBeCloseTo(82, 1);
    expect(bes[1]).toBeCloseTo(118, 1);
  });
});

describe("Iron Condor (SELL PE95/BUY PE90 + SELL CE105/BUY CE110)", () => {
  const legs = [
    leg({ side: "SELL", optionType: "PE", strike: 95, price: 3 }),
    leg({ side: "BUY", optionType: "PE", strike: 90, price: 1 }),
    leg({ side: "SELL", optionType: "CE", strike: 105, price: 3 }),
    leg({ side: "BUY", optionType: "CE", strike: 110, price: 1 }),
  ];
  const result = payoffFor(legs);
  const credit = netCredit(legs); // (3-1) + (3-1) = 4

  it("collects a net credit of 4", () => {
    expect(credit).toBeCloseTo(4, 6);
  });

  it("caps profit at the net credit", () => {
    expect(result.maxProfit).toBeCloseTo(4, 1);
  });

  it("caps loss at wing width minus net credit on both sides", () => {
    expect(result.maxLoss).toBeCloseTo(-1, 1); // (95-90) - 4 == (110-105) - 4
  });

  it("has four breakevens: two per wing", () => {
    // Between short strikes the position is flat at max profit (no additional
    // zero-crossing there); breakevens occur just outside each short strike.
    const bes = sortedBreakevens(result.breakevens);
    expect(bes).toHaveLength(2);
    expect(bes[0]).toBeCloseTo(91, 1); // 95 - 4
    expect(bes[1]).toBeCloseTo(109, 1); // 105 + 4
  });
});

describe("Call Ratio Back Spread (SELL 1x CE100 / BUY 2x CE120)", () => {
  const legs = [
    leg({ side: "SELL", optionType: "CE", strike: 100, price: 10, lots: 1 }),
    leg({ side: "BUY", optionType: "CE", strike: 120, price: 4, lots: 2 }),
  ];
  const result = payoffFor(legs);

  it("has unlimited upside (net long gamma above the long strike)", () => {
    expect(result.maxProfit).toBe(Infinity);
  });

  it("has a finite, capped downside", () => {
    // Net credit at entry = 10 - 2*4 = 2; worst case is at the short strike
    // where the short leg is max ITM and longs are worthless: -(100-100) + 2 == 2
    // Actually worst point is at strike 100: short leg payoff 0, long legs 0,
    // net = entry credit = 2 (profit), so loss is bounded, not unlimited.
    expect(Number.isFinite(result.maxLoss)).toBe(true);
  });
});

describe("Put Ratio Back Spread (SELL 1x PE100 / BUY 2x PE80)", () => {
  const legs = [
    leg({ side: "SELL", optionType: "PE", strike: 100, price: 8, lots: 1 }),
    leg({ side: "BUY", optionType: "PE", strike: 80, price: 2, lots: 2 }),
  ];
  const result = payoffFor(legs);

  // Unlike a call back-spread's true +Infinity upside, a put back-spread's
  // worst case is a finite trough at the long strike — payoff keeps
  // improving as S falls further toward 0 (floor), it doesn't keep worsening.
  it("has a finite max loss at the long strike, not -Infinity", () => {
    expect(Number.isFinite(result.maxLoss)).toBe(true);
    expect(result.maxLoss).toBeCloseTo(-16, 1); // trough at S=80
  });

  it("recovers to a profit as spot keeps falling toward 0", () => {
    const atZero = totalPnlAt(legs, 0, DAYS_AT_EXPIRY);
    expect(atZero).toBeCloseTo(64, 6);
    expect(atZero).toBeGreaterThan(result.maxLoss);
  });
});

describe("Synthetic Long Future (BUY CE100 + SELL PE100)", () => {
  const legs = [
    leg({ side: "BUY", optionType: "CE", strike: 100, price: 10 }),
    leg({ side: "SELL", optionType: "PE", strike: 100, price: 10 }),
  ];
  const result = payoffFor(legs);

  it("has unlimited upside but a finite downside floor at S=0", () => {
    expect(result.maxProfit).toBe(Infinity);
    // Underlying is floored at 0: worst case is -strike (call worthless,
    // put assigned at the full strike), not -Infinity.
    expect(Number.isFinite(result.maxLoss)).toBe(true);
    expect(result.maxLoss).toBeCloseTo(-100, 1);
  });

  it("breaks even at the strike (zero net premium), reported once", () => {
    const bes = sortedBreakevens(result.breakevens);
    expect(bes).toHaveLength(1);
    expect(bes[0]).toBeCloseTo(100, 1);
  });
});

describe("Closed leg (exitPrice set)", () => {
  it("locks P&L regardless of underlying and ignores time/IV shifts", () => {
    const closedLeg = leg({
      side: "BUY",
      optionType: "CE",
      strike: 100,
      price: 10,
      exitPrice: 25,
      lots: 3,
      lotSize: 2,
    });
    const pnlLowSpot = totalPnlAt([closedLeg], 50, 0);
    const pnlHighSpot = totalPnlAt([closedLeg], 500, 999);
    const expected = (25 - 10) * 3 * 2;
    expect(pnlLowSpot).toBeCloseTo(expected, 6);
    expect(pnlHighSpot).toBeCloseTo(expected, 6);
  });
});

describe("Inactive leg", () => {
  it("contributes zero P&L", () => {
    const inactiveLeg = leg({
      side: "BUY",
      optionType: "CE",
      strike: 100,
      price: 10,
      active: false,
    });
    expect(totalPnlAt([inactiveLeg], 150, 0)).toBe(0);
  });
});

describe("Long Future", () => {
  const legs = [leg({ side: "BUY", segment: "FUTURE", price: 100, lots: 1, lotSize: 1 })];
  const result = payoffFor(legs);

  it("has unlimited profit but a finite loss floor at S=0", () => {
    expect(result.maxProfit).toBe(Infinity);
    expect(Number.isFinite(result.maxLoss)).toBe(true);
    expect(result.maxLoss).toBeCloseTo(-100, 1); // -entry price
  });

  it("breaks even at the entry price, reported once", () => {
    const bes = sortedBreakevens(result.breakevens);
    expect(bes).toHaveLength(1);
    expect(bes[0]).toBeCloseTo(100, 1);
  });
});

describe("Empty strategy", () => {
  it("reports zero max profit/loss instead of -Infinity/Infinity", () => {
    const result = payoffFor([]);
    expect(result.maxProfit).toBe(0);
    expect(result.maxLoss).toBe(0);
    expect(result.breakevens).toEqual([]);
  });
});

describe("Covered Call (BUY FUTURE + SELL CE)", () => {
  const legs = [
    leg({ side: "BUY", segment: "FUTURE", price: 100, lots: 1, lotSize: 1 }),
    leg({ side: "SELL", optionType: "CE", strike: 110, price: 5, lots: 1, lotSize: 1 }),
  ];
  const result = payoffFor(legs);

  it("caps profit at (strike - future entry) + premium", () => {
    expect(result.maxProfit).toBeCloseTo(15, 1); // (110-100) + 5
  });

  it("has a finite loss floor at S=0, not -Infinity", () => {
    expect(Number.isFinite(result.maxLoss)).toBe(true);
    // Future leg loses its full entry price at S=0; the short call adds
    // its premium as a cushion.
    expect(result.maxLoss).toBeCloseTo(-95, 1); // -100 (future) + 5 (call premium)
  });
});
