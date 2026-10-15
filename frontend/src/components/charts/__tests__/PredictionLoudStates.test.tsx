import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Mock } from "vitest";
import { computeTrackEvalStats } from "@/lib/trackScoreboardUtils";
import { OptionsPayoffChart } from "../OptionsPayoffChart";
import { CorrelationMatrix } from "../CorrelationMatrix";
import { ExternalPredictionsComparisonChart } from "../ExternalPredictionsComparisonChart";
import { IndexFactorChart } from "../IndexFactorChart";
import { IndiaStrategyPanel } from "@/components/options/IndiaStrategyPanel";
import { EquationCard } from "@/components/prediction/EquationCard";
import { api } from "@/lib/api";

vi.mock("@/lib/echarts", () => ({
  echarts: { init: vi.fn() },
  initEChart: vi.fn(),
  CHART_GROUP: "quant-charts",
  connectCharts: vi.fn(),
}));

import { initEChart } from "@/lib/echarts";

interface FakeChart {
  setOption: Mock;
  resize: Mock;
  dispose: Mock;
  group: string;
}

const charts: FakeChart[] = [];

function lastOption(): Record<string, any> {
  const chart = charts[charts.length - 1];
  return chart.setOption.mock.calls[chart.setOption.mock.calls.length - 1][0] as Record<string, any>;
}

beforeEach(() => {
  charts.length = 0;
  vi.mocked(initEChart).mockImplementation((() => {
    const chart: FakeChart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), group: "" };
    charts.push(chart);
    return chart;
  }) as unknown as typeof initEChart);
});

afterEach(() => vi.restoreAllMocks());

describe("track scoreboard stats never score a row that has no verdict or no error", () => {
  it("a missing error_pct is not a 0% error and a missing direction_hit is not a miss", () => {
    const stats = computeTrackEvalStats(
      [
        { date: "2026-10-01", track_id: "t", error_pct: 2, direction_hit: true },
        { date: "2026-10-02", track_id: "t", error_pct: -4, direction_hit: false },
        { date: "2026-10-03", track_id: "t" }, // not matured yet
        { date: "2026-10-04", track_id: "other", error_pct: 99, direction_hit: false },
      ],
      "t",
    );
    expect(stats.evalCount).toBe(3);
    expect(stats.maePct).toBe(3); // (2 + 4) / 2, not (2 + 4 + 0) / 3
    expect(stats.hitCount).toBe(1);
    expect(stats.missCount).toBe(1);
    expect(stats.hitRate).toBe(0.5);
  });

  it("no scored rows at all gives null, not 0", () => {
    const stats = computeTrackEvalStats([{ date: "2026-10-03", track_id: "t" }], "t");
    expect(stats.hitRate).toBeNull();
    expect(stats.maePct).toBeNull();
  });
});

describe("IndexFactorChart", () => {
  it("IndexFactorChart draws no 'Current forecast' line it did not compute", () => {
    render(
      <IndexFactorChart
        spot={24000}
        sensitivity={[
          {
            factor: "oil",
            points: [
              { factor_delta_pct: -10, index_level: 24400 },
              { factor_delta_pct: 10, index_level: 23600 },
            ],
          },
        ]}
      />,
    );
    const series = lastOption().series as Array<{ markLine?: unknown; data: number[] }>;
    expect(series[0].markLine).toBeUndefined();
    expect(series[0].data).toEqual([24400, 23600]);
  });
});

describe("OptionsPayoffChart", () => {
  it("leaves a gap where the curve has a spot but no P&L instead of a fake 0", () => {
    render(<OptionsPayoffChart curve={{ spot: [90, 100, 110], pnl: [-50, 20] }} entrySpot={100} breakevens={[]} />);
    const pnl = (lastOption().series as Array<{ data: Array<[number, number]> }>)[0].data;
    expect(pnl).toEqual([
      [90, -50],
      [100, 20],
    ]);
  });
});

describe("ExternalPredictionsComparisonChart", () => {
  it("prices 'Our model' off our own forecast's spot, not a source's stale spot_at_fetch", () => {
    const snapshot = {
      predictions: [
        {
          source_id: "s1",
          fetch_status: "ok",
          spot_at_fetch: 20000, // days old
          target: { mid: 25000 },
          target_date: "2026-10-30",
        },
      ],
      sources: [{ id: "s1", display_name: "Street" }],
      internal_forecast: { spot: 24000, expected_return_pct: 1 },
    } as never;
    render(<ExternalPredictionsComparisonChart snapshot={snapshot} horizonDays={14} />);
    const option = lastOption();
    const bars = (option.series as Array<{ type?: string; data?: Array<{ value: number }> }>).find((s) => s.type === "bar")!;
    expect(bars.data!.map((d) => d.value)).toEqual([25000, 24240]);
    const spotLine = (option.series as Array<{ name?: string; markLine?: { data: Array<{ yAxis: number }> } }>).find(
      (s) => s.name === "Spot",
    )!;
    expect(spotLine.markLine!.data[0].yAxis).toBe(24000);
  });
});

describe("CorrelationMatrix", () => {
  it("leaves an unknown cell out instead of drawing r = 0", () => {
    render(
      <CorrelationMatrix
        labels={["A", "B"]}
        matrix={[
          [1, Number.NaN],
          [0.5, 1],
        ]}
      />,
    );
    const data = (lastOption().series as Array<{ data: Array<{ value: [number, number, number] }> }>)[0].data;
    expect(data.map((d) => d.value)).toEqual([
      [0, 0, 1],
      [0, 1, 0.5],
      [1, 1, 1],
    ]);
  });
});

describe("EquationCard", () => {
  it("shows a missing intercept as a dash, not 0.0000", () => {
    render(
      <EquationCard
        prediction={{ equation: { form: "poly", coefficients: { oil_brent: 0.5 } } } as never}
        spot={24000}
      />,
    );
    expect(screen.getByText(/ΔNifty_14d ≈ — /)).toBeInTheDocument();
  });
});

describe("IndiaStrategyPanel", () => {
  const research = (strategy: Record<string, unknown>) =>
    ({
      ok: true,
      data: {
        underlying: "NIFTY",
        as_of: "2026-10-14T09:31:00+05:30",
        instrument_type: "index",
        expiry: "2026-10-20",
        spot: 24000,
        atm_strike: 24000,
        pcr: 1.1,
        ranked_strategies: [{ name: "Iron condor", ...strategy }],
        recommended: {},
        charges: {},
      },
    }) as never;

  it("shows a missing PoP as a dash and says when the run was computed", async () => {
    vi.spyOn(api, "getIndiaOptionsResearch").mockResolvedValue(research({ pop: null, score: 1 }));
    render(<IndiaStrategyPanel underlying="NIFTY" />);
    screen.getByRole("button", { name: /load/i }).click();
    expect(await screen.findByTestId("india-strategy-as-of")).toHaveTextContent("14 Oct 2026, 09:31 IST");
    const pop = screen.getByText(/PoP/).parentElement!;
    expect(pop).toHaveTextContent("–");
    expect(pop).not.toHaveTextContent("0%");
  });

  it("still renders a real PoP fraction as a percent", async () => {
    vi.spyOn(api, "getIndiaOptionsResearch").mockResolvedValue(research({ pop: 0.62, score: 1 }));
    render(<IndiaStrategyPanel underlying="NIFTY" />);
    screen.getByRole("button", { name: /load/i }).click();
    await screen.findByTestId("india-strategy-as-of");
    expect(screen.getByText(/PoP/).parentElement).toHaveTextContent("62%");
  });
});
