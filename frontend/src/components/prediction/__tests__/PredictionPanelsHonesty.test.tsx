import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IndexPredictionArtifact } from "@/lib/api";

vi.mock("@/lib/echarts", () => ({
  initEChart: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
  echarts: { init: vi.fn() },
  CHART_GROUP: "g",
  connectCharts: vi.fn(),
}));
vi.mock("@/components/charts/NiftyHistoricalChart", () => ({ NiftyHistoricalChart: () => null }));
vi.mock("@/components/prediction/FactorImpactInteractiveChart", () => ({ FactorImpactInteractiveChart: () => null }));
vi.mock("@/components/prediction/FactorNewsEventPanel", () => ({ FactorNewsEventPanel: () => null }));
vi.mock("@/components/prediction/FactorPedagogyLink", () => ({ FactorPedagogyLink: () => null }));
vi.mock("@/components/charts/NewsScenarioPathChart", () => ({ NewsScenarioPathChart: () => null }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      simulateIndexPrediction: vi.fn(),
      getIndexPlaygroundContext: vi.fn(),
      getForecastEngineEvaluation: vi.fn(),
      getIndexPredictionNewsImpact: vi.fn(),
      getIndexPredictionJobs: vi.fn(),
      getIndexQuantReview: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import { useIndexSimulation } from "@/hooks/useIndexSimulation";
import { FactorImpactWorkbench } from "../FactorImpactWorkbench";
import { BacktestEvaluationPanel } from "../BacktestEvaluationPanel";
import { ForecastEnginePredictedVsActualChart } from "../ForecastEnginePredictedVsActualChart";
import { PredictionMissAnalysisPanel } from "../PredictionMissAnalysisPanel";
import { NewsImpactPanel } from "../NewsImpactPanel";
import { QuantReviewPanel } from "../QuantReviewPanel";
import { PredictionSummary } from "../PredictionSummary";

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getIndexPlaygroundContext.mockResolvedValue({ context: { headlines: [], events: [] } });
  mocked.getIndexPredictionJobs.mockResolvedValue({ news_pipeline: null });
});
afterEach(() => vi.useRealTimers());

describe("useIndexSimulation", () => {
  it("drops the answer to a shock the slider has already moved past", async () => {
    const slow = deferred<unknown>();
    mocked.simulateIndexPrediction
      .mockReturnValueOnce(slow.promise) // shock 5: slow
      .mockResolvedValueOnce({ status: "ok", simulation: { expected_return_pct: 2 } }); // shock 6: fast
    const { result, rerender } = renderHook(({ shock }) => useIndexSimulation({ primary_shock_pct: shock }, { delayMs: 0 }), {
      initialProps: { shock: 5 },
    });
    await waitFor(() => expect(mocked.simulateIndexPrediction).toHaveBeenCalledTimes(1));
    rerender({ shock: 6 });
    await waitFor(() => expect(result.current.simulation?.expected_return_pct).toBe(2));
    await act(async () => { slow.resolve({ status: "ok", simulation: { expected_return_pct: -9 } }); });
    expect(result.current.simulation?.expected_return_pct).toBe(2);
  });

  it("goes idle (result and error cleared) when the request becomes null, and reports an error status", async () => {
    mocked.simulateIndexPrediction.mockResolvedValueOnce({ status: "error", message: "model offline" });
    const { result, rerender } = renderHook(({ req }) => useIndexSimulation(req, { delayMs: 0 }), {
      initialProps: { req: { primary_shock_pct: 3 } as { primary_shock_pct: number } | null },
    });
    await waitFor(() => expect(result.current.error).toBe("model offline"));
    expect(result.current.simulation).toBeNull();
    rerender({ req: null });
    await waitFor(() => expect(result.current.error).toBeNull());
  });
});

describe("unknown is not zero", () => {
  it("the workbench shows no baseline instead of a 0.00% one when the artifact has no headline return", async () => {
    const artifact = {
      ticker: "NIFTY",
      spot: 25000,
      prediction: {},
      factor_explanation: { contributors: [{ factor: "oil_brent", label: "Brent", value: null }] },
    } as unknown as IndexPredictionArtifact;
    render(<FactorImpactWorkbench artifact={artifact} horizonDays={14} />);
    const card = (await screen.findByText(/^Baseline \(14d\)/)).closest("div")!;
    expect(card).not.toHaveTextContent("0.00%");
    expect(card).toHaveTextContent("—");
    // and a factor with a null value does not read "Brent: 0"
    expect(screen.queryByText(/^Brent: /)).toBeNull();
  });

  it("an unresolved backtest row is a dash, not a miss", () => {
    const report = {
      horizon_days: 14,
      eval_count: 2,
      metrics: {},
      daily_evaluations: [
        { date: "2026-10-01", direction_correct: null },
        { date: "2026-10-02", direction_correct: false },
        { date: "2026-10-03", direction_correct: true },
      ],
      as_of: "2026-10-13T08:00:00Z",
    } as unknown as Parameters<typeof BacktestEvaluationPanel>[0]["report"];
    const { container } = render(<BacktestEvaluationPanel report={report} />);
    const hits = [...container.querySelectorAll("tbody tr")].map((tr) => tr.lastElementChild?.textContent);
    expect(hits).toEqual(["✓", "✗", "—"]); // newest first: 10-03 hit, 10-02 miss, 10-01 unresolved
    expect(container.textContent).toContain("Computed 13 Oct 2026, 13:30 IST");
  });

  it("a forecast-engine evaluation without loss figures shows dashes, not 0.0 / 'No'", async () => {
    mocked.getForecastEngineEvaluation.mockResolvedValue({
      available: true,
      horizon_days: 14,
      n_observations: 3,
      observations: [{ target_date: "2026-10-01", cutoff: "2026-09-17", actual: 25000, predicted_point: 25100 }],
    });
    render(<ForecastEnginePredictedVsActualChart recipe="r" />);
    const loss = (await screen.findByText("Mean pinball loss")).parentElement!;
    expect(loss).toHaveTextContent("—");
    expect(loss).not.toHaveTextContent("0.0");
    expect((await screen.findByText("Beats naive?")).parentElement).toHaveTextContent("—");
  });

  it("an unreported momentum coverage does not raise the 0/N red banner", () => {
    const artifact = {
      spot: 22000,
      as_of: "2026-10-12T09:56:54+00:00",
      prediction: { expected_return_pct: 1, range: { low: 1, high: 2 }, momentum_coverage: { total: 50 } },
    } as unknown as IndexPredictionArtifact;
    const { container, rerender } = render(<PredictionSummary artifact={artifact} />);
    expect(container.textContent).not.toContain("Bottom-up momentum missing");
    rerender(
      <PredictionSummary
        artifact={{ ...artifact, prediction: { ...artifact.prediction, momentum_coverage: { total: 50, with_momentum: 5, coverage_pct: 10 } } } as unknown as IndexPredictionArtifact}
      />,
    );
    expect(container.textContent).toContain("Bottom-up momentum missing for 5/50");
  });

  it("news impact counts are dashes until a report has arrived", async () => {
    mocked.getIndexPredictionNewsImpact.mockReturnValue(new Promise(() => {}));
    render(<NewsImpactPanel horizonDays={14} />);
    expect(await screen.findByText(/— verified · — partial/)).toBeInTheDocument();
  });
});

describe("single-source news (D518)", () => {
  it("flags a single-reference story and only that one", async () => {
    const report = {
      status: "ok",
      summary: { approved_count: 2, partial_count: 0 },
      items: [
        { id: "a", title: "Lone story", single_source: true },
        { id: "b", title: "Merged story" },
      ],
    };
    render(<NewsImpactPanel horizonDays={14} externalReport={report as never} />);
    const badges = await screen.findAllByTitle("Backed by one article; counted at half weight");
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent("Single source");
  });
});

describe("one writer, latest request wins", () => {
  it("a push-driven news panel is not overwritten by its own poll", async () => {
    vi.useFakeTimers();
    const pushed = { status: "ok", summary: { approved_count: 7, partial_count: 1 }, items: [] };
    render(<NewsImpactPanel horizonDays={14} externalReport={pushed as never} monitorEnabled pollMs={30_000} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(95_000); });
    expect(mocked.getIndexPredictionNewsImpact).not.toHaveBeenCalled();
    expect(screen.getByText(/7 verified/)).toBeInTheDocument();
  });

  it("the quant review keeps the newest horizon's answer when an older one arrives late", async () => {
    const slow = deferred<unknown>();
    mocked.getIndexQuantReview
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce({ review: { model_prediction_view: "fast-14", as_of: "2026-10-13T00:00:00Z" } });
    const { rerender } = render(<QuantReviewPanel horizonDays={7} />);
    rerender(<QuantReviewPanel horizonDays={14} />);
    expect(await screen.findByText("fast-14")).toBeInTheDocument();
    await act(async () => { slow.resolve({ review: { model_prediction_view: "slow-7" } }); });
    expect(screen.queryByText("slow-7")).toBeNull();
    expect(screen.getByText("fast-14")).toBeInTheDocument();
  });

  it("opening a miss from the backtest table expands that row on an already-mounted panel", () => {
    const report = {
      horizon_days: 14,
      eval_count: 1,
      summary: {},
      misses: [{ prediction_date: "2026-10-01", maturity_date: "2026-10-15", learning_note: "oil gapped" }],
    } as unknown as Parameters<typeof PredictionMissAnalysisPanel>[0]["report"];
    const { rerender } = render(<PredictionMissAnalysisPanel report={report} />);
    expect(screen.queryByText("oil gapped")).toBeNull();
    rerender(<PredictionMissAnalysisPanel report={report} highlightDate="2026-10-01" />);
    expect(screen.getByText("oil gapped")).toBeInTheDocument();
  });

  it("a miss-analysis contribution bar the server did not give is a dash, not 0.00%", () => {
    const report = {
      horizon_days: 14,
      eval_count: 1,
      summary: {},
      misses: [{ prediction_date: "2026-10-01", maturity_date: "2026-10-15" }],
    } as unknown as Parameters<typeof PredictionMissAnalysisPanel>[0]["report"];
    const cf = [{ prediction_date: "2026-10-01", classification: "x", t0_contributions: [{ term: "fii" }, { term: "oil", contribution_pct: 0.5 }] }];
    const { container } = render(
      <PredictionMissAnalysisPanel report={report} counterfactual={cf as never} highlightDate="2026-10-01" />,
    );
    const rows = [...container.querySelectorAll("li")].filter((li) => li.textContent?.startsWith("fii") || li.textContent?.startsWith("oil"));
    expect(rows.find((r) => r.textContent?.startsWith("fii"))?.textContent).toContain("—");
    expect(rows.find((r) => r.textContent?.startsWith("oil"))?.textContent).toContain("+0.50%");
    fireEvent.click(screen.getByText("2026-10-01")); // collapse again: no throw
  });
});
