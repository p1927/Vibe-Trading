import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { PredictionControls } from "@/components/prediction/PredictionControls";
import { PredictionSummary } from "@/components/prediction/PredictionSummary";
import { FactorImpactWorkbench } from "@/components/prediction/FactorImpactWorkbench";
import { FactorCompositionTable } from "@/components/prediction/FactorCompositionTable";
import { EquationCard } from "@/components/prediction/EquationCard";
import { CausalFactorExplorer } from "@/components/prediction/CausalFactorExplorer";
import { BacktestEvaluationPanel } from "@/components/prediction/BacktestEvaluationPanel";
import { PredictionMissAnalysisPanel } from "@/components/prediction/PredictionMissAnalysisPanel";
import { IndexFactorLedgerPanel } from "@/components/prediction/IndexFactorLedgerPanel";
import { ForecastHistorySection } from "@/components/prediction/ForecastHistorySection";
import { IndexFactorTimelineChart } from "@/components/charts/IndexFactorTimelineChart";
import { NiftyForecastReplayChart } from "@/components/charts/NiftyForecastReplayChart";
import { NiftyMarketContextChart } from "@/components/charts/NiftyMarketContextChart";
import { PredictionDecompositionChart } from "@/components/charts/PredictionDecompositionChart";
import { ScenarioTiles } from "@/components/prediction/ScenarioTiles";
import { ConstituentDrivers } from "@/components/prediction/ConstituentDrivers";
import { SectorBreadthPanel } from "@/components/prediction/SectorBreadthPanel";
import { PredictionLearningPanel } from "@/components/prediction/PredictionLearningPanel";
import { PredictionPipelinePanel } from "@/components/prediction/PredictionPipelinePanel";
import { DerivativesFactorsPanel } from "@/components/prediction/DerivativesFactorsPanel";
import { PredictionScheduledJobsPanel } from "@/components/prediction/PredictionScheduledJobsPanel";
import { DataCapturePanel } from "@/components/prediction/DataCapturePanel";
import { NewsTriggerPanel } from "@/components/prediction/NewsTriggerPanel";
import { NewsImpactPanel } from "@/components/prediction/NewsImpactPanel";
import { PredictionVerificationPanel } from "@/components/prediction/PredictionVerificationPanel";
import { PredictionSectionHeader } from "@/components/prediction/PredictionSectionHeader";
import { TechnicalContextStrip } from "@/components/prediction/TechnicalContextStrip";
import { QuantReviewPanel } from "@/components/prediction/QuantReviewPanel";
import { useIndexPrediction } from "@/hooks/useIndexPrediction";
import { useIndexPredictionLive } from "@/hooks/useIndexPredictionLive";
import {
  api,
  type IndexBacktestReport,
  type IndexCounterfactualRow,
  type IndexMissAnalysisReport,
  type IndexFactorHistoryPoint,
  type IndexPredictionHistoryMeta,
  type IndexPredictionHistoryRow,
  type IndexSimulationResult,
} from "@/lib/api";

import { MACRO_DRIFT_FACTORS, niftyCloseSeries, pivotFactorHistoryWide } from "@/lib/factorHistoryUtils";
import { mergePriceSeries } from "@/lib/forecastReplayUtils";

const POLL_STORAGE_KEY = "vibe-prediction-poll-ms";
const DEFAULT_POLL_MS = 300_000;

export function Prediction() {
  const [horizonDays, setHorizonDays] = useState(14);
  const [pollMs, setPollMs] = useState(() => {
    try {
      const raw = localStorage.getItem(POLL_STORAGE_KEY);
      return raw != null ? Number(raw) : DEFAULT_POLL_MS;
    } catch {
      return DEFAULT_POLL_MS;
    }
  });
  const [dailyHistory, setDailyHistory] = useState<IndexPredictionHistoryRow[]>([]);
  const [intradayHistory, setIntradayHistory] = useState<IndexPredictionHistoryRow[]>([]);
  const [historyMeta, setHistoryMeta] = useState<IndexPredictionHistoryMeta | undefined>();
  const [factorHistory, setFactorHistory] = useState<IndexFactorHistoryPoint[]>([]);
  const [factorCoverageNotes, setFactorCoverageNotes] = useState<string[]>([]);
  const [backtest, setBacktest] = useState<IndexBacktestReport | null>(null);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [missAnalysis, setMissAnalysis] = useState<IndexMissAnalysisReport | null>(null);
  const [counterfactual, setCounterfactual] = useState<IndexCounterfactualRow[] | null>(null);
  const [missAnalysisLoading, setMissAnalysisLoading] = useState(false);
  const [missHighlightDate, setMissHighlightDate] = useState<string | null>(null);
  const [simulation, setSimulation] = useState<IndexSimulationResult | null>(null);
  const [refreshConstituents, setRefreshConstituents] = useState(false);
  const [backtestError, setBacktestError] = useState<string | null>(null);
  const [missAnalysisError, setMissAnalysisError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [factorHistoryError, setFactorHistoryError] = useState<string | null>(null);
  const [derivativesSeriesCount, setDerivativesSeriesCount] = useState(0);
  const [derivativesError, setDerivativesError] = useState<string | null>(null);
  const prevReturnRef = useRef<number | null>(null);
  const [flashReturn, setFlashReturn] = useState(false);

  const {
    artifact,
    loading,
    running,
    error,
    runAnalysis,
    applyArtifact,
    pipelineLogs,
    pipelinePanelOpen,
    factorCatalog,
    catalogLoading,
    setPipelinePanelOpen,
  } = useIndexPrediction("NIFTY", horizonDays);

  const loadHistory = useCallback(async () => {
    setHistoryError(null);
    try {
      const res = await api.getIndexPredictionHistory("NIFTY", 90, horizonDays, true);
      setDailyHistory(res.daily ?? res.rows ?? []);
      setIntradayHistory(res.intraday ?? []);
      setHistoryMeta(res.meta);
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : "History load failed");
    }
  }, [horizonDays]);

  const loadFactorHistory = useCallback(async () => {
    setFactorHistoryError(null);
    try {
      const res = await api.getIndexFactorHistory("NIFTY", 365, [
        ...MACRO_DRIFT_FACTORS,
        "dii_net_5d",
        "nifty_pcr",
      ]);
      if (res.series) setFactorHistory(res.series);
      setFactorCoverageNotes(res.coverage_notes ?? []);
    } catch (e) {
      setFactorHistoryError(e instanceof Error ? e.message : "Factor history load failed");
    }
  }, []);

  const loadBacktest = useCallback(
    async (refresh = false) => {
      setBacktestLoading(true);
      setBacktestError(null);
      try {
        const res = await api.getIndexPredictionBacktest("NIFTY", refresh, 365, horizonDays);
        if (res.report) setBacktest(res.report);
        else if (res.status !== "ok") setBacktestError(res.message || "Backtest unavailable");
      } catch (e) {
        setBacktestError(e instanceof Error ? e.message : "Backtest request failed");
      } finally {
        setBacktestLoading(false);
      }
    },
    [horizonDays],
  );

  useEffect(() => {
    void loadBacktest(false);
  }, [loadBacktest]);

  const loadMissAnalysis = useCallback(
    async (refresh = false) => {
      setMissAnalysisLoading(true);
      setMissAnalysisError(null);
      try {
        const [res, cfRes] = await Promise.all([
          api.getIndexPredictionMissAnalysis("NIFTY", refresh, 365, horizonDays),
          api.getIndexPredictionCounterfactual("NIFTY", refresh, 365, horizonDays),
        ]);
        if (res.report) setMissAnalysis(res.report);
        else if (res.status !== "ok") setMissAnalysisError(res.message || "Miss analysis unavailable");
        setCounterfactual(cfRes.report?.misses ?? cfRes.report?.rows ?? null);
      } catch (e) {
        setMissAnalysisError(e instanceof Error ? e.message : "Miss analysis request failed");
      } finally {
        setMissAnalysisLoading(false);
      }
    },
    [horizonDays],
  );

  useEffect(() => {
    void loadMissAnalysis(false);
  }, [loadMissAnalysis]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory, artifact?.as_of]);

  useEffect(() => {
    void loadFactorHistory();
  }, [loadFactorHistory, artifact?.as_of]);

  const handleLiveUpdate = useCallback(
    (next: typeof artifact) => {
      if (!next) return;
      applyArtifact(next);
      const ret = next.prediction?.expected_return_pct;
      if (ret != null && prevReturnRef.current != null && ret !== prevReturnRef.current) {
        setFlashReturn(true);
        window.setTimeout(() => setFlashReturn(false), 1200);
      }
      if (ret != null) prevReturnRef.current = ret;
      void loadHistory();
    },
    [applyArtifact, loadHistory],
  );

  const { lastReason, countdownSec, materialNewsCount } = useIndexPredictionLive({
    ticker: "NIFTY",
    horizonDays,
    pollMs,
    enabled: pollMs > 0,
    onUpdate: handleLiveUpdate,
  });

  useEffect(() => {
    try {
      localStorage.setItem(POLL_STORAGE_KEY, String(pollMs));
    } catch {
      /* ignore */
    }
  }, [pollMs]);

  const handleRun = () => {
    setSimulation(null);
    void runAnalysis(horizonDays, refreshConstituents).then(() => loadHistory());
  };

  const handleDerivativesLoadState = useCallback((count: number, err: string | null) => {
    setDerivativesSeriesCount(count);
    setDerivativesError(err);
  }, []);

  const regimeLabel = (() => {
    const r = artifact?.regime;
    if (!r) return "";
    const raw = r.label ?? r.regime ?? "";
    return typeof raw === "string" ? raw : "";
  })();

  const forecastTarget = (() => {
    const spot = artifact?.spot;
    const ret = simulation?.expected_return_pct ?? artifact?.prediction?.expected_return_pct;
    if (spot == null || ret == null) return null;
    return spot * (1 + ret / 100);
  })();

  const niftyPriceSeries = useMemo(() => {
    const wide = pivotFactorHistoryWide(factorHistory);
    const fromFactors = niftyCloseSeries(wide)
      .filter((r) => r.close != null)
      .map((r) => ({ date: r.date, close: r.close as number }));
    const fromBacktest = (backtest?.nifty_series ?? []).map((p) => ({
      date: p.date,
      close: p.close,
    }));
    const merged = mergePriceSeries([fromBacktest, fromFactors]);
    if (!merged.length && artifact?.spot != null && artifact.as_of) {
      return [{ date: String(artifact.as_of).slice(0, 10), close: artifact.spot }];
    }
    return merged;
  }, [factorHistory, backtest?.nifty_series, artifact?.spot, artifact?.as_of]);

  return (
    <div className="mx-auto flex w-full max-w-[90rem] flex-col gap-4 p-4 pb-10 lg:flex-row lg:items-start lg:gap-5 md:p-6">
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <PredictionControls
          horizonDays={horizonDays}
          onHorizonChange={setHorizonDays}
          pollMs={pollMs}
          onPollChange={setPollMs}
          refreshConstituents={refreshConstituents}
          onRefreshConstituentsChange={setRefreshConstituents}
          onRun={handleRun}
          running={running}
          lastUpdated={artifact?.as_of}
          spot={artifact?.spot}
          regime={regimeLabel}
          pipelinePanelOpen={pipelinePanelOpen}
          onTogglePipelinePanel={() => setPipelinePanelOpen(!pipelinePanelOpen)}
        />

        <NewsTriggerPanel
          materialNewsCount={materialNewsCount}
          lastReason={lastReason}
          countdownSec={countdownSec}
          pollMs={pollMs}
          monitorEnabled={pollMs > 0}
        />

        <PredictionScheduledJobsPanel />
        <DataCapturePanel />

        {error ? (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            {error}
          </div>
        ) : null}

        {artifact?.stage_errors?.length ? (
          <ul className="space-y-1 text-[11px] text-red-700 dark:text-red-400">
            {artifact.stage_errors.map((e) => (
              <li key={e}>• Pipeline: {e}</li>
            ))}
          </ul>
        ) : null}

        {artifact?.data_warnings?.length ? (
          <ul className="space-y-1 text-[11px] text-amber-700 dark:text-amber-400">
            {artifact.data_warnings.map((w) => (
              <li key={w}>• {w}</li>
            ))}
          </ul>
        ) : null}

        {loading && !artifact ? (
          <div className="flex h-40 items-center justify-center text-muted-foreground">Loading prediction…</div>
        ) : null}

        {artifact ? (
          <>
            <PredictionVerificationPanel
              artifact={artifact}
              external={{
                horizonDays,
                factorHistoryCount: factorHistory.length,
                factorHistoryError,
                dailyHistoryCount: dailyHistory.length,
                historyError,
                derivativesSeriesCount,
                derivativesError,
                backtest,
                backtestError,
              }}
            />

            <section className="space-y-4">
              <PredictionSectionHeader
                title="Forecast output"
                subtitle="Headline target and historical replay — what the model predicted vs what Nifty did."
                modelRole="display"
              />
              <PredictionSummary artifact={artifact} flashReturn={flashReturn} horizonDays={horizonDays} />

              <TechnicalContextStrip
                interpretation={
                  (artifact.prediction as { interpretation?: Record<string, unknown> } | undefined)
                    ?.interpretation as Parameters<typeof TechnicalContextStrip>[0]["interpretation"]
                }
                horizonDays={horizonDays}
              />

              <div className="space-y-3">
                <PredictionSectionHeader
                  title="Where Nifty is heading"
                  subtitle={`Pick any day with a recorded forecast and compare the ${horizonDays}d path to actual Nifty. Orange dashed = forecast; green = actual path.`}
                />
                <NiftyForecastReplayChart
                horizonDays={horizonDays}
                ledgerRows={dailyHistory}
                backtestEvals={backtest?.daily_evaluations ?? []}
                priceSeries={niftyPriceSeries}
                priceLoading={backtestLoading && !niftyPriceSeries.length}
                liveForecast={
                  artifact
                    ? {
                        asOf: artifact.as_of,
                        spot: artifact.spot ?? 0,
                        expectedReturnPct: artifact.prediction?.expected_return_pct ?? 0,
                        rangeLow: artifact.prediction?.range?.low,
                        rangeHigh: artifact.prediction?.range?.high,
                        simulatedReturnPct: simulation?.expected_return_pct,
                      }
                    : undefined
                }
                height={380}
                />
              </div>
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="What moves the forecast"
                subtitle="Factor workbench: shock one driver (or pick news/event), see Nifty path + cascade; forecast chart above updates when anchor is today."
                modelRole="feeds"
              />
              <FactorImpactWorkbench
                artifact={artifact}
                horizonDays={horizonDays}
                onSimulationChange={setSimulation}
              />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Scenario outcomes"
                subtitle="Most likely left → least likely right. Probability-weighted ranges anchor the headline when the model diverges."
                modelRole="feeds"
              />
              <ScenarioTiles
                scenarios={artifact.scenarios as Parameters<typeof ScenarioTiles>[0]["scenarios"]}
                horizonDays={horizonDays}
                reconciled={Boolean(artifact.prediction?.reconciled_with_scenarios)}
              />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Constituent drivers"
                subtitle="Expand any stock to see drivers, upcoming events, and sentiment history (archived daily)."
                modelRole="feeds"
              />
              <ConstituentDrivers signals={artifact.constituent_signals} />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Macro factor levels"
                subtitle="Current readings and each factor's contribution to the macro overlay."
                modelRole="feeds"
              />
              <FactorCompositionTable
                globalFactors={artifact.global_factors}
                contributors={artifact.factor_explanation?.contributors}
                sensitivity={artifact.factor_sensitivity as Parameters<typeof FactorCompositionTable>[0]["sensitivity"]}
              />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Derivatives &amp; institutional flows"
                subtitle="PCR, FII/DII nets, and FII futures vs Nifty 50 (right axis) — see if flows led index moves."
                modelRole="context"
              />
              <DerivativesFactorsPanel days={365} onLoadState={handleDerivativesLoadState} />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Macro factor drift (12 months)"
                subtitle="How key inputs moved vs Nifty 50 (% change from period start) — bold line is the index."
                modelRole="context"
              />
              <IndexFactorTimelineChart
                series={factorHistory}
                factors={[...MACRO_DRIFT_FACTORS].filter((f) => f !== "nifty_close")}
                height={260}
                coverageNotes={factorCoverageNotes}
              />
              {factorHistoryError ? (
                <p className="text-[11px] text-red-600 dark:text-red-400">{factorHistoryError}</p>
              ) : null}
            </section>

            <div className="grid gap-4 lg:grid-cols-2">
              <section className="space-y-3">
                <PredictionSectionHeader title="Sector breadth" subtitle="Sentiment dispersion across Nifty sectors." modelRole="context" />
                <SectorBreadthPanel breadth={artifact.sector_breadth} />
              </section>
              <section className="space-y-3">
                <PredictionSectionHeader title="Market context" subtitle="Nifty level vs macro factor history." modelRole="context" />
                <NiftyMarketContextChart
                  series={factorHistory}
                  forecastTarget={forecastTarget}
                  height={240}
                />
              </section>
            </div>

            <section className="space-y-3">
              <PredictionSectionHeader title="Forecast equation" subtitle="Bottom-up constituents + macro ridge overlay." modelRole="display" />
              <EquationCard prediction={artifact.prediction} spot={artifact.spot} horizonDays={horizonDays} />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Causal factor sensitivity"
                subtitle="Per-driver news, shock sweep, and downstream effects on Nifty (reconciled Ridge curves + cascade)."
                modelRole="display"
              />
              <CausalFactorExplorer
                artifact={artifact}
                horizonDays={horizonDays}
                factorHistory={factorHistory}
              />
            </section>

            <section id="news-impact" className="space-y-3">
              <PredictionSectionHeader
                title="News → Nifty impact"
                subtitle="Summarized headlines verified against factor data — predicted vs actual Nifty points; rejected clickbait hidden."
                modelRole="display"
              />
              <NewsImpactPanel
                horizonDays={horizonDays}
                pollMs={pollMs}
                monitorEnabled={pollMs > 0}
              />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Forecast track record"
                subtitle="Past predictions vs realised Nifty moves."
                modelRole="verify"
              />
              <ForecastHistorySection
                daily={dailyHistory}
                intraday={intradayHistory}
                meta={historyMeta}
                horizonDays={horizonDays}
                onOpenCounterfactual={() => {
                  document.getElementById("prediction-miss-analysis")?.scrollIntoView({ behavior: "smooth" });
                }}
              />
              <IndexFactorLedgerPanel
                daily={dailyHistory}
                intraday={intradayHistory}
                horizonDays={horizonDays}
              />
              {historyError ? (
                <p className="text-[11px] text-red-600 dark:text-red-400">{historyError}</p>
              ) : null}
              <PredictionDecompositionChart rows={dailyHistory} />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Walk-forward backtest"
                subtitle="Out-of-sample MAE and direction hit rate — validates the model, not the live headline."
                modelRole="verify"
              />
              <BacktestEvaluationPanel
                report={backtest}
                loading={backtestLoading}
                error={backtestError}
                onRefresh={() => void loadBacktest(true)}
                onMissSelect={(date) => {
                  setMissHighlightDate(date);
                  document.getElementById("prediction-miss-analysis")?.scrollIntoView({ behavior: "smooth" });
                }}
              />
            </section>

            <section id="prediction-miss-analysis" className="space-y-3">
              <PredictionSectionHeader
                title="Why predictions miss"
                subtitle="T0 vs maturity factor drift, headlines, and categorized learning notes for every wrong direction call."
                modelRole="verify"
              />
              <PredictionMissAnalysisPanel
                report={missAnalysis}
                counterfactual={counterfactual}
                loading={missAnalysisLoading}
                error={missAnalysisError}
                highlightDate={missHighlightDate}
                onRefresh={() => void loadMissAnalysis(true)}
              />
            </section>

            <section className="space-y-3">
              <PredictionSectionHeader
                title="Quant review"
                subtitle="Rule-based second opinion from playbooks + live TA — labeled separately from the Ridge headline."
                modelRole="verify"
              />
              <QuantReviewPanel ticker="NIFTY" horizonDays={horizonDays} />
            </section>

            <PredictionLearningPanel artifact={artifact} history={dailyHistory} />
          </>
        ) : null}
      </div>

      <PredictionPipelinePanel
        open={pipelinePanelOpen}
        running={running}
        logs={pipelineLogs}
        artifact={artifact}
        factorCatalog={factorCatalog}
        catalogLoading={catalogLoading}
      />
    </div>
  );
}
