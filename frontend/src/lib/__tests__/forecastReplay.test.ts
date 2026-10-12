import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { api, type ForecastReplayResponse } from "../api";
import { band, dayOutcomes, errorSeries, recordPath, weekGroups } from "../forecastReplay/paths";
import { sourceStyle } from "../forecastReplay/sourceStyle";
import type { ReplayPoint, ReplayRecord } from "../forecastReplay/types";
import { useForecastReplay } from "../../hooks/useForecastReplay";

const pt = (h: number, date: string, p50: number, o: Partial<ReplayPoint> = {}): ReplayPoint => ({
  horizon_days: h, target_date: date, p50_level: p50, p10_level: null, p90_level: null,
  matured: false, actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null, ...o,
});
const rec = (issue_day: string, points: ReplayPoint[], approximate_path = false): ReplayRecord => ({
  source_id: "fan", family: "fan", issued_at: `${issue_day}T10:00:00+05:30`, issue_day,
  provenance: "live", spot_at_issue: 24000, points, approximate_path, meta: {},
});

describe("recordPath / band", () => {
  it("fan of 5 points gives 6 vertices starting at spot", () => {
    const pts = [1, 2, 3, 4, 5].map((h) => pt(h, `2026-10-0${h}`, 24000 + h * 10));
    const { vertices, approximate } = recordPath(rec("2026-09-30", pts));
    expect(vertices).toHaveLength(6);
    expect(vertices[0]).toEqual({ date: "2026-09-30", level: 24000 });
    expect(vertices[5]).toEqual({ date: "2026-10-05", level: 24050 });
    expect(approximate).toBe(false);
  });
  it("single target gives 2 vertices and carries approximate", () => {
    const { vertices, approximate } = recordPath(rec("2026-09-30", [pt(14, "2026-10-16", 24200)], true));
    expect(vertices).toHaveLength(2);
    expect(approximate).toBe(true);
  });
  it("band is null without p10/p90, present with them", () => {
    expect(band(rec("2026-09-30", [pt(1, "2026-10-01", 24010)]))).toBeNull();
    expect(band(rec("2026-09-30", [pt(1, "2026-10-01", 24010, { p10_level: 23900, p90_level: 24100 })]))).toEqual([
      { date: "2026-10-01", p10: 23900, p90: 24100 },
    ]);
  });
  it("dayOutcomes lists every point's error fields", () => {
    const out = dayOutcomes(rec("2026-09-30", [pt(1, "2026-10-01", 24010, { matured: true, error_pts: -10, error_pct: -0.04 })]));
    expect(out).toEqual([
      { horizon_days: 1, target_date: "2026-10-01", matured: true, error_pts: -10, error_pct: -0.04, in_band: null, direction_hit: null },
    ]);
  });
});

describe("weekGroups", () => {
  it("Sunday belongs to the previous Monday's week; Monday starts a new one; holiday week stays one group", () => {
    const days = ["2026-10-05", "2026-10-07", "2026-10-09", "2026-10-11", "2026-10-12"];
    const g = weekGroups(days.map((d) => rec(d, [])));
    expect(g.map((x) => [x.week_start, x.records.map((r) => r.issue_day)])).toEqual([
      ["2026-10-05", ["2026-10-05", "2026-10-07", "2026-10-09", "2026-10-11"]],
      ["2026-10-12", ["2026-10-12"]],
    ]);
  });
});

describe("errorSeries", () => {
  it("skips unmatured points and other horizons, keeps order", () => {
    const m = { matured: true, error_pts: 5, error_pct: 0.02, in_band: true };
    const s = errorSeries(
      [
        rec("2026-10-01", [pt(14, "2026-10-15", 1, m)]),
        rec("2026-10-02", [pt(14, "2026-10-16", 1)]),
        rec("2026-10-03", [pt(7, "2026-10-10", 1, m)]),
        rec("2026-10-04", [pt(14, "2026-10-18", 1, { ...m, error_pts: -8, error_pct: -0.03, in_band: false })]),
      ],
      14,
    );
    expect(s).toEqual([
      { issue_day: "2026-10-01", error_pct: 0.02, error_pts: 5, in_band: true },
      { issue_day: "2026-10-04", error_pct: -0.03, error_pts: -8, in_band: false },
    ]);
  });
});

describe("sourceStyle", () => {
  it("is deterministic with distinct dash per provenance", () => {
    expect(sourceStyle("track:quant_ridge", "live")).toEqual(sourceStyle("track:quant_ridge", "live"));
    expect(sourceStyle("track:quant_ridge", "live").color).toBe("#3b82f6");
    const dashes = (["live", "replay", "backtest"] as const).map((p) => JSON.stringify(sourceStyle("fan", p).dash));
    expect(new Set(dashes).size).toBe(3);
    expect(sourceStyle("fan", "backtest").hatch).toBe(true);
    expect(sourceStyle("fan", "live").hatch).toBe(false);
  });
  it("hash fallback is stable and differs between unknown ids; server label wins", () => {
    const a = sourceStyle("external:brand_new", "live", "Brand New");
    expect(a.color).toBe(sourceStyle("external:brand_new", "live").color);
    expect(a.color).toMatch(/^hsl\(/);
    expect(a.color).not.toBe(sourceStyle("external:other_one", "live").color);
    expect(a.label).toBe("Brand New");
  });
});

describe("useForecastReplay", () => {
  const bundle = (ticker: string) => ({ status: "ok", ticker, actuals: [], sources: [], records: [], stats: {} }) as ForecastReplayResponse;
  const base = { ticker: "NIFTY", sources: ["fan"], start: "2026-09-01", end: "2026-10-01", horizon: 14, provenance: "live" as const };

  it("aborts the stale request on param change and keeps the previous bundle while loading", async () => {
    const signals: AbortSignal[] = [];
    const spy = vi.spyOn(api, "getForecastReplay").mockImplementation((p, signal) => {
      signals.push(signal!);
      return p.horizon === 14 ? Promise.resolve(bundle("A")) : new Promise(() => {});
    });
    const { result, rerender } = renderHook((p) => useForecastReplay(p), { initialProps: base });
    await waitFor(() => expect(result.current.bundle?.ticker).toBe("A"));
    rerender({ ...base, horizon: 7 });
    expect(signals[0].aborted).toBe(true);
    expect(result.current.bundle?.ticker).toBe("A");
    expect(result.current.loading).toBe(true);
    spy.mockRestore();
  });

  it("surfaces the error text verbatim", async () => {
    const spy = vi.spyOn(api, "getForecastReplay").mockRejectedValue(new Error("adapter exploded: HTTP 500"));
    const { result } = renderHook(() => useForecastReplay(base));
    await waitFor(() => expect(result.current.error).toBe("adapter exploded: HTTP 500"));
    expect(result.current.loading).toBe(false);
    spy.mockRestore();
  });
});
