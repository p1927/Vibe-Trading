import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import type { IndexPredictionHistoryRow, ExternalPredictionSnapshot } from "@/lib/api";

const echarts = vi.hoisted(() => {
  const setOption = vi.fn();
  return { setOption, initEChart: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })) };
});
vi.mock("@/lib/echarts", () => ({ initEChart: echarts.initEChart, echarts: { init: vi.fn() }, CHART_GROUP: "g", connectCharts: vi.fn() }));
vi.mock("@/hooks/useForecastReplay", () => ({ useForecastReplay: () => ({ bundle: null, loading: false, error: null, refetch: vi.fn() }) }));
vi.mock("@/lib/api", async (orig) => {
  const mod = await orig<typeof import("@/lib/api")>();
  return { ...mod, api: { ...mod.api, getIndexDerivativesHistory: vi.fn() } };
});

import { api } from "@/lib/api";
import { PredictionDecompositionChart } from "@/components/charts/PredictionDecompositionChart";
import { DerivativesFactorsPanel } from "../DerivativesFactorsPanel";
import { ExternalPredictionsPanel } from "../ExternalPredictionsPanel";

describe("a failed request is shown in its section", () => {
  it("derivatives history: the server's 503 text, not 'no history, run backfill'", async () => {
    vi.mocked(api.getIndexDerivativesHistory).mockRejectedValue(new Error("could not reach stock_simulator service at http://127.0.0.1:8902"));
    const onLoadState = vi.fn();
    const { container } = render(<DerivativesFactorsPanel onLoadState={onLoadState} />);
    await waitFor(() => expect(container.querySelector('[data-testid="derivatives-error"]')).not.toBeNull());
    expect(container.textContent).toContain("could not reach stock_simulator");
    expect(container.textContent).not.toContain("Run factor backfill");
    expect(onLoadState).toHaveBeenCalledWith(0, expect.stringContaining("stock_simulator"));
  });

  const snapshot = { sources: [], predictions: [] } as unknown as ExternalPredictionSnapshot;
  const panel = (over: Record<string, unknown>) => (
    <ExternalPredictionsPanel
      snapshot={snapshot} loading={false} refreshing={false} refreshLogs={[]} error={null} horizonDays={14}
      onHorizonChange={vi.fn()} onRefresh={vi.fn()} onDiscover={vi.fn()} onAddSource={vi.fn()} onRemoveSource={vi.fn()}
      {...over}
    />
  );

  it("discovering sources fails loudly instead of becoming an unhandled rejection", async () => {
    const onDiscover = vi.fn().mockRejectedValue(new Error("search backend down"));
    const { getByText, container } = render(panel({ onDiscover }));
    fireEvent.click(getByText("Discover sources"));
    await waitFor(() => expect(container.textContent).toContain("Discover sources failed: search backend down"));
  });
});

describe("decomposition chart gaps", () => {
  it("a part the ledger row lacks is a gap (null), not a 0% bar", () => {
    const rows = [
      { predicted_at: "2026-10-01T10:00:00+00:00", issue_day: "2026-10-01", bottom_up_return_pct: null, macro_delta_pct: 1.2 },
      { predicted_at: "2026-10-02T10:00:00+00:00", issue_day: "2026-10-02", bottom_up_return_pct: 0.4, macro_delta_pct: null },
    ] as unknown as IndexPredictionHistoryRow[];
    render(<PredictionDecompositionChart rows={rows} />);
    const option = echarts.setOption.mock.calls.at(-1)?.[0] as { series: { name: string; data: (number | null)[] }[]; xAxis: { data: string[] } };
    expect(option.series.find((s) => s.name === "Bottom-up")?.data).toEqual([null, 0.4]);
    expect(option.series.find((s) => s.name === "Macro")?.data).toEqual([1.2, null]);
    expect(option.xAxis.data).toEqual(["1 Oct", "2 Oct"]); // the issue day itself, no timezone shift
  });
});
