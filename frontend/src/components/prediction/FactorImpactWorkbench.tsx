import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatFactorValue } from "@/lib/displayText";
import {
  api,
  ApiError,
  type IndexPredictionArtifact,
  type IndexSimulationResult,
  type IndexUpcomingEvent,
  type PlaygroundTrigger,
} from "@/lib/api";
import { factorLabel, triggerToWorkbenchState } from "@/lib/factorEventMapping";
import { FactorImpactInteractiveChart } from "@/components/prediction/FactorImpactInteractiveChart";
import { FactorNewsEventPanel } from "@/components/prediction/FactorNewsEventPanel";

interface Props {
  artifact: IndexPredictionArtifact;
  horizonDays: number;
  onSimulationChange?: (result: IndexSimulationResult | null) => void;
  headlines?: PlaygroundTrigger[];
  events?: PlaygroundTrigger[];
  contextLoading?: boolean;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

const CHANNEL_LABELS: Record<string, string> = {
  valuation_pct: "Valuation",
  liquidity_spread_pct: "Rates & spreads",
  energy_pct: "Energy",
  fx_rates_pct: "FX & rates",
  global_risk_pct: "Global risk",
  vol_pct: "Volatility",
  flows_pct: "Flows",
  technical_pct: "Technical",
  sentiment_news_pct: "Sentiment & news",
};

export function FactorImpactWorkbench({
  artifact,
  horizonDays,
  onSimulationChange,
  headlines: headlinesProp,
  events: eventsProp,
  contextLoading: contextLoadingProp,
}: Props) {
  const contributors = artifact.factor_explanation?.contributors ?? [];
  const channelAttribution = artifact.factor_explanation?.channel_attribution ?? undefined;
  const multicollinearityWarning = Boolean(artifact.factor_explanation?.multicollinearity_warning);
  const correlatedPairs = artifact.factor_explanation?.correlated_pairs ?? [];
  const attributionMethod = artifact.factor_explanation?.method;
  const attributionDisclaimer =
    artifact.factor_explanation?.attribution_disclaimer ??
    "Attribution shows model sensitivity, not causal effect. Correlated factors share credit.";
  const sensitivity = (artifact.factor_sensitivity ?? []) as Array<{
    factor?: string;
    label?: string;
    points?: Array<{ factor_delta_pct?: number; index_level?: number; return_pct?: number }>;
  }>;

  const baselineReturn = artifact.prediction?.expected_return_pct ?? 0;
  const spot = artifact.spot ?? 0;
  const baselineLevel = spot > 0 ? spot * (1 + baselineReturn / 100) : null;

  const rankedFactors = useMemo(() => {
    const keys = new Set<string>();
    const list: Array<{
      factor: string;
      label: string;
      contribution?: number;
      correlationCaveat?: boolean;
    }> = [];
    for (const c of contributors) {
      const f = c.factor || "";
      if (!f || keys.has(f)) continue;
      keys.add(f);
      list.push({
        factor: f,
        label: c.label || factorLabel(f),
        contribution: c.contribution_pct,
        correlationCaveat: c.correlation_caveat,
      });
    }
    for (const row of artifact.global_factors ?? []) {
      const f = row.factor || "";
      if (!f || keys.has(f)) continue;
      keys.add(f);
      list.push({ factor: f, label: row.label || factorLabel(f) });
    }
    return list;
  }, [contributors, artifact.global_factors]);

  const channelRows = useMemo(() => {
    if (!channelAttribution) return [];
    return Object.entries(channelAttribution)
      .filter(([key, value]) => !key.startsWith("_") && Number.isFinite(Number(value)))
      .map(([key, value]) => ({
        key,
        label: CHANNEL_LABELS[key] || key.replace(/_pct$/, "").replace(/_/g, " "),
        value: Number(value),
      }))
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  }, [channelAttribution]);

  const maxChannelAbs = useMemo(
    () => Math.max(0.01, ...channelRows.map((row) => Math.abs(row.value))),
    [channelRows],
  );

  const [activeFactor, setActiveFactor] = useState(rankedFactors[0]?.factor ?? "oil_brent");
  const [shockPct, setShockPct] = useState(0);
  const [cascadeOn, setCascadeOn] = useState(true);
  const [chartMode, setChartMode] = useState<"horizon" | "shock_sweep">("horizon");
  const [simulation, setSimulation] = useState<IndexSimulationResult | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);
  const [eventPresetId, setEventPresetId] = useState<string | undefined>();
  const [selectedTriggerId, setSelectedTriggerId] = useState<string | null>(null);
  const [headlinesLocal, setHeadlinesLocal] = useState<PlaygroundTrigger[]>([]);
  const [eventsLocal, setEventsLocal] = useState<PlaygroundTrigger[]>([]);
  const [contextLoadingLocal, setContextLoadingLocal] = useState(true);
  const useExternalContext = headlinesProp !== undefined || eventsProp !== undefined;
  const headlines = useExternalContext ? (headlinesProp ?? []) : headlinesLocal;
  const events = useExternalContext ? (eventsProp ?? []) : eventsLocal;
  const contextLoading = useExternalContext ? Boolean(contextLoadingProp) : contextLoadingLocal;
  const [whyText, setWhyText] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    if (rankedFactors.length && !rankedFactors.some((r) => r.factor === activeFactor)) {
      setActiveFactor(rankedFactors[0].factor);
    }
  }, [rankedFactors, activeFactor]);

  useEffect(() => {
    if (useExternalContext) return;
    let cancelled = false;
    setContextLoadingLocal(true);
    void api
      .getIndexPlaygroundContext(artifact.ticker || "NIFTY")
      .then((res) => {
        if (cancelled) return;
        setHeadlinesLocal(res.context?.headlines ?? []);
        setEventsLocal(res.context?.events ?? []);
      })
      .catch(() => {
        if (!cancelled) {
          setHeadlinesLocal([]);
          setEventsLocal([]);
        }
      })
      .finally(() => {
        if (!cancelled) setContextLoadingLocal(false);
      });
    return () => {
      cancelled = true;
    };
  }, [artifact.ticker, artifact.as_of, useExternalContext]);

  const runSimulate = useCallback(async () => {
    if (shockPct === 0 && !eventPresetId) {
      setSimulation(null);
      setSimError(null);
      onSimulationChange?.(null);
      return;
    }

    setSimulating(true);
    setSimError(null);
    try {
      const res = await api.simulateIndexPrediction({
        ticker: artifact.ticker || "NIFTY",
        horizon_days: horizonDays,
        primary_factor: eventPresetId ? undefined : activeFactor,
        primary_shock_pct: eventPresetId ? undefined : shockPct,
        cascade: cascadeOn,
        event_preset_id: eventPresetId,
      });
      if (res.status === "error" || res.status === "not_found") {
        setSimError(res.message || "Simulation failed");
        return;
      }
      if (res.simulation) {
        setSimulation(res.simulation);
        onSimulationChange?.(res.simulation);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Simulation request failed";
      if (err instanceof ApiError && (err.status === 405 || err.status === 404)) {
        setSimError(`${msg} — restart trade API on port 8899.`);
      } else {
        setSimError(msg);
      }
    } finally {
      setSimulating(false);
    }
  }, [
    activeFactor,
    shockPct,
    cascadeOn,
    eventPresetId,
    artifact.ticker,
    horizonDays,
    onSimulationChange,
  ]);

  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void runSimulate();
    }, 300);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [runSimulate]);

  const reset = () => {
    setShockPct(0);
    setEventPresetId(undefined);
    setSelectedTriggerId(null);
    setWhyText(null);
    setSimulation(null);
    setSimError(null);
    onSimulationChange?.(null);
  };

  const onTriggerSelect = (trigger: PlaygroundTrigger) => {
    const state = triggerToWorkbenchState(trigger);
    setSelectedTriggerId(trigger.id || null);
    setActiveFactor(state.primaryFactor);
    setShockPct(state.shockPct);
    setEventPresetId(state.eventPresetId);
    setWhyText(trigger.why || null);
  };

  const displayReturn = simulation?.expected_return_pct ?? baselineReturn;
  const displayLevel = simulation?.index_level ?? baselineLevel;
  const deltaReturn =
    simulation?.expected_return_pct != null
      ? simulation.expected_return_pct - baselineReturn
      : 0;

  const cascadeRows = simulation?.cascade_applied?.filter((r) => r.reason?.includes("cascade")) ?? [];

  const cascadeMeta = artifact.cascade_calibration;
  const cascadeMethodLabel =
    simulation?.cascade_method === "data_calibrated"
      ? "Data-calibrated (VAR blend)"
      : cascadeMeta?.status === "ok"
        ? "Heuristic (calibration pending)"
        : "Heuristic rules";

  const cascadeRegime =
    simulation?.cascade_regime ?? cascadeMeta?.regime ?? "calm";

  const activeMeta = rankedFactors.find((r) => r.factor === activeFactor);
  const baseVal = Number(
    artifact.global_factors?.find((g) => g.factor === activeFactor)?.value ??
      contributors.find((c) => c.factor === activeFactor)?.value,
  );

  if (!rankedFactors.length) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground shadow-sm">
        Factor ranking unavailable — run full analysis first.
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Factor impact workbench
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Select a factor or headline, shock it ±10%, see Nifty path to horizon with correlated macro
            cascade. Method: {cascadeMethodLabel}
            {cascadeRegime !== "calm" ? ` · ${cascadeRegime} regime` : ""}
            {attributionMethod ? ` · attribution: ${attributionMethod.replace(/_/g, " ")}` : ""}.
          </p>
          <p className="mt-1 text-[10px] text-amber-800 dark:text-amber-300">{attributionDisclaimer}</p>
        </div>
        <button
          type="button"
          onClick={reset}
          className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted/50"
        >
          <RotateCcw className="h-3 w-3" />
          Reset
        </button>
      </div>

      {multicollinearityWarning ? (
        <div className="mb-4 rounded-lg border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-[11px] text-violet-950 dark:text-violet-100">
          <p className="font-medium">
            {attributionMethod === "correlation_dependent_shap"
              ? "Correlated macro factors — covariance-aware SHAP"
              : "Correlated macro factors — group attribution active"}
          </p>
          <p className="mt-0.5 text-violet-900/90 dark:text-violet-200/90">
            {attributionMethod === "correlation_dependent_shap"
              ? "Credit is shared using 365d panel covariance. Prefer channel bars below; individual factor ranks remain approximate."
              : "Per-factor rankings split credit among correlated inputs. Prefer channel bars below; drill into individual factors with caution."}
          </p>
          {correlatedPairs.length > 0 ? (
            <ul className="mt-1.5 space-y-0.5 text-[10px] tabular-nums">
              {correlatedPairs.slice(0, 3).map((pair) => (
                <li key={`${pair.factor_a}-${pair.factor_b}`}>
                  {factorLabel(pair.factor_a || "")} ↔ {factorLabel(pair.factor_b || "")}
                  {pair.correlation != null ? ` (r=${Number(pair.correlation).toFixed(2)})` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {channelRows.length > 0 ? (
        <div className="mb-4 rounded-lg border bg-muted/20 px-3 py-2">
          <p className="text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
            Macro channel attribution
          </p>
          <ul className="mt-2 space-y-1.5">
            {channelRows.map((row) => (
              <li key={row.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)_4.5rem] items-center gap-2 text-[10px]">
                <span className="truncate text-muted-foreground">{row.label}</span>
                <div className="h-2 overflow-hidden rounded-full bg-muted/60">
                  <div
                    className={cn(
                      "h-full rounded-full",
                      row.value >= 0 ? "bg-emerald-500/70" : "bg-red-500/70",
                    )}
                    style={{ width: `${Math.min(100, (Math.abs(row.value) / maxChannelAbs) * 100)}%` }}
                  />
                </div>
                <span className="text-right tabular-nums font-medium">{fmtPct(row.value)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-muted/40 px-3 py-2">
          <p className="text-[9px] uppercase text-muted-foreground">Baseline ({horizonDays}d)</p>
          <p className="text-sm font-semibold tabular-nums">{fmtLevel(baselineLevel)}</p>
          <p className="text-[10px] text-muted-foreground">{fmtPct(baselineReturn)}</p>
        </div>
        <div
          className={cn(
            "rounded-lg px-3 py-2 transition-colors",
            simulation || shockPct !== 0
              ? "bg-primary/10 ring-1 ring-primary/30"
              : "bg-muted/40",
          )}
        >
          <p className="text-[9px] uppercase text-muted-foreground">
            Scenario{simulating ? " (recalculating…)" : ""}
          </p>
          <p className="text-sm font-semibold tabular-nums">{fmtLevel(displayLevel)}</p>
          <p
            className={cn(
              "text-[10px] tabular-nums",
              deltaReturn > 0 && "text-emerald-600 dark:text-emerald-400",
              deltaReturn < 0 && "text-red-600 dark:text-red-400",
            )}
          >
            {fmtPct(displayReturn)}
            {deltaReturn !== 0 ? ` (${deltaReturn >= 0 ? "+" : ""}${deltaReturn.toFixed(2)}% vs base)` : ""}
          </p>
        </div>
        <div className="rounded-lg bg-muted/40 px-3 py-2">
          <p className="text-[9px] uppercase text-muted-foreground">Cascade factors moved</p>
          <p className="text-sm font-semibold tabular-nums">{cascadeRows.length}</p>
          <p className="text-[10px] text-muted-foreground">
            {cascadeOn ? cascadeMethodLabel : "Primary only"}
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={activeFactor}
              onChange={(e) => {
                setActiveFactor(e.target.value);
                setEventPresetId(undefined);
                setSelectedTriggerId(null);
              }}
              className="rounded-md border bg-background px-2 py-1.5 text-[11px]"
            >
              {rankedFactors.map((r) => (
                <option key={r.factor} value={r.factor}>
                  {r.label}
                  {r.correlationCaveat ? " · correlated" : ""}
                  {r.contribution != null ? ` (${fmtPct(r.contribution)})` : ""}
                </option>
              ))}
            </select>
            <label className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <input
                type="checkbox"
                checked={cascadeOn}
                onChange={(e) => setCascadeOn(e.target.checked)}
                className="accent-primary"
              />
              Cascade linked factors
            </label>
            <div className="ml-auto flex gap-1">
              <button
                type="button"
                onClick={() => setChartMode("horizon")}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-[10px]",
                  chartMode === "horizon" && "border-primary/50 bg-primary/10",
                )}
              >
                Horizon path
              </button>
              <button
                type="button"
                onClick={() => setChartMode("shock_sweep")}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-[10px]",
                  chartMode === "shock_sweep" && "border-primary/50 bg-primary/10",
                )}
              >
                Shock sweep
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="w-8 text-[9px] text-muted-foreground">−10%</span>
            <input
              type="range"
              min={-10}
              max={10}
              step={1}
              value={shockPct}
              onChange={(e) => {
                setShockPct(Number(e.target.value));
                setEventPresetId(undefined);
              }}
              className="h-2 flex-1 cursor-pointer accent-primary"
              aria-label={`Shock ${activeMeta?.label || activeFactor}`}
            />
            <span className="w-8 text-right text-[9px] text-muted-foreground">+10%</span>
            <span className="w-12 text-right text-[10px] font-medium tabular-nums">
              {shockPct > 0 ? "+" : ""}
              {shockPct}%
            </span>
          </div>

          {Number.isFinite(baseVal) ? (
            <p className="text-[10px] text-muted-foreground">
              {activeMeta?.label}: {formatFactorValue(activeFactor, baseVal)}
              {simulation?.factor_overrides?.[activeFactor] != null ? (
                <>
                  {" → "}
                  <span className="font-medium text-primary">
                    {formatFactorValue(activeFactor, simulation.factor_overrides[activeFactor])}
                  </span>
                </>
              ) : null}
            </p>
          ) : null}

          {whyText ? (
            <p className="rounded-md bg-muted/30 px-2 py-1.5 text-[10px] text-muted-foreground">{whyText}</p>
          ) : null}

          {simError ? <p className="text-[11px] text-red-600 dark:text-red-400">{simError}</p> : null}

          <FactorImpactInteractiveChart
            spot={spot}
            horizonDays={horizonDays}
            baselineReturnPct={baselineReturn}
            simulation={simulation}
            activeFactor={activeFactor}
            sensitivity={sensitivity}
            upcomingEvents={artifact.upcoming_events as IndexUpcomingEvent[] | undefined}
            chartMode={chartMode}
            height={280}
          />

          {cascadeRows.length > 0 ? (
            <div className="rounded-lg border px-3 py-2">
              <p className="text-[9px] font-semibold uppercase text-muted-foreground">Cascade breakdown</p>
              <ul className="mt-1 space-y-0.5 text-[10px]">
                {simulation?.cascade_applied?.map((row) => (
                  <li key={row.factor} className="tabular-nums">
                    <span className="font-medium">{factorLabel(row.factor || "")}</span>:{" "}
                    {row.before} → {row.after}
                    {row.reason?.includes("cascade") ? (
                      <span className="text-muted-foreground"> (linked)</span>
                    ) : null}
                    {row.var_implied_after != null && row.heuristic_after != null ? (
                      <span className="block text-[9px] text-muted-foreground">
                        heuristic {row.heuristic_after} · VAR-implied {row.var_implied_after}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        <FactorNewsEventPanel
          headlines={headlines}
          events={events}
          selectedId={selectedTriggerId}
          onSelect={onTriggerSelect}
          loading={contextLoading}
        />
      </div>
    </div>
  );
}
