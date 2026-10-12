import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { shadedRecords } from "@/lib/forecastReplay/gap";
import { barDates, fitKey, focusRange, errorSeries, rangeAction } from "@/lib/forecastReplay/paths";
import { labelGapBars, thinLabels } from "@/lib/forecastReplay/labels";
import { returnImplausibleSummary, rowState } from "@/lib/forecastReplay/horizon";
import { selectionForMode } from "@/lib/forecastReplay/view";
import { sourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayPoint, ReplayRecord, ReplaySource } from "@/lib/forecastReplay/types";
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

describe("row states of errored and empty sources", () => {
  const styles = { a: sourceStyle("a", "live") };
  const src = (status: ReplaySource["status"], reason: string | null): ReplaySource => ({
    source_id: "a", family: "f", label: "A", provenance: "live", status, reason, first_day: null, last_day: null, n_stored: 0, n_returned: null,
  });
  const matured = rec("a", "2026-09-02", [pt(14, "2026-09-18", 100, { matured: true })]);
  it("error wins over every record-based state; empty wins only without a point at the horizon", () => {
    const err = src("error", "RuntimeError: forced");
    expect(rowState([], undefined, "a", 14, err)).toBe("error");
    expect(rowState([matured], { eval_count: 3 }, "a", 14, err)).toBe("error");
    expect(rowState([], undefined, "a", 14, src("empty", "no rows"))).toBe("empty");
    expect(rowState([matured], { eval_count: 3 }, "a", 14, src("empty", "x"))).toBe("ok");
    expect(rowState([matured], undefined, "a", 5, src("empty", "x"))).toBe("empty");
    expect(rowState([], undefined, "a", 14, src("ok", null))).toBe("no-point");
  });
  it("the table shows the server's reason, not 'no 14d point'", () => {
    const { rerender } = render(<SourceComparisonTable ids={["a"]} stats={{}} styles={styles} records={[]} horizon={14} sources={[src("error", "RuntimeError: forced")]} />);
    expect(screen.getByTestId("row-state-error").textContent).toBe("error: RuntimeError: forced");
    rerender(<SourceComparisonTable ids={["a"]} stats={{}} styles={styles} records={[]} horizon={14} sources={[src("empty", "no rows in window")]} />);
    expect(screen.getByTestId("row-state-empty").textContent).toBe("no history: no rows in window");
    rerender(<SourceComparisonTable ids={["a"]} stats={{}} styles={styles} records={[]} horizon={14} sources={[src("error", null)]} />);
    expect(screen.getByTestId("row-state-error").textContent).toBe("error: unknown");
    rerender(<SourceComparisonTable ids={["a"]} stats={{}} styles={styles} records={[]} horizon={14} />);
    expect(screen.getByTestId("row-state-no-point").textContent).toBe("no 14d point");
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

describe("fitKey", () => {
  const base = () => fitKey("2026-10-08", ["2026-10-08"], ["a", "b"], 14, "2026-07-10");
  it("is identical for the same view whatever the order or duplicates of the source ids (a refetch keeps pan/zoom)", () => {
    expect(base()).toBe("2026-10-08|2026-10-08|a,b|14|2026-07-10");
    expect(fitKey("2026-10-08", ["2026-10-08"], ["b", "a", "b"], 14, "2026-07-10")).toBe(base());
  });
  it("changes for a new anchor, source set, horizon, week focus or window start (those re-fit)", () => {
    for (const k of [
      fitKey("2026-10-09", ["2026-10-08"], ["a", "b"], 14, "2026-07-10"),
      fitKey("2026-10-08", ["2026-10-08"], ["a"], 14, "2026-07-10"),
      fitKey("2026-10-08", ["2026-10-08"], ["a", "b"], 7, "2026-07-10"),
      fitKey("2026-10-08", ["2026-10-07", "2026-10-08"], ["a", "b"], 14, "2026-07-10"),
      fitKey("2026-10-08", ["2026-10-08"], ["a", "b"], 14, "2026-04-12"),
    ]) expect(k).not.toBe(base());
  });
  it("does not depend on an empty source list being a different identity", () => {
    expect(fitKey("", [""], [], 14, "")).toBe(fitKey("", [""], [], 14, ""));
  });
});

describe("rangeAction", () => {
  it("fits for a new key, and until the fit for this key has been laid out; keeps only once it has", () => {
    expect(rangeAction("k", { key: "", settled: false })).toBe("fit"); // first run after a (re)mount
    expect(rangeAction("k", { key: "k", settled: false })).toBe("fit"); // same-frame second run: reading the range now would clobber the fit (QA4)
    expect(rangeAction("k", { key: "j", settled: true })).toBe("fit"); // new anchor/source/horizon/window
    expect(rangeAction("k", { key: "k", settled: true })).toBe("keep"); // a refetch of the same view
  });
});

describe("barDates", () => {
  it("is the ascending union of actual dates and every plotted path date", () => {
    const fan = rec("fan", "2026-09-02", [pt(1, "2026-09-03", 100), pt(21, "2026-10-02", 100)]);
    expect(barDates(["2026-09-01", "2026-09-02", "2026-09-03"], [fan])).toEqual(["2026-09-01", "2026-09-02", "2026-09-03", "2026-10-02"]);
  });
});

describe("thinLabels", () => {
  it("keeps a label only when it is minBars from the last kept one", () => {
    expect(thinLabels([0, 1, 2, 3, 4, 5, 6].map((idx) => ({ idx })), 3)).toEqual([true, false, false, true, false, false, true]);
  });
  it("the anchor is always kept and pushes its neighbours out", () => {
    expect(thinLabels([{ idx: 0 }, { idx: 1 }, { idx: 2, priority: true }, { idx: 3 }, { idx: 5 }], 3)).toEqual([false, false, true, false, true]);
  });
  it("is independent of input order and keeps the earlier item on one bar", () => {
    expect(thinLabels([{ idx: 5 }, { idx: 1 }, { idx: 5 }, { idx: 2 }], 2)).toEqual([true, true, false, false]);
    expect(thinLabels([], 3)).toEqual([]);
  });
  it("labelGapBars: about 48px of text per label", () => {
    expect([labelGapBars(14.87), labelGapBars(5.29), labelGapBars(100), labelGapBars(0)]).toEqual([4, 10, 1, 1]);
  });
});

describe("selectionForMode", () => {
  const D = ["track:quant_ridge@backtest", "track:macro_only@backtest"];
  it("Compare after Single restores the defaults beside the kept source", () => {
    expect(selectionForMode(false, ["track:quant_ridge@backtest"], D)).toEqual(D);
    expect(selectionForMode(false, ["track:xgboost_macro@backtest"], D)).toEqual(["track:xgboost_macro@backtest", ...D]);
  });
  it("keeps the user's own set and order, adds nothing already there", () => {
    expect(selectionForMode(false, ["b", "a"], ["a", "b"])).toEqual(["b", "a"]);
    expect(selectionForMode(false, ["a", "z", "b"], ["a", "b"])).toEqual(["a", "z", "b"]);
  });
  it("Single keeps only the first source and leaves one source alone", () => {
    expect(selectionForMode(true, ["a", "b", "c"], D)).toEqual(["a"]);
    expect(selectionForMode(true, ["a"], D)).toEqual(["a"]);
    expect(selectionForMode(true, [], D)).toEqual([]);
  });
  it("single -> compare -> single round-trips to the first source", () => {
    const one = selectionForMode(true, D, D);
    const both = selectionForMode(false, one, D);
    expect(both).toEqual(D);
    expect(selectionForMode(true, both, D)).toEqual([D[0]]);
  });
});
