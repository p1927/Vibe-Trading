import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { IndexFactorChart } from "../IndexFactorChart";

vi.mock("@/lib/echarts", () => ({
  echarts: { init: vi.fn() },
  initEChart: vi.fn(),
  CHART_GROUP: "quant-charts",
  connectCharts: vi.fn(),
}));
import { initEChart } from "@/lib/echarts";

type Opt = { xAxis: { data: string[] }; series: Array<{ data: unknown[]; markLine?: { data: Array<{ yAxis: number }> } }> };
const options: Opt[] = [];
beforeEach(() => {
  options.length = 0;
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.mocked(initEChart).mockImplementation((() => ({
    setOption: (o: Opt) => options.push(o),
    resize: vi.fn(),
    dispose: vi.fn(),
  })) as unknown as typeof initEChart);
});

// A sweep point with no shock size is dropped: it used to be labelled "0%" and picked as the zero-shock
// "Current forecast" baseline.
describe("IndexFactorChart: a missing shock size is not a 0% shock", () => {
  it("factor sweep drops the point and never takes it as the baseline", () => {
    render(
      <IndexFactorChart
        sensitivity={[{ factor: "crude", points: [
          { index_level: 99999 },
          { factor_delta_pct: -5, index_level: 100 },
          { factor_delta_pct: 0, index_level: 200 },
          { factor_delta_pct: 5, index_level: 300 },
        ] }]}
      />,
    );
    const o = options[options.length - 1];
    expect(o.xAxis.data).toEqual(["-5%", "0%", "5%"]);
    expect(o.series[0].data).toEqual([100, 200, 300]);
    expect(o.series[0].markLine?.data[0].yAxis).toBe(200);
  });

  it("with fewer than two placeable points nothing is drawn", () => {
    render(<IndexFactorChart sensitivity={[{ factor: "crude", points: [{ index_level: 1 }, { factor_delta_pct: 1, index_level: 2 }] }]} />);
    expect(options).toHaveLength(0);
  });

  it("event curve drops a point without shock_progress", () => {
    render(
      <IndexFactorChart
        sensitivity={[]}
        eventCurves={[{ event: "e", curve: [{ index_level: 5 }, { shock_progress: 0.5, index_level: 6 }, { shock_progress: 1, index_level: 7 }] }]}
      />,
    );
    const o = options[options.length - 1];
    expect(o.xAxis.data).toEqual(["50%", "100%"]);
  });
});
