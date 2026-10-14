import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { IndexFactorTimelineChart } from "../IndexFactorTimelineChart";
import { buildWaterfallBars, waterfallStack } from "../AttributionWaterfallChart";
import { widgetPayoffInputs } from "@/lib/tradePlanLegs";

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

describe("R5: macro timeline normalisation of a signed series", () => {
  it("a net-flow series that starts negative and improves reads as a rise, not a fall", () => {
    render(
      <IndexFactorTimelineChart
        showNiftyOverlay={false}
        series={[
          { date: "2026-10-01", fii_net_5d: -100, usd_inr: 80 },
          { date: "2026-10-02", fii_net_5d: -50, usd_inr: 88 },
          { date: "2026-10-03", fii_net_5d: 50, usd_inr: 72 },
        ]}
      />,
    );
    const o = options[options.length - 1] as { series: Array<{ name: string; data: number[] }> };
    const byName = Object.fromEntries(o.series.map((s) => [s.name, s.data]));
    expect(byName["FII net 5d"]).toEqual([0, 50, 150]); // (v - base) / |base|
    expect(byName["USD/INR"]).toEqual([0, 10, -10]); // positive bases are unchanged
  });
});

describe("R5: attribution waterfall stacks on the correct side of zero", () => {
  const parts = (b: Parameters<typeof buildWaterfallBars>[0]) => buildWaterfallBars(b).map(waterfallStack);
  const close = (a: number, b: number) => expect(a).toBeCloseTo(b, 10);

  it("an all-positive path keeps its spacer below the bar", () => {
    const [bench, alloc] = parts({ benchmark_return: 0.1, allocation: 0.02, selection: 0, interaction: 0 });
    expect(bench).toEqual({ placeholder: 0, pos: 0.1, neg: 0 });
    close(alloc.placeholder, 0.1);
    close(alloc.pos, 0.02);
    expect(alloc.neg).toBe(0);
  });

  it("a negative benchmark hangs below zero instead of being drawn upward", () => {
    const [bench] = parts({ benchmark_return: -0.08, allocation: 0, selection: 0, interaction: 0 });
    expect(bench).toEqual({ placeholder: 0, pos: 0, neg: -0.08 });
  });

  it("an effect that crosses zero is split into an above and a below part", () => {
    const [, alloc] = parts({ benchmark_return: -0.03, allocation: 0.05, selection: 0, interaction: 0 });
    close(alloc.pos, 0.02);
    close(alloc.neg, -0.03);
    expect(alloc.placeholder).toBe(0);
  });

  it("a falling effect below zero keeps the spacer at its upper edge", () => {
    const [, alloc] = parts({ benchmark_return: -0.02, allocation: -0.03, selection: 0, interaction: 0 });
    close(alloc.placeholder, -0.02);
    expect(alloc.pos).toBe(0);
    close(alloc.neg, -0.03);
  });
});

describe("R5: payoff IV that nobody reported is flagged as assumed", () => {
  const legs = [
    { id: "1", active: true, segment: "OPTION", side: "BUY", optionType: "CE", strike: 25000, price: 100, lots: 1, lotSize: 75 },
  ] as never;
  const widget = (prediction: Record<string, unknown>) => ({ underlying: "NIFTY", expiry: "2026-10-29", spot: 25000, prediction }) as never;

  it("no atm_iv: regime guess, atmIvAssumed true", () => {
    const r = widgetPayoffInputs(widget({ iv_regime: "high" }), legs);
    expect([r.atmIv, r.atmIvAssumed]).toEqual([22, true]);
  });
  it("a reported atm_iv is used as is", () => {
    const r = widgetPayoffInputs(widget({ atm_iv: 14.5, iv_regime: "high" }), legs);
    expect([r.atmIv, r.atmIvAssumed]).toEqual([14.5, false]);
  });
  it("a zero atm_iv is not a reading", () => {
    expect(widgetPayoffInputs(widget({ atm_iv: 0 }), legs).atmIvAssumed).toBe(true);
  });
});
