import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { shadedRecords } from "@/lib/forecastReplay/gap";
import { focusRange, errorSeries } from "@/lib/forecastReplay/paths";
import { returnImplausibleSummary, rowState } from "@/lib/forecastReplay/horizon";
import { sourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayPoint, ReplayRecord } from "@/lib/forecastReplay/types";
import { ReturnImplausibleBadge } from "../RecordBadges";
import { SourceComparisonTable } from "../SourceComparisonTable";

const pt = (h: number, target: string, p50: number, o: Partial<ReplayPoint> = {}): ReplayPoint => ({
  horizon_days: h, target_date: target, p50_level: p50, p10_level: null, p90_level: null, matured: false,
  actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null, ...o,
});
const rec = (id: string, day: string, points: ReplayPoint[], meta: ReplayRecord["meta"] = {}): ReplayRecord => ({
  source_id: id, family: "f", issued_at: `${day}T10:00:00+05:30`, issue_day: day, provenance: "live",
  spot_at_issue: 100, approximate_path: false, meta, points,
});

// 24 trading bars 2026-09-01 .. 2026-10-02 (weekends skipped); the anchor is index 1, the 21d fan target index 22
const TRADING = ["09-01", "09-02", "09-03", "09-04", "09-07", "09-08", "09-09", "09-10", "09-11", "09-14", "09-15", "09-16", "09-17", "09-18", "09-21", "09-22", "09-23", "09-24", "09-25", "09-28", "09-29", "09-30", "10-01", "10-02"].map((d) => `2026-${d}`);

describe("focusRange", () => {
  const fan = rec("fan", "2026-09-02", [1, 5, 21].map((h) => pt(h, TRADING[h === 21 ? 22 : 1 + h], 100)));
  it("reaches the 21d fan target (bar 22) plus 2 bars of padding; the old range stopped at bar 17", () => {
    expect(focusRange(TRADING, [fan], "2026-09-02", 16)).toEqual({ from: 0, to: 24 });
  });
  it("counts path dates beyond the last actual close as bars", () => {
    expect(focusRange(TRADING.slice(0, 5), [fan], "2026-09-02", 16)).toEqual({ from: 0, to: 8 });
  });
  it("an implausible record never widens the range, unless it is the only one", () => {
    const wild = rec("w", "2026-09-02", [pt(21, "2026-10-02", 200)], { return_implausible: true });
    const near = rec("n", "2026-09-02", [pt(5, TRADING[6], 101)]);
    expect(focusRange(TRADING, [near, wild], "2026-09-02", 16)).toEqual({ from: 0, to: 8 });
    expect(focusRange(TRADING, [wild], "2026-09-02", 16)).toEqual({ from: 0, to: 25 });
  });
  it("falls back to anchor + fallbackBars with no records, null when the anchor is off the scale", () => {
    expect(focusRange(TRADING, [], "2026-09-03", 16)).toEqual({ from: 0, to: 20 });
    expect(focusRange(TRADING, [], "2025-01-01", 16)).toBeNull();
  });
  it("starts 30 bars before a late anchor", () => {
    const days = Array.from({ length: 80 }, (_, i) => `2026-${String(1 + Math.floor(i / 28)).padStart(2, "0")}-${String((i % 28) + 1).padStart(2, "0")}`);
    expect(focusRange(days, [], days[60], 5)).toEqual({ from: 30, to: 67 });
  });
});

describe("row states", () => {
  const styles = { a: sourceStyle("a", "live") };
  const matured = rec("a", "2026-09-02", [pt(14, "2026-09-18", 100, { matured: true })]);
  const pending = rec("a", "2026-09-02", [pt(14, "2026-09-18", 100)]);
  it("rowState distinguishes no point, pending, no evaluations and ok", () => {
    expect(rowState([matured], undefined, "a", 5)).toBe("no-point");
    expect(rowState([pending], { eval_count: 0 }, "a", 14)).toBe("pending");
    expect(rowState([matured], { eval_count: 0 }, "a", 14)).toBe("no-eval");
    expect(rowState([matured], undefined, "a", 14)).toBe("no-eval");
    expect(rowState([matured], { eval_count: 3 }, "a", 14)).toBe("ok");
  });
  it("the table says why instead of n=0 with dashes", () => {
    const { rerender } = render(<SourceComparisonTable ids={["a"]} stats={{ a: { eval_count: 0 } }} styles={styles} records={[pending]} horizon={14} />);
    expect(screen.getByTestId("row-state-pending").textContent).toBe("no matured 14d point yet");
    rerender(<SourceComparisonTable ids={["a"]} stats={{ a: { eval_count: 0 } }} styles={styles} records={[matured]} horizon={14} />);
    expect(screen.getByTestId("row-state-no-eval").textContent).toBe("no evaluations at 14d");
    rerender(<SourceComparisonTable ids={["a"]} stats={{}} styles={styles} records={[matured]} horizon={5} />);
    expect(screen.getByTestId("row-state-no-point").textContent).toBe("no 5d point");
  });
});

describe("implausible records", () => {
  const wild = rec("w", "2026-09-02", [pt(14, "2026-09-18", 180, { matured: true, error_pct: -40, error_pts: -80 })], { return_implausible: true, return_pct: 80.04 });
  it("summary picks the largest return; badge text and tooltip carry it", () => {
    const other = rec("w", "2026-09-03", [], { return_implausible: true, return_pct: -55 });
    expect(returnImplausibleSummary([rec("w", "2026-09-04", [])])).toBeNull();
    expect(returnImplausibleSummary([wild, other])).toEqual({ count: 2, returnPct: 80.04 });
    render(<ReturnImplausibleBadge records={[wild]} />);
    expect(screen.getByTestId("return-implausible-badge").textContent).toBe("return +80% looks implausible");
    expect(screen.getByTestId("return-implausible-badge").getAttribute("title")).toContain("1 record");
  });
  it("is excluded from the error strip series but stays a record", () => {
    expect(errorSeries([wild], 14)).toEqual([]);
    expect(errorSeries([{ ...wild, meta: {} }], 14)).toHaveLength(1);
  });
  it("is not gap-shaded either", () => {
    const ok = rec("n", "2026-09-02", [pt(5, "2026-09-09", 101, { matured: true })]);
    expect(shadedRecords([wild, ok], "2026-09-02")).toEqual([ok]);
  });
  it("badge without a return_pct still renders, with no number", () => {
    render(<ReturnImplausibleBadge records={[rec("w", "2026-09-02", [], { return_implausible: true })]} />);
    expect(screen.getByTestId("return-implausible-badge").textContent).toBe("return looks implausible");
  });
});
