import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatFactorValue } from "@/lib/displayText";
import {
  api,
  ApiError,
  type IndexFactorContributor,
  type IndexPredictionArtifact,
  type IndexSimulationResult,
} from "@/lib/api";

interface SensitivityCurve {
  factor?: string;
  label?: string;
  current_value?: number;
  points?: Array<{ factor_delta_pct?: number; return_pct?: number; index_level?: number }>;
}

interface Props {
  artifact: IndexPredictionArtifact;
  horizonDays: number;
  onSimulationChange?: (result: IndexSimulationResult | null) => void;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

const ABSOLUTE_FACTORS = new Set(["repo_rate", "india_vix", "us_10y", "fii_net_5d"]);

function valueFromSlider(base: number, deltaPct: number, factor: string): number {
  if (ABSOLUTE_FACTORS.has(factor)) {
    return base + (deltaPct / 100) * Math.max(Math.abs(base), 1);
  }
  return base * (1 + deltaPct / 100);
}

function estimateReturnFromCurve(curve: SensitivityCurve | undefined, deltaPct: number): number | null {
  const points = curve?.points ?? [];
  if (!points.length) return null;
  const sorted = [...points].sort(
    (a, b) => Number(a.factor_delta_pct ?? 0) - Number(b.factor_delta_pct ?? 0),
  );
  const exact = sorted.find((p) => Number(p.factor_delta_pct) === deltaPct);
  if (exact?.return_pct != null && Number.isFinite(exact.return_pct)) return exact.return_pct;
  let below = sorted[0];
  let above = sorted[sorted.length - 1];
  for (const p of sorted) {
    const x = Number(p.factor_delta_pct ?? 0);
    if (x <= deltaPct) below = p;
    if (x >= deltaPct) {
      above = p;
      break;
    }
  }
  const x0 = Number(below.factor_delta_pct ?? 0);
  const x1 = Number(above.factor_delta_pct ?? 0);
  const y0 = Number(below.return_pct ?? 0);
  const y1 = Number(above.return_pct ?? 0);
  if (x0 === x1) return y0;
  const t = (deltaPct - x0) / (x1 - x0);
  return y0 + t * (y1 - y0);
}

export function FactorPlayground({ artifact, horizonDays, onSimulationChange }: Props) {
  const contributors = artifact.factor_explanation?.contributors ?? [];
  const sensitivity = (artifact.factor_sensitivity ?? []) as SensitivityCurve[];
  const baselineReturn = artifact.prediction?.expected_return_pct ?? 0;
  const spot = artifact.spot ?? 0;
  const baselineLevel = spot > 0 ? spot * (1 + baselineReturn / 100) : null;

  const [sliders, setSliders] = useState<Record<string, number>>({});
  const [simulation, setSimulation] = useState<IndexSimulationResult | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  const sensitivityByFactor = useMemo(() => {
    const map = new Map<string, SensitivityCurve>();
    for (const row of sensitivity) {
      if (row.factor) map.set(row.factor, row);
    }
    return map;
  }, [sensitivity]);

  const factorMeta = useMemo(() => {
    const map = new Map<string, { label: string; current: number; fromContributor: boolean }>();
    for (const row of sensitivity) {
      if (!row.factor) continue;
      const current = Number(row.current_value ?? 0);
      if (Number.isFinite(current)) {
        map.set(row.factor, {
          label: row.label || row.factor,
          current,
          fromContributor: false,
        });
      }
    }
    for (const gf of artifact.global_factors ?? []) {
      const key = gf.factor;
      if (!key) continue;
      const val = Number(gf.value);
      if (!Number.isFinite(val)) continue;
      if (!map.has(key)) {
        map.set(key, { label: gf.label || key, current: val, fromContributor: false });
      }
    }
    for (const c of contributors) {
      const key = c.factor;
      if (!key) continue;
      const val = Number(c.value);
      if (!Number.isFinite(val)) continue;
      const existing = map.get(key);
      map.set(key, {
        label: c.label || existing?.label || key,
        current: val,
        fromContributor: true,
      });
    }
    return map;
  }, [sensitivity, artifact.global_factors, contributors]);

  const ranked = useMemo(() => {
    const list = [...contributors].sort(
      (a, b) => Math.abs(b.contribution_pct ?? 0) - Math.abs(a.contribution_pct ?? 0),
    );
    return list.slice(0, 12);
  }, [contributors]);

  const maxAbsContrib = useMemo(
    () => Math.max(...ranked.map((r) => Math.abs(r.contribution_pct ?? 0)), 0.01),
    [ranked],
  );

  const runSimulate = useCallback(
    async (nextSliders: Record<string, number>) => {
      const active = Object.entries(nextSliders).filter(([, v]) => v !== 0);
      if (!active.length) {
        setSimulation(null);
        setSimError(null);
        onSimulationChange?.(null);
        return;
      }
      const overrides: Record<string, number> = {};
      for (const [factor, deltaPct] of active) {
        const meta = factorMeta.get(factor);
        if (!meta) continue;
        overrides[factor] = valueFromSlider(meta.current, deltaPct, factor);
      }
      if (!Object.keys(overrides).length) {
        setSimError("No adjustable factors with live values — run full analysis first.");
        return;
      }

      setSimulating(true);
      setSimError(null);
      try {
        const res = await api.simulateIndexPrediction({
          ticker: artifact.ticker || "NIFTY",
          horizon_days: horizonDays,
          factor_overrides: overrides,
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
          setSimError(
            `${msg} — restart the trade API (port 8899) so /simulate is registered, then reload this page.`,
          );
        } else {
          setSimError(msg);
        }
      } finally {
        setSimulating(false);
      }
    },
    [artifact.ticker, factorMeta, horizonDays, onSimulationChange],
  );

  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void runSimulate(sliders);
    }, 300);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [sliders, runSimulate]);

  const reset = () => {
    setSliders({});
    setSimulation(null);
    setSimError(null);
    onSimulationChange?.(null);
  };

  const displayReturn = simulation?.expected_return_pct ?? baselineReturn;
  const displayLevel = simulation?.index_level ?? baselineLevel;
  const deltaReturn =
    simulation?.expected_return_pct != null
      ? simulation.expected_return_pct - baselineReturn
      : 0;

  if (!ranked.length) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground shadow-sm">
        Factor ranking unavailable — run full analysis (needs macro model + attribution).
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Factor impact ranking & what-if playground
          </p>
          <p className="text-[11px] text-muted-foreground">
            Move a slider to shock one factor ±10%. Nifty target updates live (chart above reflects your scenario).
          </p>
        </div>
        <button
          type="button"
          onClick={reset}
          className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted/50"
        >
          <RotateCcw className="h-3 w-3" />
          Reset all
        </button>
      </div>

      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-muted/40 px-3 py-2">
          <p className="text-[9px] uppercase text-muted-foreground">Baseline ({horizonDays}d)</p>
          <p className="text-sm font-semibold tabular-nums">{fmtLevel(baselineLevel)}</p>
          <p className="text-[10px] text-muted-foreground">{fmtPct(baselineReturn)}</p>
        </div>
        <div
          className={cn(
            "rounded-lg px-3 py-2 transition-colors",
            (simulation || Object.values(sliders).some((v) => v !== 0))
              ? "bg-primary/10 ring-1 ring-primary/30"
              : "bg-muted/40",
          )}
        >
          <p className="text-[9px] uppercase text-muted-foreground">
            Your scenario{simulating ? " (recalculating…)" : simulation ? "" : Object.values(sliders).some((v) => v !== 0) ? " (waiting…)" : ""}
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
            {deltaReturn !== 0
              ? ` (${deltaReturn >= 0 ? "+" : ""}${deltaReturn.toFixed(2)}% vs base)`
              : ""}
          </p>
        </div>
        <div className="rounded-lg bg-muted/40 px-3 py-2">
          <p className="text-[9px] uppercase text-muted-foreground">Active tweaks</p>
          <p className="text-sm font-semibold tabular-nums">
            {Object.values(sliders).filter((v) => v !== 0).length}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {simulating ? "Recalculating…" : simError ? "Error — see below" : "factors adjusted"}
          </p>
        </div>
      </div>

      {simError ? (
        <p className="mb-3 text-[11px] text-red-600 dark:text-red-400">{simError}</p>
      ) : null}

      <div className="space-y-2">
        {ranked.map((row: IndexFactorContributor, idx) => {
          const key = row.factor || `f-${idx}`;
          const contrib = row.contribution_pct ?? 0;
          const barPct = (Math.abs(contrib) / maxAbsContrib) * 100;
          const sliderVal = sliders[key] ?? 0;
          const meta = factorMeta.get(key);
          const curve = sensitivityByFactor.get(key);
          const baseVal = meta?.current ?? Number(row.value);
          const canAdjust = meta != null && Number.isFinite(baseVal);
          const shockedVal = canAdjust ? valueFromSlider(baseVal, sliderVal, key) : null;
          const previewRet = sliderVal !== 0 ? estimateReturnFromCurve(curve, sliderVal) : null;

          return (
            <div
              key={key}
              className={cn(
                "rounded-lg border px-3 py-3 transition-colors",
                sliderVal !== 0 && "border-primary/40 bg-primary/5",
              )}
            >
              <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:gap-4">
                <div className="flex min-w-0 flex-1 items-start gap-2">
                  <span className="w-6 shrink-0 text-[10px] font-mono text-muted-foreground">
                    #{idx + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                      <span className="text-[12px] font-medium">{row.label || key}</span>
                      <span
                        className={cn(
                          "text-[11px] tabular-nums font-medium",
                          contrib > 0 && "text-emerald-600 dark:text-emerald-400",
                          contrib < 0 && "text-red-600 dark:text-red-400",
                        )}
                      >
                        {fmtPct(contrib)} on Nifty
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn(
                          "h-full rounded-full transition-all",
                          contrib >= 0 ? "bg-emerald-500/70" : "bg-red-500/70",
                        )}
                        style={{ width: `${barPct}%` }}
                      />
                    </div>
                    <p className="mt-0.5 text-[9px] text-muted-foreground">
                      {row.share_of_macro != null
                        ? `${Math.round(row.share_of_macro * 100)}% of macro overlay`
                        : "Macro contributor"}
                      {canAdjust
                        ? ` · now ${formatFactorValue(key, baseVal)}`
                        : " · no live value"}
                    </p>
                  </div>
                </div>

                {canAdjust ? (
                  <div className="flex w-full flex-col gap-1 lg:w-[320px] lg:shrink-0">
                    <div className="flex items-center gap-2">
                      <span className="w-7 text-[9px] text-muted-foreground">−10%</span>
                      <input
                        type="range"
                        min={-10}
                        max={10}
                        step={1}
                        value={sliderVal}
                        onChange={(e) =>
                          setSliders((prev) => ({ ...prev, [key]: Number(e.target.value) }))
                        }
                        className="h-2 flex-1 cursor-pointer accent-primary"
                        aria-label={`Adjust ${row.label || key}`}
                      />
                      <span className="w-7 text-right text-[9px] text-muted-foreground">+10%</span>
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] tabular-nums">
                      <span className="text-muted-foreground">
                        Shock:{" "}
                        <span className="font-mono text-foreground">
                          {formatFactorValue(key, baseVal)}
                        </span>
                        {sliderVal !== 0 && shockedVal != null ? (
                          <>
                            {" → "}
                            <span className="font-mono font-medium text-primary">
                              {formatFactorValue(key, shockedVal)}
                            </span>
                          </>
                        ) : null}
                      </span>
                      <span
                        className={cn(
                          "font-medium",
                          sliderVal > 0 && "text-emerald-600 dark:text-emerald-400",
                          sliderVal < 0 && "text-red-600 dark:text-red-400",
                        )}
                      >
                        {sliderVal > 0 ? "+" : ""}
                        {sliderVal}% shock
                        {previewRet != null && sliderVal !== 0
                          ? ` → ${fmtPct(previewRet)} Nifty`
                          : ""}
                      </span>
                    </div>
                  </div>
                ) : (
                  <p className="text-[9px] text-muted-foreground lg:w-[320px]">
                    Run analysis to populate this factor&apos;s live level.
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
