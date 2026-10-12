import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ExternalPredictionRecord } from "@/lib/api";
import type { ExternalReplay } from "@/lib/forecastReplay/external";
import type { ReplayRecord } from "@/lib/forecastReplay/types";
import { ExternalPredictionCard } from "../ExternalPredictionCard";

vi.mock("@/components/charts/ForecastReplayChart", () => ({ ForecastReplayChart: () => null }));

const card = { source_id: "economictimes", fetch_status: "ok", direction: "bullish" } as unknown as ExternalPredictionRecord;
const rec = (meta: ReplayRecord["meta"]): ReplayRecord => ({
  source_id: "external:economictimes", family: "external", issued_at: "2026-07-23T10:00:00+05:30", issue_day: "2026-07-23",
  provenance: "live", spot_at_issue: 22365.8, approximate_path: true, meta,
  points: [{
    horizon_days: 14, target_date: "2026-08-06", p50_level: 23900, p10_level: null, p90_level: null, matured: true,
    actual_level: 24000, error_pts: -100, error_pct: -0.4, in_band: null, direction_hit: true,
  }],
});
const replay = (records: ReplayRecord[]): ExternalReplay => ({ records, actuals: [], note: null, loading: false });
const view = (r: ExternalReplay) => render(<ExternalPredictionCard record={card} horizonDays={14} replay={r} />);

describe("ExternalPredictionCard record badges", () => {
  it("warns when the recorded spot differs from the issue-day close, with the 6.3% gap", () => {
    view(replay([rec({ spot_suspect: true, spot_close_gap_pct: -6.3 })]));
    expect(screen.getByTestId("spot-suspect-badge").textContent).toContain("spot differs from close by 6.3%");
  });

  it("warns on an implausible return", () => {
    view(replay([rec({ return_implausible: true, return_pct: 41 })]));
    expect(screen.getByTestId("return-implausible-badge").textContent).toContain("+41%");
  });

  it("shows both warnings when both flags are set, and none for a clean record or no record", () => {
    const { unmount } = view(replay([rec({ spot_suspect: true, return_implausible: true })]));
    expect(screen.getByTestId("spot-suspect-badge")).toBeTruthy();
    expect(screen.getByTestId("return-implausible-badge")).toBeTruthy();
    unmount();
    view(replay([rec({})]));
    expect(screen.queryByTestId("spot-suspect-badge")).toBeNull();
    expect(screen.queryByTestId("return-implausible-badge")).toBeNull();
    view(replay([]));
    expect(screen.queryAllByTestId("spot-suspect-badge")).toHaveLength(0);
  });

  it("flags only the record the card draws (the newest), not an older flagged one", () => {
    const older = { ...rec({ spot_suspect: true, spot_close_gap_pct: -6.3 }), issue_day: "2026-07-22" };
    view(replay([older, rec({})]));
    expect(screen.queryByTestId("spot-suspect-badge")).toBeNull();
  });
});
