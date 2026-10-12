import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { gapPolygons, maturedUntil, shadedRecords } from "@/lib/forecastReplay/gap";
import { bandCount, defaultHorizon, hasPointAt, horizonOptions, spotSuspectSummary } from "@/lib/forecastReplay/horizon";
import { sourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayPoint, ReplayRecord } from "@/lib/forecastReplay/types";
import { SourceComparisonTable } from "../SourceComparisonTable";
import { RecordBadges, SpotSuspectBadge } from "../RecordBadges";

const pt = (h: number, target: string, p50: number, o: Partial<ReplayPoint> = {}): ReplayPoint => ({
  horizon_days: h, target_date: target, p50_level: p50, p10_level: null, p90_level: null, matured: false,
  actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null, ...o,
});
const record = (id: string, points: ReplayPoint[], meta: ReplayRecord["meta"] = {}): ReplayRecord => ({
  source_id: id, family: "f", issued_at: "2026-10-01T10:00:00+05:30", issue_day: "2026-10-01", provenance: "live",
  spot_at_issue: 100, approximate_path: false, meta, points,
});

const DATES = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-08"];
const xOf = (d: string) => (DATES.includes(d) ? DATES.indexOf(d) : null);
const closes = (v: number[]) => v.map((close, i) => ({ date: DATES[i], close }));

describe("gapPolygons", () => {
  // predicted: 100 -> 102 (x1) -> 98 (x3); actual crosses the predicted path between x1 and x2
  const rec = record("a", [pt(1, "2026-10-02", 102, { matured: true }), pt(3, "2026-10-04", 98, { matured: true }), pt(5, "2026-10-08", 90)]);
  const actuals = closes([100, 101, 101, 103, 120, 130]);

  it("splits at the exact sign change and clips at the last matured point", () => {
    const polys = gapPolygons(rec, actuals, xOf);
    expect(maturedUntil(rec)).toBe("2026-10-04");
    expect(polys).toHaveLength(2);
    expect(polys[0]).toEqual({
      sign: -1,
      pts: [{ x: 0, pred: 100, act: 100 }, { x: 1, pred: 102, act: 101 }, { x: 1.5, pred: 101, act: 101 }],
    });
    expect(polys[1]).toEqual({
      sign: 1,
      pts: [{ x: 1.5, pred: 101, act: 101 }, { x: 2, pred: 100, act: 101 }, { x: 3, pred: 98, act: 103 }],
    });
    // x=4 (2026-10-05) and x=5 are after the last matured date: never shaded
    expect(polys.flatMap((p) => p.pts).every((s) => s.x <= 3)).toBe(true);
  });

  it("draws nothing for a record with no matured point", () => {
    expect(gapPolygons(record("a", [pt(3, "2026-10-04", 98)]), actuals, xOf)).toEqual([]);
  });

  it("one matured and one pending point shades only up to the matured one", () => {
    const r = record("a", [pt(1, "2026-10-02", 99, { matured: true }), pt(5, "2026-10-08", 95)]);
    const polys = gapPolygons(r, actuals, xOf);
    expect(polys).toEqual([{ sign: 1, pts: [{ x: 0, pred: 100, act: 100 }, { x: 1, pred: 99, act: 101 }] }]);
  });

  it("an actual path equal to the forecast yields no area, a path always above yields one polygon", () => {
    const flat = record("a", [pt(3, "2026-10-04", 103, { matured: true })]);
    expect(gapPolygons(flat, closes([100, 101, 102, 103, 0, 0]), xOf)).toEqual([]);
    const above = gapPolygons(flat, closes([100, 105, 105, 105, 0, 0]), xOf);
    expect(above).toHaveLength(1);
    expect(above[0].sign).toBe(1);
    expect(above[0].pts).toEqual([{ x: 0, pred: 100, act: 100 }, { x: 1, pred: 101, act: 105 },{ x: 2, pred: 102, act: 105 }, { x: 3, pred: 103, act: 105 }]);
  });

  it("skips dates that are not on the time scale", () => {
    expect(gapPolygons(rec, actuals, () => null)).toEqual([]);
  });
});

describe("shadedRecords", () => {
  const five = ["a", "b", "c", "d", "e"].map((id) => record(id, [pt(1, "2026-10-02", 1, { matured: true })]));
  it("shades every drawn record up to 4 sources, only the anchor record beyond", () => {
    expect(shadedRecords(five.slice(0, 4), "2026-10-01")).toHaveLength(4);
    expect(shadedRecords(five, "2026-10-01").map((r) => r.source_id)).toEqual(["a"]);
    expect(shadedRecords(five, "2026-10-09")).toEqual([]);
  });
  it("never shades the live overlay", () => {
    expect(shadedRecords([record("x", [], { overlay: true })], "2026-10-01")).toEqual([]);
  });
});

describe("horizon options", () => {
  const fan = record("fan", [1, 3, 5, 10, 21].map((h) => pt(h, "2026-10-20", 100)));
  const head = record("headline", [pt(14, "2026-10-20", 100)]);
  it("is the sorted union over the records", () => {
    expect(horizonOptions([fan, head])).toEqual([1, 3, 5, 10, 14, 21]);
    expect(horizonOptions([])).toEqual([]);
  });
  it("defaults to the page horizon if present, else the nearest (shorter on ties), else null", () => {
    const opts = [1, 3, 5, 10, 14, 21];
    expect(defaultHorizon(opts, 14)).toBe(14);
    expect(defaultHorizon([1, 3, 5, 10, 21], 14)).toBe(10);
    expect(defaultHorizon([1, 3, 5, 10, 21], 16)).toBe(21); // |21-16| = 5 < |16-10| = 6
    expect(defaultHorizon([1, 3, 5, 10, 21], 17)).toBe(21);
    expect(defaultHorizon([10, 20], 15)).toBe(10);
    expect(defaultHorizon([], 14)).toBeNull();
  });
  it("a single option is returned whatever the page horizon", () => {
    expect(defaultHorizon([5], 14)).toBe(5);
  });
  it("hasPointAt tells the fan has no 14d point", () => {
    expect(hasPointAt([fan, head], "fan", 14)).toBe(false);
    expect(hasPointAt([fan, head], "fan", 5)).toBe(true);
    expect(hasPointAt([fan, head], "headline", 14)).toBe(true);
  });
});

describe("bandCount / spotSuspectSummary", () => {
  it("counts matured banded points at the horizon only", () => {
    const r = [
      record("h", [pt(14, "2026-10-20", 1, { matured: true, in_band: true })]),
      record("h", [pt(14, "2026-10-21", 1, { matured: true, in_band: false })]),
      record("h", [pt(14, "2026-10-22", 1, { matured: true, in_band: null })]),
      record("h", [pt(14, "2026-10-23", 1, { in_band: true })]),
      record("h", [pt(5, "2026-10-23", 1, { matured: true, in_band: true })]),
    ];
    expect(bandCount(r, "h", 14)).toEqual({ inside: 1, total: 2 });
    expect(bandCount(r, "h", 3)).toEqual({ inside: 0, total: 0 });
  });
  it("summarises flagged records with the largest gap", () => {
    expect(spotSuspectSummary([record("a", [])])).toBeNull();
    const flagged = [
      record("a", [], { spot_suspect: true, spot_close_gap_pct: -1.5 }),
      record("a", [], { spot_suspect: true, spot_close_gap_pct: 0.4 }),
      record("a", [], { spot_suspect: false, spot_close_gap_pct: 9 }),
    ];
    expect(spotSuspectSummary(flagged)).toEqual({ count: 2, gapPct: -1.5 });
    expect(spotSuspectSummary([record("a", [], { spot_suspect: true })])).toEqual({ count: 1, gapPct: null });
  });
});

describe("table states", () => {
  const styles = Object.fromEntries(["fan", "headline", "none"].map((i) => [i, sourceStyle(i, "live")]));
  const fan = record("fan", [1, 5, 21].map((h) => pt(h, "2026-10-20", 100)));
  const head = record("headline", [pt(14, "2026-10-20", 100, { matured: true, in_band: false })], { spot_suspect: true, spot_close_gap_pct: 2.34 });
  const stats = { headline: { eval_count: 3, mae_pct: 1, bias_pct: 0, direction_hit_rate: 0.5, in_band_rate: 0 } };

  it("shows 'no 14d point' instead of zero-looking cells, the band count, and the spot badge", () => {
    render(<SourceComparisonTable ids={["fan", "headline"]} stats={stats} styles={styles} records={[fan, head]} horizon={14} />);
    expect(screen.getByTestId("row-state-no-point").textContent).toBe("no 14d point");
    expect(screen.getByText("0% (0/1)")).toBeTruthy();
    expect(screen.getByTestId("spot-suspect-badge").textContent).toBe("spot differs from close by 2.3%");
    expect(screen.getByTestId("spot-suspect-badge").getAttribute("title")).toContain("1 record");
  });
  it("shows '—' for the band when the source records none", () => {
    const noBand = record("headline", [pt(14, "2026-10-20", 100, { matured: true })]);
    render(<SourceComparisonTable ids={["headline"]} stats={{ headline: { ...stats.headline, in_band_rate: null } }} styles={styles} records={[noBand]} horizon={14} />);
    expect(screen.queryByTestId("row-state-no-point")).toBeNull();
    expect(screen.queryByTestId("spot-suspect-badge")).toBeNull();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
  it("badge renders nothing for unflagged records", () => {
    const { container } = render(<SpotSuspectBadge records={[fan]} />);
    expect(container.textContent).toBe("");
  });
});
