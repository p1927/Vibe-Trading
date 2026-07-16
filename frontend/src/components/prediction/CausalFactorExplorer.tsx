import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { CausalFactorHistoryChart } from "@/components/charts/CausalFactorHistoryChart";
import {
  CascadeFactorBars,
  FactorShockImpactChart,
  pointAtShock,
  ShockSummaryCards,
} from "@/components/charts/FactorShockImpactChart";
import {
  api,
  type IndexFactorHistoryPoint,
  type IndexPredictionArtifact,
  type IndexSimulationResult,
  type PlaygroundTrigger,
} from "@/lib/api";
import { factorLabel } from "@/lib/factorEventMapping";
import { CASCADE_DOWNSTREAM, PINNED_CAUSAL_FACTORS } from "@/lib/factorCascadeMap";
import { researchNoteForFactor } from "@/lib/factorResearchNotes";
import { formatFactorValue } from "@/lib/displayText";

interface Props {
  artifact: IndexPredictionArtifact;
  horizonDays: number;
  factorHistory?: IndexFactorHistoryPoint[];
}

interface CascadeDownstreamRule {
  factor: string;
  multiplier: number;
  mode: string;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function CausalFactorExplorer({ artifact, horizonDays, factorHistory = [] }: Props) {
  const sensitivity = (artifact.factor_sensitivity ?? []) as Array<{
    factor?: string;
    label?: string;
    points?: Array<{ factor_delta_pct?: number; index_level?: number; return_pct?: number }>;
  }>;

  const contributors = artifact.factor_explanation?.contributors ?? [];
  const baselineReturn = artifact.prediction?.expected_return_pct ?? 0;
  const spot = artifact.spot ?? 0;

  const factorOptions = useMemo(() => {
    const seen = new Set<string>();
    const list: Array<{ factor: string; label: string; pinned?: boolean }> = [];
    for (const key of PINNED_CAUSAL_FACTORS) {
      if (seen.has(key)) continue;
      seen.add(key);
      list.push({ factor: key, label: factorLabel(key), pinned: true });
    }
    for (const c of contributors) {
      const f = c.factor || "";
      if (!f || seen.has(f)) continue;
      seen.add(f);
      list.push({ factor: f, label: c.label || factorLabel(f) });
    }
    for (const row of sensitivity) {
      const f = row.factor || "";
      if (!f || seen.has(f)) continue;
      seen.add(f);
      list.push({ factor: f, label: row.label || factorLabel(f) });
    }
    return list;
  }, [contributors, sensitivity]);

  const [activeFactor, setActiveFactor] = useState(factorOptions[0]?.factor ?? "fii_net_5d");
  const [chartMode, setChartMode] = useState<"history" | "shock">("history");
  const [shockPct, setShockPct] = useState(0);
  const [simulation, setSimulation] = useState<IndexSimulationResult | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [factorNews, setFactorNews] = useState<Record<string, PlaygroundTrigger[]>>({});
  const [cascadeRules, setCascadeRules] = useState<Record<string, CascadeDownstreamRule[]>>(CASCADE_DOWNSTREAM);
  const [contextLoading, setContextLoading] = useState(true);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    if (factorOptions.length && !factorOptions.some((f) => f.factor === activeFactor)) {
      setActiveFactor(factorOptions[0].factor);
    }
  }, [factorOptions, activeFactor]);

  useEffect(() => {
    let cancelled = false;
    setContextLoading(true);
    void api
      .getIndexPlaygroundContext(artifact.ticker || "NIFTY")
      .then((res) => {
        if (cancelled) return;
        const ctx = res.context;
        if (ctx?.factor_news) setFactorNews(ctx.factor_news as Record<string, PlaygroundTrigger[]>);
        if (ctx?.cascade_downstream) {
          setCascadeRules(ctx.cascade_downstream as Record<string, CascadeDownstreamRule[]>);
        }
      })
      .catch(() => {
        if (!cancelled) setFactorNews({});
      })
      .finally(() => {
        if (!cancelled) setContextLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [artifact.ticker, artifact.as_of]);

  const activeCurve = sensitivity.find((c) => c.factor === activeFactor);
  const shockPoints = activeCurve?.points ?? [];
  const baselinePoint = useMemo(() => pointAtShock(shockPoints, 0), [shockPoints]);
  const isolatedAtShock = useMemo(
    () => pointAtShock(shockPoints, shockPct),
    [shockPoints, shockPct],
  );
  const activeMeta = factorOptions.find((f) => f.factor === activeFactor);
  const researchNote = researchNoteForFactor(activeFactor);
  const downstream =
    cascadeRules[activeFactor] ??
    CASCADE_DOWNSTREAM[activeFactor] ??
    [];

  const baseVal = Number(
    artifact.global_factors?.find((g) => g.factor === activeFactor)?.value ??
      contributors.find((c) => c.factor === activeFactor)?.value,
  );

  const runSimulate = useCallback(async () => {
    if (shockPct === 0) {
      setSimulation(null);
      return;
    }
    setSimulating(true);
    try {
      const res = await api.simulateIndexPrediction({
        ticker: artifact.ticker || "NIFTY",
        horizon_days: horizonDays,
        primary_factor: activeFactor,
        primary_shock_pct: shockPct,
        cascade: true,
      });
      if (res.simulation) setSimulation(res.simulation);
    } catch {
      setSimulation(null);
    } finally {
      setSimulating(false);
    }
  }, [activeFactor, shockPct, artifact.ticker, horizonDays]);

  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void runSimulate();
    }, 350);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [runSimulate]);

  const newsItems = factorNews[activeFactor] ?? [];

  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Causal factor explorer
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Read linked news for each driver. <span className="font-medium text-foreground">History</span>{" "}
          shows past Nifty vs factor. <span className="font-medium text-foreground">Shock</span> — move
          the slider ±1%…±10% to see how Nifty at horizon changes and which linked factors move with it.
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {factorOptions.map((f) => (
          <button
            key={f.factor}
            type="button"
            onClick={() => {
              setActiveFactor(f.factor);
              setShockPct(0);
              setSimulation(null);
            }}
            className={cn(
              "rounded-full border px-2.5 py-1 text-[10px] transition-colors",
              activeFactor === f.factor
                ? "border-primary/60 bg-primary/10 font-medium text-foreground"
                : "border-border/60 text-muted-foreground hover:bg-muted/40",
            )}
          >
            {f.label}
            {f.pinned ? " · key" : ""}
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => setChartMode("history")}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-[10px]",
                  chartMode === "history" && "border-primary/50 bg-primary/10",
                )}
              >
                History
              </button>
              <button
                type="button"
                onClick={() => setChartMode("shock")}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-[10px]",
                  chartMode === "shock" && "border-primary/50 bg-primary/10",
                )}
              >
                Shock
              </button>
            </div>
            {chartMode === "shock" ? (
              <>
                <span className="text-[9px] text-muted-foreground">−10%</span>
                <input
                  type="range"
                  min={-10}
                  max={10}
                  step={1}
                  value={shockPct}
                  onChange={(e) => setShockPct(Number(e.target.value))}
                  className="h-2 max-w-[220px] flex-1 cursor-pointer accent-primary"
                  aria-label={`Shock ${activeMeta?.label || activeFactor}`}
                />
                <span className="text-[9px] text-muted-foreground">+10%</span>
                <span className="text-[10px] font-medium tabular-nums">
                  {shockPct > 0 ? "+" : ""}
                  {shockPct}%
                  {simulating ? " …" : ""}
                </span>
              </>
            ) : null}
          </div>

          {Number.isFinite(baseVal) ? (
            <p className="text-[10px] text-muted-foreground">
              {activeMeta?.label}: {formatFactorValue(activeFactor, baseVal)}
            </p>
          ) : null}

          {researchNote ? (
            <p className="rounded-md bg-muted/30 px-2 py-1.5 text-[10px] text-muted-foreground">
              {researchNote.summary}
            </p>
          ) : null}

          {chartMode === "history" ? (
            <CausalFactorHistoryChart
              activeFactor={activeFactor}
              factorHistory={factorHistory}
              height={280}
            />
          ) : (
            <div className="space-y-3">
              <ShockSummaryCards
                baseline={baselinePoint}
                isolated={isolatedAtShock}
                simulation={simulation}
                shockPct={shockPct}
              />
              <FactorShockImpactChart
                label={activeMeta?.label || factorLabel(activeFactor)}
                points={shockPoints}
                activeShockPct={shockPct}
                simulation={simulation}
                height={260}
              />
              <p className="text-[10px] text-muted-foreground">
                Blue line: Nifty at {horizonDays}d if only {activeMeta?.label || factorLabel(activeFactor)}{" "}
                moves (others fixed). Pin = your slider. Cascade card includes linked factors (FII → VIX,
                oil → INR, etc.) that also feed the Nifty forecast.
              </p>
              {simulation?.cascade_applied?.length ? (
                <CascadeFactorBars rows={simulation.cascade_applied} />
              ) : shockPct !== 0 && simulating ? (
                <p className="text-[10px] text-muted-foreground">Recalculating cascade…</p>
              ) : null}
            </div>
          )}

          {chartMode === "shock" && simulation?.expected_return_pct != null && shockPct !== 0 ? (
            <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-[10px]">
              <span className="font-medium">Nifty spot today:</span> {fmtLevel(spot)}
              {" · "}
              <span className="font-medium">Cascade target:</span>{" "}
              {fmtLevel(simulation.index_level)} ({fmtPct(simulation.expected_return_pct)} over {horizonDays}d)
              {baselineReturn != null ? (
                <span className="text-muted-foreground">
                  {" "}
                  — {fmtPct(simulation.expected_return_pct - baselineReturn)} vs headline
                </span>
              ) : null}
            </div>
          ) : null}

          {downstream.length > 0 ? (
            <div className="rounded-lg border px-3 py-2">
              <p className="text-[9px] font-semibold uppercase text-muted-foreground">
                Typical downstream effects (cause →)
              </p>
              <ul className="mt-1 space-y-0.5 text-[10px] text-muted-foreground">
                {downstream.map((row) => (
                  <li key={row.factor}>
                    <span className="font-medium text-foreground">{factorLabel(row.factor)}</span>
                    {row.mode === "absolute"
                      ? ` · ~${row.multiplier} pts per 1% ${activeMeta?.label || activeFactor} shock`
                      : ` · ~${(row.multiplier * 100).toFixed(0)}% of 1% primary shock`}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        <div className="space-y-2">
          <p className="text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
            News & events — {activeMeta?.label || factorLabel(activeFactor)}
          </p>
          {contextLoading ? (
            <p className="text-[11px] text-muted-foreground">Loading headlines…</p>
          ) : newsItems.length ? (
            <div className="max-h-[420px] space-y-1.5 overflow-y-auto">
              {newsItems.map((item, i) => (
                <div
                  key={item.id || `n-${i}`}
                  className="rounded-lg border border-border/60 px-2.5 py-2 text-[10px]"
                >
                  <p className="font-medium leading-snug">{item.title || item.label}</p>
                  {item.why ? (
                    <p className="mt-1 text-muted-foreground line-clamp-3">{item.why}</p>
                  ) : null}
                  <p className="mt-0.5 text-[9px] text-muted-foreground">
                    {item.kind === "headline" || item.kind === "material" ? "News" : item.kind ?? "Event"}
                    {item.source ? ` · ${item.source}` : ""}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              No headlines tagged to this factor today — try FII/DII or oil for flow/commodity news.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
