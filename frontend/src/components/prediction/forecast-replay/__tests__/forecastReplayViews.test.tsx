import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SourcePicker } from "../SourcePicker";
import { DayStepper, stepDay } from "../DayStepper";
import { SourceComparisonTable } from "../SourceComparisonTable";
import { errorStripOption } from "../ErrorStrip";
import { sourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayRecord, ReplaySource } from "@/lib/forecastReplay/types";
import { forHorizon, maturityBuckets, overlayRecord, rollingMean } from "@/lib/forecastReplay/view";
import { externalReplayFor, externalSourceId, externalWindow } from "@/lib/forecastReplay/external";

const src = (id: string, o: Partial<ReplaySource> = {}): ReplaySource => ({
  source_id: id, family: "f", label: id, provenance: "live", status: "ok", reason: null,
  first_day: "2026-10-01", last_day: "2026-10-08", n_records: 3, ...o,
});
const styles = (ids: string[]) => Object.fromEntries(ids.map((i) => [i, sourceStyle(i, "live")]));
const rec = (id: string, day: string, h: number, o: Partial<ReplayRecord["points"][0]> = {}): ReplayRecord => ({
  source_id: id, family: "f", issued_at: `${day}T10:00:00+05:30`, issue_day: day, provenance: "live", spot_at_issue: 100,
  approximate_path: true, meta: {},
  points: [{ horizon_days: h, target_date: "2026-10-20", p50_level: 102, p10_level: null, p90_level: null, matured: false,
    actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null, ...o }],
});

describe("SourcePicker", () => {
  const sources = [src("a"), src("b", { status: "empty", reason: "no evaluations" }), src("c", { status: "error", reason: "boom" }), src("d", { provenance: "backtest" })];
  it("shows status chips and hides sources of inactive provenance", () => {
    render(<SourcePicker sources={sources} selected={["a"]} provenance={["live"]} styles={styles(["a", "b", "c", "d"])} onToggleSource={vi.fn()} onToggleProvenance={vi.fn()} />);
    expect(screen.getByText("ok · 3")).toBeTruthy();
    expect(screen.getByText("no history: no evaluations")).toBeTruthy();
    expect(screen.getByText("error: boom")).toBeTruthy();
    expect(screen.queryByText("d")).toBeNull();
  });
  it("disables non-ok sources and reports clicks on ok ones", () => {
    const onToggle = vi.fn();
    render(<SourcePicker sources={sources} selected={["a"]} provenance={["live"]} styles={styles(["a"])} onToggleSource={onToggle} onToggleProvenance={vi.fn()} />);
    expect((screen.getByTitle("no history: no evaluations") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /^a/ }));
    expect(onToggle).toHaveBeenCalledWith("a");
  });
  it("copes with zero sources and 40 sources", () => {
    const { rerender } = render(<SourcePicker sources={[]} selected={[]} provenance={["live"]} styles={{}} onToggleSource={vi.fn()} onToggleProvenance={vi.fn()} />);
    expect(screen.getByText(/No sources/)).toBeTruthy();
    const many = Array.from({ length: 40 }, (_, i) => src(`s${i}`));
    rerender(<SourcePicker sources={many} selected={["s0"]} provenance={["live"]} styles={styles(many.map((m) => m.source_id))} onToggleSource={vi.fn()} onToggleProvenance={vi.fn()} />);
    expect(screen.getAllByRole("button")).toHaveLength(40);
  });
});

describe("DayStepper", () => {
  const days = ["2026-10-01", "2026-10-05", "2026-10-08"];
  it("steps within bounds and disables at the ends", () => {
    const onChange = vi.fn();
    render(<DayStepper days={days} anchor="2026-10-01" onChange={onChange} />);
    expect((screen.getByText(/Prev forecast/).closest("button") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText(/Next forecast/));
    expect(onChange).toHaveBeenCalledWith("2026-10-05");
  });
  it("arrow keys step; a gap day steps to its neighbours", () => {
    const onChange = vi.fn();
    render(<DayStepper days={days} anchor="2026-10-03" onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole("group"), { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("2026-10-05");
    fireEvent.keyDown(screen.getByRole("group"), { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("2026-10-01");
    expect(screen.getByText(/none recorded on 2026-10-03/)).toBeTruthy();
    expect(stepDay(days, "2026-10-08", 1)).toBe("2026-10-08");
  });
  it("renders with no days and shows buckets and revisions", () => {
    render(<DayStepper days={[]} anchor="" onChange={vi.fn()} revisions={3} buckets={{ matured: 1, partial: 0, pending: 2 }} />);
    expect(screen.getByText(/0 days with a recorded forecast/)).toBeTruthy();
    expect(screen.getByText(/3 runs that day/)).toBeTruthy();
    expect(screen.getByText(/matured 1 \/ partly 0 \/ pending 2/)).toBeTruthy();
  });
});

describe("SourceComparisonTable", () => {
  it("renders server stats, dashes for null/missing/string values", () => {
    render(
      <SourceComparisonTable
        ids={["a", "b"]}
        styles={styles(["a", "b"])}
        stats={{ a: { track_id: "a", eval_count: 8, mae_pct: 3.9638, bias_pct: -3.9, direction_hit_rate: 0.375, in_band_rate: null } }}
        records={[rec("a", "2026-10-01", 14), rec("b", "2026-10-01", 14)]}
        horizon={14}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(rows[1].textContent).toContain("3.96");
    expect(rows[1].textContent).toContain("38%");
    expect(rows[1].textContent).toContain("—");
    expect(rows[2].textContent).toMatch(/b—/);
  });
});

describe("pure helpers", () => {
  it("errorStripOption caps at 4 sources and handles zero matured points", () => {
    const ids = ["a", "b", "c", "d", "e"];
    const recs = ids.map((i) => rec(i, "2026-10-01", 14, { matured: true, error_pts: 1, error_pct: 0.5 }));
    const { option, days } = errorStripOption(recs, 14, ids, styles(ids));
    expect(option.series).toHaveLength(8);
    expect(days).toEqual(["2026-10-01"]);
    expect(errorStripOption([rec("a", "2026-10-01", 14)], 14, ["a"], styles(["a"])).days).toEqual([]);
  });
  it("rollingMean, forHorizon, maturityBuckets", () => {
    expect(rollingMean([1, 2, 3, 4], 2)).toEqual([null, 1.5, 2.5, 3.5]);
    expect(forHorizon([rec("a", "2026-10-01", 7), rec("a", "2026-10-02", 14)], 14)).toHaveLength(1);
    expect(maturityBuckets([rec("a", "2026-10-01", 14), rec("a", "2026-10-01", 14, { matured: true })])).toEqual({ matured: 1, partial: 0, pending: 1 });
  });
  it("overlayRecord borrows the target date and prefers the simulated return", () => {
    const o = overlayRecord({ asOf: "2026-10-12", spot: 200, expectedReturnPct: 1, simulatedReturnPct: 5 }, [rec("headline", "2026-10-09", 14)], 14);
    expect(o?.points[0].target_date).toBe("2026-10-20");
    expect(o?.points[0].p50_level).toBeCloseTo(210);
    expect(o?.meta.simulated).toBe(true);
    expect(overlayRecord({ spot: 200, expectedReturnPct: 1 }, [], 14)).toBeNull();
  });
  it("externalWindow runs from 14 days before the oldest card to today, capped at 395 days", () => {
    expect(externalWindow([{ as_of: "2026-10-01T05:00:00Z" }, { published_at: "2026-09-20" }], "2026-10-12")).toEqual({ start: "2026-09-06", end: "2026-10-12" });
    expect(externalWindow([{ as_of: "2024-01-01" }], "2026-10-12")).toEqual({ start: "2025-09-12", end: "2026-10-12" });
    expect(externalWindow([], "2026-10-12")).toEqual({ start: "2026-07-14", end: "2026-10-12" });
  });
  it("externalReplayFor slices one source and always explains an empty card", () => {
    const bundle = {
      status: "ok", ticker: "NIFTY", stats: {}, actuals: [{ date: "2026-10-01", close: 100 }],
      sources: [src("external:a"), src("external:b", { status: "empty", reason: "no history days" }), src("external:c", { status: "error", reason: "boom" }), src("external:d")],
      records: [rec("external:a", "2026-10-01", 252), rec("external:a", "2026-10-02", 252), rec("external:z", "2026-10-02", 14)],
    };
    const a = externalReplayFor(bundle, "a", null, false);
    expect(a.records.map((r) => r.issue_day)).toEqual(["2026-10-01", "2026-10-02"]);
    expect([a.note, a.actuals.length]).toEqual([null, 1]);
    expect(externalReplayFor(bundle, "b", null, false).note).toBe("no history: no history days");
    expect(externalReplayFor(bundle, "c", null, false).note).toBe("error: boom");
    expect(externalReplayFor(bundle, "d", null, false).note).toBe("no history: no recorded forecast in this window");
    expect(externalReplayFor(bundle, "gone", null, false).note).toBe("no history: source not in the replay registry");
    expect(externalReplayFor(null, "a", "HTTP 422", false).note).toBe("Forecast replay failed: HTTP 422");
    expect(externalReplayFor(null, "a", null, true)).toMatchObject({ records: [], note: null, loading: true });
    expect(externalSourceId("a")).toBe("external:a");
    // two sources with the identical forecast: the server keeps one record and names the other in deduped_with
    const dup = { ...bundle, records: [{ ...rec("external:a", "2026-10-02", 14), meta: { deduped_with: ["external:d"] } }] };
    expect(externalReplayFor(dup, "d", null, false)).toMatchObject({ note: null, records: [{ source_id: "external:a" }] });
    expect(externalReplayFor(dup, "b", null, false).records).toEqual([]);
  });
});
