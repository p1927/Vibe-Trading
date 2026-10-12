import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { IndexTrackScoreboardReport } from "@/lib/api";
import { TrackScoreboardPanel } from "../TrackScoreboardPanel";
import { BACKTEST_COMBINER_IDS, CANONICAL_TRACK_IDS, EXPERIMENTAL_TRACK_IDS } from "@/lib/trackScoreboardReplayUtils";

vi.mock("@/components/charts/TrackScoreboardChart", () => ({ TrackScoreboardChart: () => null }));
vi.mock("@/components/prediction/TrackScoreboardReplaySection", () => ({ TrackScoreboardReplaySection: () => null }));

// A label that no client-side title-casing of the id could produce.
const lab = (id: string) => `SRV<${id}>`;
const ids = [...CANONICAL_TRACK_IDS, ...EXPERIMENTAL_TRACK_IDS];
const catalog = Object.fromEntries(
  [...ids, ...BACKTEST_COMBINER_IDS.map((c) => `combiner:${c}`)].map((id) => [id, { label: lab(id), implementation: "impl" }]),
);
const report = {
  status: "ok",
  eval_count: 40,
  track_catalog: catalog,
  tracks: Object.fromEntries(ids.map((id) => [id, { track_id: id, eval_count: 3, mae_pct: 1, direction_hit_rate: 0.5 }])),
  combiners: Object.fromEntries(BACKTEST_COMBINER_IDS.map((c) => [c, { track_id: c, eval_count: 3, direction_hit_rate: 0.5 }])),
  live: {
    as_of: "2026-10-12T10:00", spot: 100, active_combiner: "equal_weight_3", headline_source: "quant_ridge",
    forecast_tracks: { lightgbm_macro: { expected_return_pct: 1, view: "up" }, macro_only: { expected_return_pct: 2, available: false } },
  },
} as unknown as IndexTrackScoreboardReport;

const textOf = (r: IndexTrackScoreboardReport) => render(<TrackScoreboardPanel report={r} />).container.textContent ?? "";

describe("TrackScoreboardPanel labels", () => {
  it("shows the server's label for every track and combiner on the page", () => {
    const t = textOf(report);
    for (const id of ids) expect(t, id).toContain(lab(id));
    for (const c of BACKTEST_COMBINER_IDS) expect(t, c).toContain(lab(`combiner:${c}`));
  });

  it("labels the live snapshot (active combiner, headline source) from the catalog too", () => {
    const t = textOf(report);
    expect(t).toContain(`combiner ${lab("combiner:equal_weight_3")}`);
    expect(t).toContain(`headline ${lab("quant_ridge")}`);
  });

  it("never renders a client-made label from an id", () => {
    const t = textOf(report);
    for (const bad of ["lightgbm macro", "Lightgbm Macro", "macro only", "Macro Only", "quant ridge no overlay", "equal weight 3"]) {
      expect(t).not.toContain(bad);
    }
  });

  it("does not repeat the (ML) suffix the server label already carries", () => {
    const withMl = { ...report, track_catalog: { ...catalog, lightgbm_macro: { label: "LightGBM macro (ML)" } } } as unknown as IndexTrackScoreboardReport;
    const t = textOf(withMl);
    expect(t).toContain("LightGBM macro (ML)");
    expect(t).not.toContain("(ML)(ML)");
    expect(t).not.toContain("(ML) (ML)");
  });
});
