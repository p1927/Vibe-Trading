import { describe, expect, it } from "vitest";
import {
  DEFAULT_CATEGORY_INTERVALS,
  DEFAULT_EQUITY_INTERVALS,
  DEFAULT_HISTORICAL_CONFIG,
  DEFAULT_WS_THROTTLE_HZ,
  HISTORICAL_INTERVAL_OPTIONS,
  HISTORICAL_LOOKBACK_OPTIONS,
  INTERVAL_OPTIONS,
  OFF_KEY,
  UNLIMITED_KEY,
  WS_THROTTLE_OPTIONS,
  clampHistoricalLookbackDays,
  daysToHistoricalLookbackKey,
  historicalIntervalKeyToApi,
  historicalIntervalMaxLookbackDays,
  historicalLookbackKeyToDays,
  hzToWsKey,
  intervalKeyToSeconds,
  secondsToIntervalKey,
  wsKeyToHz,
} from "../intervalOptions";

describe("INTERVAL_OPTIONS", () => {
  it("has 18 entries", () => {
    expect(INTERVAL_OPTIONS).toHaveLength(18);
  });

  it("'off' is value 0 and is the first entry", () => {
    expect(INTERVAL_OPTIONS[0].key).toBe(OFF_KEY);
    expect(INTERVAL_OPTIONS[0].value).toBe(0);
  });

  it("all non-off values are positive and strictly increasing", () => {
    const nonOff = INTERVAL_OPTIONS.slice(1);
    for (let i = 0; i < nonOff.length; i++) {
      expect(nonOff[i].value).toBeGreaterThan(0);
      if (i > 0) {
        expect(nonOff[i].value).toBeGreaterThan(nonOff[i - 1].value);
      }
    }
  });

  it("matches the requested cadence set", () => {
    // Lock the values the user explicitly asked for, so a typo here is
    // caught immediately.
    const expected: Record<string, number> = {
      off: 0,
      "1s": 1, "5s": 5, "10s": 10, "20s": 20, "30s": 30,
      "1m": 60, "2m": 120, "5m": 300, "10m": 600, "15m": 900,
      "20m": 1200, "30m": 1800, "45m": 2700,
      "1h": 3600, "2h": 7200, "5h": 18000, "1d": 86400,
    };
    for (const o of INTERVAL_OPTIONS) {
      expect(expected[o.key]).toBe(o.value);
    }
  });
});

describe("WS_THROTTLE_OPTIONS", () => {
  it("starts with 'unlimited' (hz = 0)", () => {
    expect(WS_THROTTLE_OPTIONS[0].key).toBe(UNLIMITED_KEY);
    expect(WS_THROTTLE_OPTIONS[0].hz).toBe(0);
  });

  it("'unlimited' is first, then hz decreases as the throttle tightens", () => {
    // unlimited=0 is a sentinel for "no throttle", not a rate. Compare
    // the strictly-positive hz values (skip the unlimited sentinel).
    const rates = WS_THROTTLE_OPTIONS.slice(1).map((o) => o.hz);
    for (let i = 1; i < rates.length; i++) {
      expect(rates[i]).toBeLessThan(rates[i - 1]);
    }
  });
});

describe("intervalKeyToSeconds", () => {
  it("'off' → 0", () => {
    expect(intervalKeyToSeconds("off")).toBe(0);
  });

  it("'10s' → 10", () => {
    expect(intervalKeyToSeconds("10s")).toBe(10);
  });

  it("'1d' → 86400", () => {
    expect(intervalKeyToSeconds("1d")).toBe(86400);
  });

  it("null / empty / nonsense → null (caller decides fallback)", () => {
    expect(intervalKeyToSeconds(null)).toBeNull();
    expect(intervalKeyToSeconds(undefined)).toBeNull();
    expect(intervalKeyToSeconds("")).toBeNull();
    expect(intervalKeyToSeconds("nonsense")).toBeNull();
  });
});

describe("secondsToIntervalKey", () => {
  it("0 / null / undefined / negative → 'off'", () => {
    expect(secondsToIntervalKey(0)).toBe("off");
    expect(secondsToIntervalKey(null)).toBe("off");
    expect(secondsToIntervalKey(undefined)).toBe("off");
    expect(secondsToIntervalKey(-1)).toBe("off");
  });

  it("known seconds → matching key", () => {
    expect(secondsToIntervalKey(900)).toBe("15m");
    expect(secondsToIntervalKey(10)).toBe("10s");
    expect(secondsToIntervalKey(86400)).toBe("1d");
  });

  it("unknown seconds → 'off' (we'd rather show the truth than a lie)", () => {
    expect(secondsToIntervalKey(7)).toBe("off");
    expect(secondsToIntervalKey(99999)).toBe("off");
  });
});

describe("wsKeyToHz", () => {
  it("'unlimited' → null", () => {
    expect(wsKeyToHz("unlimited")).toBeNull();
  });

  it("'4hz' → 4", () => {
    expect(wsKeyToHz("4hz")).toBe(4);
  });

  it("nonsense → null", () => {
    expect(wsKeyToHz("nonsense")).toBeNull();
    expect(wsKeyToHz(null)).toBeNull();
  });
});

describe("hzToWsKey", () => {
  it("null / 0 / negative → 'unlimited'", () => {
    expect(hzToWsKey(null)).toBe("unlimited");
    expect(hzToWsKey(0)).toBe("unlimited");
    expect(hzToWsKey(-1)).toBe("unlimited");
  });

  it("known hz → matching key", () => {
    expect(hzToWsKey(4)).toBe("4hz");
    expect(hzToWsKey(50)).toBe("50hz");
  });

  it("unknown hz → 'unlimited' (fallback rather than guess)", () => {
    expect(hzToWsKey(7)).toBe("unlimited");
  });
});

describe("defaults", () => {
  it("DEFAULT_CATEGORY_INTERVALS round-trips through helpers", () => {
    for (const [cat, secs] of Object.entries(DEFAULT_CATEGORY_INTERVALS)) {
      const key = secondsToIntervalKey(secs);
      const back = intervalKeyToSeconds(key);
      expect(back, `round-trip for ${cat}`).toBe(secs);
    }
  });

  it("DEFAULT_WS_THROTTLE_HZ round-trips through helpers", () => {
    const key = hzToWsKey(DEFAULT_WS_THROTTLE_HZ);
    const back = wsKeyToHz(key);
    expect(back).toBe(DEFAULT_WS_THROTTLE_HZ);
  });

  it("DEFAULT_EQUITY_INTERVALS defaults to off", () => {
    expect(DEFAULT_EQUITY_INTERVALS).toEqual({
      equity_option_chain: 0,
      equity_market_depth: 0,
      equity_full_quote: 0,
    });
  });
});

describe("HISTORICAL_INTERVAL_OPTIONS", () => {
  it("has 10 entries", () => {
    expect(HISTORICAL_INTERVAL_OPTIONS).toHaveLength(10);
  });

  it("matches the expected interval set + apiValue mapping", () => {
    const expected: Record<string, { apiValue: string; max: number }> = {
      "1m": { apiValue: "1minute", max: 7 },
      "5m": { apiValue: "5minute", max: 7 },
      "15m": { apiValue: "15minute", max: 7 },
      "30m": { apiValue: "30minute", max: 7 },
      "1h": { apiValue: "60minute", max: 14 },
      "2h": { apiValue: "120minute", max: 14 },
      "4h": { apiValue: "240minute", max: 14 },
      "1d": { apiValue: "1day", max: 365 },
      "1w": { apiValue: "1week", max: 365 },
      "1mo": { apiValue: "1month", max: 365 },
    };
    for (const opt of HISTORICAL_INTERVAL_OPTIONS) {
      const want = expected[opt.key];
      expect(opt.apiValue).toBe(want.apiValue);
      expect(opt.max_lookback_days).toBe(want.max);
    }
  });

  it("per-interval max lookback matches Indmoney API rules", () => {
    // Minutes → 7 days, hours → 14 days, daily+ → 365 days.
    const byApiValue = Object.fromEntries(
      HISTORICAL_INTERVAL_OPTIONS.map((o) => [o.apiValue, o]),
    );
    expect(byApiValue["1minute"].max_lookback_days).toBe(7);
    expect(byApiValue["5minute"].max_lookback_days).toBe(7);
    expect(byApiValue["15minute"].max_lookback_days).toBe(7);
    expect(byApiValue["30minute"].max_lookback_days).toBe(7);
    expect(byApiValue["60minute"].max_lookback_days).toBe(14);
    expect(byApiValue["120minute"].max_lookback_days).toBe(14);
    expect(byApiValue["240minute"].max_lookback_days).toBe(14);
    expect(byApiValue["1day"].max_lookback_days).toBe(365);
    expect(byApiValue["1week"].max_lookback_days).toBe(365);
    expect(byApiValue["1month"].max_lookback_days).toBe(365);
  });
});

describe("HISTORICAL_LOOKBACK_OPTIONS", () => {
  it("has 8 entries", () => {
    expect(HISTORICAL_LOOKBACK_OPTIONS).toHaveLength(8);
  });

  it("day counts match the labels", () => {
    expect(HISTORICAL_LOOKBACK_OPTIONS.find((o) => o.key === "1d")?.days).toBe(1);
    expect(HISTORICAL_LOOKBACK_OPTIONS.find((o) => o.key === "7d")?.days).toBe(7);
    expect(HISTORICAL_LOOKBACK_OPTIONS.find((o) => o.key === "30d")?.days).toBe(30);
    expect(HISTORICAL_LOOKBACK_OPTIONS.find((o) => o.key === "365d")?.days).toBe(365);
  });
});

describe("historical helpers", () => {
  it("historicalIntervalKeyToApi maps user-facing to API value", () => {
    expect(historicalIntervalKeyToApi("1d")).toBe("1day");
    expect(historicalIntervalKeyToApi("1mo")).toBe("1month");
    expect(historicalIntervalKeyToApi("4h")).toBe("240minute");
  });

  it("historicalIntervalMaxLookbackDays returns the per-interval max", () => {
    expect(historicalIntervalMaxLookbackDays("5m")).toBe(7);
    expect(historicalIntervalMaxLookbackDays("2h")).toBe(14);
    expect(historicalIntervalMaxLookbackDays("1w")).toBe(365);
  });

  it("historicalLookbackKeyToDays resolves user-facing keys", () => {
    expect(historicalLookbackKeyToDays("7d")).toBe(7);
    expect(historicalLookbackKeyToDays("30d")).toBe(30);
    expect(historicalLookbackKeyToDays("365d")).toBe(365);
  });

  it("daysToHistoricalLookbackKey round-trips on exact days", () => {
    expect(daysToHistoricalLookbackKey(7)).toBe("7d");
    expect(daysToHistoricalLookbackKey(30)).toBe("30d");
    expect(daysToHistoricalLookbackKey(365)).toBe("365d");
  });

  it("daysToHistoricalLookbackKey falls back to the closest ≤ key", () => {
    // 10 days is between 7d and 14d — should clamp down to 7d, not invent 10d.
    expect(daysToHistoricalLookbackKey(10)).toBe("7d");
    // 100 days is between 90d and 180d.
    expect(daysToHistoricalLookbackKey(100)).toBe("90d");
    // 0 days clamps to the smallest.
    expect(daysToHistoricalLookbackKey(0)).toBe("1d");
  });

  it("clampHistoricalLookbackDays clamps to the interval max", () => {
    expect(clampHistoricalLookbackDays("5m", 365)).toBe(7);
    expect(clampHistoricalLookbackDays("1d", 365)).toBe(365);
    expect(clampHistoricalLookbackDays("2h", 30)).toBe(14);
    // Zero / negative request → at least 1 (or the max if max is 1).
    expect(clampHistoricalLookbackDays("5m", 0)).toBe(1);
    expect(clampHistoricalLookbackDays("5m", -10)).toBe(1);
  });
});

describe("DEFAULT_HISTORICAL_CONFIG", () => {
  it("is a 1d / 30d config", () => {
    expect(DEFAULT_HISTORICAL_CONFIG.interval).toBe("1d");
    expect(DEFAULT_HISTORICAL_CONFIG.lookback_days).toBe("30d");
  });

  it("round-trips through helpers", () => {
    const apiInterval = historicalIntervalKeyToApi(DEFAULT_HISTORICAL_CONFIG.interval);
    const days = historicalLookbackKeyToDays(DEFAULT_HISTORICAL_CONFIG.lookback_days);
    expect(apiInterval).toBe("1day");
    expect(days).toBe(30);
  });
});
