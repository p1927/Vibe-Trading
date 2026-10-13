import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { IndexTrackScoreboardReport } from "@/lib/api";
import { TrackScoreboardPanel } from "../TrackScoreboardPanel";
import { promotionCell, scoreboardIds } from "@/lib/trackScoreboardUtils";

vi.mock("@/components/charts/TrackScoreboardChart", () => ({ TrackScoreboardChart: () => null }));
vi.mock("@/components/prediction/TrackScoreboardReplaySection", () => ({ TrackScoreboardReplaySection: () => null }));

// A label that no client-side title-casing of the id could produce.
const lab = (id: string) => `SRV<${id}>`;
// The track and combiner sets are the server's (`track_constants.py`): the page never carries its own list. These
// include ids a hand-copied browser list once missed (reconciled_hybrid, garch_vol, forecast_engine_timesfm, inverse_mae_w12).
const liveIds = ["quant_ridge", "macro_only", "reconciled_hybrid", "naive_zero"];
const expIds = ["lightgbm_macro", "garch_vol", "forecast_engine_timesfm", "external_consensus"];
const combIds = ["quant_only", "equal_weight_3", "inverse_mae_w12", "stacked_ridge_meta"];
const ids = [...liveIds, ...expIds];
const catalog = {
  ...Object.fromEntries(liveIds.map((id) => [id, { label: lab(id), implementation: "impl", experimental: false }])),
  ...Object.fromEntries(expIds.map((id) => [id, { label: lab(id), implementation: "impl", experimental: true }])),
  ...Object.fromEntries(combIds.map((c) => [`combiner:${c}`, { label: lab(`combiner:${c}`), implementation: "" }])),
};
const report = {
  status: "ok",
  eval_count: 40,
  track_catalog: catalog,
  tracks: Object.fromEntries(ids.map((id) => [id, { track_id: id, eval_count: 3, mae_pct: 1, direction_hit_rate: 0.5 }])),
  combiners: Object.fromEntries(combIds.map((c) => [c, { track_id: c, eval_count: 3, direction_hit_rate: 0.5 }])),
  live: {
    as_of: "2026-10-12T10:00", spot: 100, active_combiner: "equal_weight_3", headline_source: "quant_ridge",
    forecast_tracks: {
      lightgbm_macro: { expected_return_pct: 1, view: "up" },
      macro_only: { expected_return_pct: 2, available: false },
      forecast_engine_timesfm: { expected_return_pct: 0, available: false, provenance: { reason: "horizon_mismatch" } },
    },
  },
} as unknown as IndexTrackScoreboardReport;

const textOf = (r: IndexTrackScoreboardReport) => render(<TrackScoreboardPanel report={r} />).container.textContent ?? "";

describe("TrackScoreboardPanel labels", () => {
  it("shows the server's label for every track and combiner on the page", () => {
    const t = textOf(report);
    for (const id of ids) expect(t, id).toContain(lab(id));
    for (const c of combIds) expect(t, c).toContain(lab(`combiner:${c}`));
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
    const withMl = { ...report, track_catalog: { ...catalog, lightgbm_macro: { label: "LightGBM macro (ML)", experimental: true } } } as unknown as IndexTrackScoreboardReport;
    const t = textOf(withMl);
    expect(t).toContain("LightGBM macro (ML)");
    expect(t).not.toContain("(ML)(ML)");
    expect(t).not.toContain("(ML) (ML)");
  });
});

describe("scoreboard track lists come from the report", () => {
  it("splits live / experimental / combiners by the catalog and keeps server order", () => {
    expect(scoreboardIds(report)).toEqual({ live: liveIds, experimental: expIds, combiners: combIds });
  });

  it("a track the browser has never heard of is listed (no hand-copied id list)", () => {
    const r = {
      ...report,
      track_catalog: { ...catalog, brand_new_track: { label: lab("brand_new_track"), experimental: false } },
      tracks: { ...report.tracks, brand_new_track: { track_id: "brand_new_track", eval_count: 1 } },
    } as unknown as IndexTrackScoreboardReport;
    expect(scoreboardIds(r).live).toContain("brand_new_track");
    expect(textOf(r)).toContain(lab("brand_new_track"));
  });

  it("without a catalog the tracks table still lists the report's tracks", () => {
    const r = { status: "ok", tracks: { a: {}, b: {} }, combiners: { c: {} } } as unknown as IndexTrackScoreboardReport;
    expect(scoreboardIds(r)).toEqual({ live: ["a", "b"], experimental: [], combiners: ["c"] });
  });
});

describe("combiner promotion", () => {
  // the real cached scoreboard: two combiners pass this run's thresholds, the Bonferroni bootstrap bar rejects both
  const promo = {
    promoted_combiners: [],
    raw_promoted_combiners: ["stacked_ridge_meta"],
    verdicts: { stacked_ridge_meta: { promoted: true }, equal_weight_3: { promoted: false, mae_passes: false }, quant_only: { promoted: false } },
    bootstrap_ci: { stacked_ridge_meta: { passes: false, lower_bound_pp: -5.43, margin_pp: 3 } },
  } as unknown as NonNullable<IndexTrackScoreboardReport["promotion"]>;

  it("a raw pass that failed the bootstrap bar is not 'yes'", () => {
    const c = promotionCell(promo, "stacked_ridge_meta");
    expect(c.label).not.toBe("yes");
    expect(c.tone).toBe("raw");
    expect(c.title).toContain("-5.43pp");
  });

  it("only promoted_combiners say yes", () => {
    expect(promotionCell({ ...promo, promoted_combiners: ["stacked_ridge_meta"] }, "stacked_ridge_meta").label).toBe("yes");
  });

  it("a raw pass with no bootstrap entry says it is not yet stable, still not 'yes'", () => {
    const c = promotionCell({ ...promo, bootstrap_ci: {}, verdicts: { x: { promoted: true } } }, "x");
    expect(c).toMatchObject({ label: "raw pass only", tone: "raw" });
    expect(c.title).toContain("stable");
  });

  it("a bootstrap that failed for too few pairs names that reason", () => {
    const c = promotionCell({ ...promo, bootstrap_ci: { x: { passes: false, reason: "insufficient_pairs" } }, verdicts: { x: { promoted: true } } }, "x");
    expect(c.title).toContain("insufficient pairs");
  });

  it("a failing combiner says which bar: mae or no", () => {
    expect(promotionCell(promo, "equal_weight_3").label).toBe("mae");
    expect(promotionCell(promo, "quant_only").label).toBe("no");
    expect(promotionCell(undefined, "x").label).toBe("no");
  });

  it("the table shows the raw pass as such, next to the report-only footer", () => {
    const r = { ...report, promotion: promo } as unknown as IndexTrackScoreboardReport;
    const { container } = render(<TrackScoreboardPanel report={r} />);
    expect(container.querySelector('[data-testid="promote-stacked_ridge_meta"]')?.textContent).toBe("raw pass only");
    expect(container.textContent).toContain("Report-only");
  });
});

describe("scoreboard states are loud", () => {
  it("says why a live track is unavailable (the server's reason), and the vote is a dash, not 0%", () => {
    const { container } = render(<TrackScoreboardPanel report={report} />);
    expect(container.querySelector('[data-testid="unavailable-forecast_engine_timesfm"]')?.textContent).toContain("horizon mismatch");
    expect(container.textContent).not.toContain("+0.00%");
  });

  it("an empty live snapshot is stated, not an empty grid", () => {
    const r = { ...report, live: { as_of: "2026-10-12T10:00", forecast_tracks: {}, forecast_lab_error: null } } as unknown as IndexTrackScoreboardReport;
    const { container } = render(<TrackScoreboardPanel report={r} />);
    expect(container.querySelector('[data-testid="live-snapshot-empty"]')).not.toBeNull();
    expect(container.textContent).not.toContain("Live track snapshot");
  });

  it("a failed live forecast lab shows its error", () => {
    const r = { ...report, live: { forecast_tracks: {}, forecast_lab_error: "TimesFM artifact unreadable" } } as unknown as IndexTrackScoreboardReport;
    const { container } = render(<TrackScoreboardPanel report={r} />);
    expect(container.querySelector('[data-testid="live-lab-error"]')?.textContent).toContain("TimesFM artifact unreadable");
  });

  it("a stale cached scoreboard says so in the page itself", () => {
    const r = { ...report, needs_refresh: true, as_of: "2026-08-29T13:29:45+00:00" } as unknown as IndexTrackScoreboardReport;
    const { container } = render(<TrackScoreboardPanel report={r} />);
    expect(container.querySelector('[data-testid="scoreboard-stale"]')?.textContent).toContain("2026-08-29");
    expect(render(<TrackScoreboardPanel report={report} />).container.querySelector('[data-testid="scoreboard-stale"]')).toBeNull();
  });
});
