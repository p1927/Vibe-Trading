import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { api, type ForecastReplayResponse } from "@/lib/api";
import { issueTimeline } from "@/lib/forecastReplay/timeline";
import type { ReplayPoint, ReplayRecord } from "@/lib/forecastReplay/types";
import { ForecastHistorySection } from "../../ForecastHistorySection";

vi.mock("@/lib/echarts", () => ({ initEChart: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }) }));

const pt = (h: number, p50: number, o: Partial<ReplayPoint> = {}): ReplayPoint => ({
  horizon_days: h, target_date: "2026-10-26", p50_level: p50, p10_level: null, p90_level: null, matured: false,
  actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null, ...o,
});
const rec = (day: string, at: string, points: ReplayPoint[], o: Partial<ReplayRecord> = {}): ReplayRecord => ({
  source_id: "headline", family: "headline", issued_at: at, issue_day: day, provenance: "live", spot_at_issue: 20000,
  approximate_path: false, meta: {}, points, ...o,
});

describe("issueTimeline", () => {
  it("one ascending point per issue day: the latest run, expected and realised return from the p50 / actual level", () => {
    const out = issueTimeline(
      [
        rec("2026-10-02", "2026-10-02T10:00:00+05:30", [pt(14, 20400, { p10_level: 20100, p90_level: 20700, matured: true, actual_level: 19000 })]),
        rec("2026-10-01", "2026-10-01T09:00:00+05:30", [pt(14, 20200)]),
        rec("2026-10-02", "2026-10-02T15:00:00+05:30", [pt(14, 20600)]), // later run of the same day wins
      ],
      14,
    );
    expect(out.map((p) => p.issueDay)).toEqual(["2026-10-01", "2026-10-02"]);
    expect(out[0]).toMatchObject({ target: 20200, low: null, high: null, actualReturnPct: null });
    expect(out[0].expectedReturnPct).toBeCloseTo(1, 10);
    expect(out[1].target).toBe(20600);
    expect(out[1].expectedReturnPct).toBeCloseTo(3, 10);

    const matured = issueTimeline([rec("2026-10-02", "t", [pt(14, 20400, { p10_level: 20100, p90_level: 20700, matured: true, actual_level: 19000 })])], 14)[0];
    expect(matured).toMatchObject({ low: 20100, high: 20700 });
    expect(matured.expectedReturnPct).toBeCloseTo(2, 10);
    expect(matured.actualReturnPct).toBeCloseTo(-5, 10);
  });

  it("leaves out records without a point at the horizon, implausible ones and spot-less ones", () => {
    const out = issueTimeline(
      [
        rec("2026-10-01", "a", [pt(7, 20100)]),
        rec("2026-10-02", "b", [pt(14, 90000)], { meta: { return_implausible: true } }),
        rec("2026-10-03", "c", [pt(14, 20100)], { spot_at_issue: 0 }),
        rec("2026-10-04", "d", [pt(14, 20100)]),
      ],
      14,
    );
    expect(out.map((p) => p.issueDay)).toEqual(["2026-10-04"]);
  });

  it("a point that has not matured has no realised return even if a level is attached", () => {
    expect(issueTimeline([rec("2026-10-01", "a", [pt(14, 20100, { actual_level: 19000 })])], 14)[0].actualReturnPct).toBeNull();
  });
});

describe("ForecastHistorySection reads the headline history from the replay", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("requests only the live headline source and counts the replay's days in the hint", async () => {
    const bundle: ForecastReplayResponse = {
      status: "ok", ticker: "NIFTY", actuals: [], stats: {},
      sources: [{ source_id: "headline", family: "headline", label: "Headline", provenance: "live", status: "ok", reason: null, first_day: "2026-10-01", last_day: "2026-10-02", n_stored: 2, n_returned: 2 }],
      records: [rec("2026-10-01", "2026-10-01T10:00:00+05:30", [pt(14, 20200)]), rec("2026-10-02", "2026-10-02T10:00:00+05:30", [pt(14, 20400)])],
    };
    const spy = vi.spyOn(api, "getForecastReplay").mockResolvedValue(bundle);
    render(<ForecastHistorySection daily={[]} intraday={[]} horizonDays={14} />);
    await waitFor(() => expect(screen.getByText(/2 days of 14d-horizon forecasts/)).toBeTruthy());
    expect(spy.mock.calls[0][0]).toMatchObject({ ticker: "NIFTY", sources: ["headline"], horizon: 14, provenance: "live" });
  });
});
