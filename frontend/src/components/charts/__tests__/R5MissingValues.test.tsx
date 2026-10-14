import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { FactorImpactInteractiveChart } from "../../prediction/FactorImpactInteractiveChart";

vi.mock("@/lib/echarts", () => ({
  echarts: { init: vi.fn() },
  initEChart: vi.fn(),
  CHART_GROUP: "quant-charts",
  connectCharts: vi.fn(),
}));
import { initEChart } from "@/lib/echarts";

const options: Array<Record<string, unknown>> = [];
beforeEach(() => {
  options.length = 0;
  vi.mocked(initEChart).mockImplementation((() => ({
    setOption: (o: Record<string, unknown>) => options.push(o),
    resize: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
  })) as unknown as typeof initEChart);
});

describe("R5: a missing Nifty level is a gap, never 0", () => {
  it("shock sweep: null index_level is null, unknown spot draws no baseline line", () => {
    render(
      <FactorImpactInteractiveChart
        spot={0}
        horizonDays={5}
        baselineReturnPct={1}
        simulation={null}
        activeFactor="crude"
        chartMode="shock_sweep"
        sensitivity={[{ factor: "crude", points: [{ factor_delta_pct: -5 }, { factor_delta_pct: 5, index_level: 24000 }] }]}
      />,
    );
    const o = options[options.length - 1] as { series: Array<{ data: unknown[]; markLine: { data: unknown[] } }> };
    expect(o.series[0].data).toEqual([null, 24000]);
    expect(o.series[0].markLine.data).toEqual([]);
  });

  it("horizon path: unknown spot gives null levels, not a line at 0", () => {
    render(
      <FactorImpactInteractiveChart spot={0} horizonDays={3} baselineReturnPct={1} simulation={null} activeFactor="crude" />,
    );
    const o = options[options.length - 1] as { series: Array<{ data: unknown[] }> };
    expect(o.series[0].data.every((v) => v === null)).toBe(true);
  });
});
