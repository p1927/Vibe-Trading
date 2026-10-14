import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { MiniPnlOverTimeChart } from "../MiniPnlOverTimeChart";
import { MiniPayoffChart } from "../MiniPayoffChart";
import { getChartTheme } from "@/lib/chart-theme";

vi.mock("@/lib/echarts", () => ({
  echarts: { init: vi.fn() },
  initEChart: vi.fn(),
  CHART_GROUP: "quant-charts",
  connectCharts: vi.fn(),
}));
import { initEChart } from "@/lib/echarts";

type Option = { xAxis: { data: unknown[] }; series: Array<{ data: unknown[]; lineStyle: { color: string } }> };
const options: Option[] = [];
beforeEach(() => {
  options.length = 0;
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.mocked(initEChart).mockImplementation((() => ({
    setOption: (o: Option) => options.push(o),
    resize: vi.fn(),
    dispose: vi.fn(),
  })) as unknown as typeof initEChart);
});

// D410: a series with no readable value is "unknown" (neutral), never a green up-trend, and a missing
// expiry is not day 0.
describe("mini P&L charts: missing is missing, not zero", () => {
  it("an all-missing P&L-over-time series is drawn in the neutral colour, not up/green", () => {
    render(<MiniPnlOverTimeChart samples={[{ pnl: NaN }, { pnl: Number.NaN }] as never} />);
    const o = options[options.length - 1];
    const theme = getChartTheme();
    expect(o.series[0].data).toEqual([null, null]);
    expect(o.series[0].lineStyle.color).toBe(theme.axisColor);
    expect(o.series[0].lineStyle.color).not.toBe(theme.upColor);
  });

  it("a missing days_to_expiry is not plotted as day 0", () => {
    render(<MiniPnlOverTimeChart samples={[{ pnl: 1 }, { pnl: 2, days_to_expiry: 3 }]} />);
    expect(options[options.length - 1].xAxis.data).not.toContain(0);
  });

  it("an all-missing payoff series is neutral too", () => {
    render(<MiniPayoffChart samples={[{ spot: 1, pnl: NaN }, { spot: 2, pnl: NaN }] as never} />);
    const o = options[options.length - 1];
    expect(o.series[0].lineStyle.color).toBe(getChartTheme().axisColor);
  });

  it("a real positive series keeps the up colour, a real negative the down colour", () => {
    render(<MiniPnlOverTimeChart samples={[{ pnl: 1, days_to_expiry: 2 }, { pnl: 5, days_to_expiry: 1 }]} />);
    expect(options[options.length - 1].series[0].lineStyle.color).toBe(getChartTheme().upColor);
    render(<MiniPnlOverTimeChart samples={[{ pnl: 1, days_to_expiry: 2 }, { pnl: -5, days_to_expiry: 1 }]} />);
    expect(options[options.length - 1].series[0].lineStyle.color).toBe(getChartTheme().downColor);
  });
});
