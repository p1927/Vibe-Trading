import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { ForecastReplayChart } from "@/components/charts/ForecastReplayChart";
import { api, type ForecastReplayResponse } from "@/lib/api";
import { baseSeriesColors, sourceStyle, trackDisplayLabel } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayRecord, ReplaySource } from "@/lib/forecastReplay/types";
import {
  MAX_REPLAY_SOURCES, overlayChip, overlayRecord, selectionForMode, toggleSelection, uniqueBy,
} from "@/lib/forecastReplay/view";
import { SourcePicker } from "../SourcePicker";
import { useReplayView } from "../useReplayView";

// the chart is mounted for real (width stubbed) so the colours it passes to lightweight-charts can be read back
const added: { color?: string; title?: string }[] = [];
vi.mock("lightweight-charts", () => ({
  LineSeries: "line", LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3 },
  createSeriesMarkers: () => ({ setMarkers: () => undefined }),
}));
vi.mock("@/lib/lightweightChartOptions", () => ({
  createLightweightChart: () => ({
    applyOptions: () => undefined, subscribeClick: () => undefined, remove: () => undefined, removeSeries: () => undefined,
    timeScale: () => ({ getVisibleLogicalRange: () => null, setVisibleLogicalRange: () => undefined, fitContent: () => undefined }),
    addSeries: (_t: unknown, o: { color?: string; title?: string }) => {
      added.push(o);
      return { setData: () => undefined, attachPrimitive: () => undefined };
    },
  }),
  fitLightweightChart: () => undefined, zoomLightweightChartIn: () => undefined, zoomLightweightChartOut: () => undefined,
}));

const rec = (id: string, day: string, h: number, target: string, o: Partial<ReplayRecord> = {}): ReplayRecord => ({
  source_id: id, family: "f", issued_at: `${day}T10:00:00+05:30`, issue_day: day, provenance: "live", spot_at_issue: 22419,
  approximate_path: true, meta: {},
  points: [{ horizon_days: h, target_date: target, p50_level: 22734, p10_level: null, p90_level: null, matured: false,
    actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null }],
  ...o,
});
const src = (id: string, o: Partial<ReplaySource> = {}): ReplaySource => ({
  source_id: id, family: "f", label: id, provenance: "live", status: "ok", reason: null,
  first_day: "2026-10-01", last_day: "2026-10-08", n_records: 3, ...o,
});

describe("overlayRecord target date (QA3 7d report)", () => {
  // headline now records 14d only; the 7d points are from August
  const records = [rec("headline", "2026-08-17", 7, "2026-08-24"), rec("headline", "2026-10-12", 14, "2026-10-30")];
  const live = { asOf: "2026-10-12", spot: 22419, expectedReturnPct: 1.41 };

  it("at 7d it does not borrow the August date: no dated point, a labelled chip instead", () => {
    expect(overlayRecord(live, records, 7)).toBeNull();
    expect(overlayChip(live, 7)).toBe("Live forecast: +1.41% over 7d — no recorded date for this horizon");
  });
  it("at 14d the date comes from the latest issue day's 14d point", () => {
    const o = overlayRecord(live, records, 14);
    expect(o?.points[0].target_date).toBe("2026-10-30");
    expect(o?.points[0].p50_level).toBeCloseTo(22735.1, 1);
  });
  it("a point of the same horizon on an older day is not borrowed either", () => {
    expect(overlayRecord(live, [rec("headline", "2026-10-09", 14, "2026-10-23"), rec("headline", "2026-10-12", 7, "2026-10-19")], 14)).toBeNull();
  });
  it("a borrowed target that is not after the overlay's own day is refused", () => {
    expect(overlayRecord({ ...live, asOf: "2026-10-30" }, records, 14)).toBeNull();
  });
  it("a fan record with a 7d point on the latest day counts; the what-if chip signs negatives", () => {
    const fan = rec("fan", "2026-10-12", 7, "2026-10-19");
    expect(overlayRecord(live, [fan], 7)?.points[0].target_date).toBe("2026-10-19");
    expect(overlayChip({ spot: 100, expectedReturnPct: 1, simulatedReturnPct: -2.5 }, 14)).toBe("Live forecast (what-if): -2.50% over 14d — no recorded date for this horizon");
    expect(overlayChip({ spot: 0, expectedReturnPct: 1 }, 14)).toBeNull();
    expect(overlayRecord(live, [], 7)).toBeNull();
  });
});

describe("labels come from the server, never guessed in the browser", () => {
  it("falls back to the raw id", () => {
    expect(trackDisplayLabel("combiner:equal_weight_2")).toBe("combiner:equal_weight_2");
    expect(trackDisplayLabel("macro_only_no_overlay", null)).toBe("macro_only_no_overlay");
    expect(sourceStyle("track:quant_ridge", "live").label).toBe("quant_ridge");
  });
  it("uses the catalog, bare combiner ids included", () => {
    const cat = { "combiner:equal_weight_2": { label: "Equal weight (2)" }, quant_ridge: { label: "Quant ridge" } };
    expect(trackDisplayLabel("equal_weight_2", cat)).toBe("Equal weight (2)");
    expect(trackDisplayLabel("quant_ridge", cat)).toBe("Quant ridge");
    expect(trackDisplayLabel("quant_ridge", { quant_ridge: {} })).toBe("quant_ridge");
  });
});

describe("source cap", () => {
  const ids = Array.from({ length: 45 }, (_, i) => `s${i}`);
  it("the limit is the server's 40", () => expect(MAX_REPLAY_SOURCES).toBe(40));
  it("toggleSelection refuses the 41st, still removes at the cap and never empties", () => {
    const forty = ids.slice(0, 40);
    expect(toggleSelection(forty, "s40", false)).toBe(forty);
    expect(toggleSelection(ids.slice(0, 39), "s39", false)).toHaveLength(40);
    expect(toggleSelection(forty, "s0", false)).toHaveLength(39);
    expect(toggleSelection(["a"], "a", false)).toEqual(["a"]);
    expect(toggleSelection(forty, "s41", true)).toEqual(["s41"]);
  });
  it("the compare switch cannot push the selection past 40", () => {
    expect(selectionForMode(false, ids.slice(0, 39), ["x", "y", "z"])).toHaveLength(40);
  });
  it("the picker disables the rest and says why at the cap", () => {
    const onToggle = vi.fn();
    const sources = ids.map((i) => src(i));
    const styles = Object.fromEntries(ids.map((i) => [i, sourceStyle(i, "live")]));
    render(<SourcePicker sources={sources} selected={ids.slice(0, 40)} provenance={["live"]} styles={styles} atCap onToggleSource={onToggle} onToggleProvenance={vi.fn()} />);
    expect(screen.getByTestId("source-cap-message").textContent).toContain("At most 40 sources per request");
    const s41 = screen.getByRole("button", { name: /^s40/ }) as HTMLButtonElement;
    expect(s41.disabled).toBe(true);
    expect((screen.getByRole("button", { name: /^s0/ }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /^s0/ }));
    expect(onToggle).toHaveBeenCalledWith("s0");
  });
  it("no message below the cap", () => {
    render(<SourcePicker sources={[src("a")]} selected={["a"]} provenance={["live"]} styles={{ a: sourceStyle("a", "live") }} onToggleSource={vi.fn()} onToggleProvenance={vi.fn()} />);
    expect(screen.queryByTestId("source-cap-message")).toBeNull();
  });
});

describe("duplicate ids", () => {
  it("uniqueBy keeps the first of each key", () => {
    expect(uniqueBy([{ k: "a", v: 1 }, { k: "a", v: 2 }, { k: "b", v: 3 }], (x) => x.k)).toEqual([{ k: "a", v: 1 }, { k: "b", v: 3 }]);
  });
  it("the hook dedupes sources, records and default selection", async () => {
    const bundle = {
      status: "ok", ticker: "NIFTY", stats: {}, actuals: [],
      sources: [src("a"), src("a"), src("b")],
      records: [rec("a", "2026-10-08", 14, "2026-10-22"), rec("a", "2026-10-08", 14, "2026-10-22")],
    } as unknown as ForecastReplayResponse;
    vi.spyOn(api, "getForecastReplay").mockResolvedValue(bundle);
    const { result } = renderHook(() => useReplayView({ ticker: "NIFTY", horizon: 14, defaultSources: ["a", "a"], defaultProvenance: ["live"] }));
    await waitFor(() => expect(result.current.allSources).toHaveLength(2));
    expect(result.current.selected).toEqual(["a"]);
    expect(result.current.records).toHaveLength(1);
    vi.restoreAllMocks();
  });
});

describe("legend swatches share the series colours", () => {
  const actuals = [{ date: "2026-10-09", close: 100 }, { date: "2026-10-12", close: 101 }];
  const props = { records: [], actuals, anchorDay: "2026-10-12", horizon: 14, sourceStyles: {} };
  const swatch = (label: string) => (screen.getByText(label).querySelector("span") as HTMLElement).style.background;
  const setTheme = (dark: boolean, info: string) => {
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.setProperty("--info", info);
    window.localStorage.setItem("qa-theme", dark ? "dark" : "light");
  };

  beforeEach(() => {
    added.length = 0;
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 800 });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    document.documentElement.classList.remove("dark");
  });

  for (const [name, dark, info, hex] of [["light", false, "217 91% 60%", "#3c83f6"], ["dark", true, "199 89% 48%", "#0da2e7"]] as const) {
    it(`${name}: the Nifty close swatch is the colour the close series is drawn with`, () => {
      setTheme(dark, info);
      render(<ForecastReplayChart {...props} />);
      const close = added.find((o) => o.title === "Nifty");
      expect(close?.color).toBe(hex);
      expect(close?.color).toBe(baseSeriesColors().close);
      expect(swatch("Nifty close")).toBe("rgb(" + [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(", ") + ")");
      expect(added.find((o) => o.title === "Actual path")?.color).toBe(baseSeriesColors().actual);
    });
  }
});
