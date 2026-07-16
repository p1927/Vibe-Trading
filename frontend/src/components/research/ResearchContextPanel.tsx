import { AlertTriangle, ChevronDown, Sparkles } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { HubPlanArtifact, PlanPrediction, TradePlanScenario } from "@/lib/api";
import {
  buildPlanHeadline,
  buildPlanSummary,
  formatStrategyName,
  formatViewLabel,
  formatLegsSummary,
  shouldShowConfidence,
} from "@/lib/planDisplay";

export type { PlanPrediction };

export interface RankedStrategy {
  name?: string;
  tier?: string | null;
  score?: number | null;
  pop?: number | null;
  rationale?: string | null;
}

export interface ResearchContextPanelProps {
  underlying: string;
  artifact?: HubPlanArtifact | null;
  prediction?: PlanPrediction | null;
  recommendedName?: string | null;
  recommendedRationale?: string | null;
  recommendedTier?: string | null;
  recommendedScore?: number | null;
  rankedStrategies?: RankedStrategy[];
  scenarios?: TradePlanScenario[];
}

function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${(v * (v <= 1 ? 100 : 1)).toFixed(digits)}%`;
}

function tierTone(tier: string | null | undefined): string {
  const t = (tier || "").toLowerCase();
  if (t.includes("strong") || t.includes("best")) {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";
  }
  if (t.includes("consider")) {
    return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400";
  }
  return "border-muted bg-muted/40 text-muted-foreground";
}

export function ResearchContextPanel({
  underlying,
  artifact,
  prediction,
  recommendedName,
  recommendedRationale,
  recommendedTier,
  recommendedScore,
  rankedStrategies = [],
  scenarios = [],
}: ResearchContextPanelProps) {
  const [open, setOpen] = useState(true);

  const merged: HubPlanArtifact = artifact ?? {
    underlying,
    prediction: prediction ?? undefined,
    recommended_name: recommendedName ?? undefined,
    recommended_rationale: recommendedRationale ?? undefined,
    recommended_tier: recommendedTier ?? undefined,
    recommended_score: recommendedScore ?? undefined,
    ranked_strategies: rankedStrategies as Array<Record<string, unknown>>,
    scenarios,
  };

  const pred = merged.prediction;
  const headline = buildPlanHeadline(merged);
  const summary = buildPlanSummary(merged);
  const legsSummary = formatLegsSummary(merged.recommended_legs);
  const warnings = merged.data_warnings ?? [];
  const hasRanked = (merged.ranked_strategies?.length ?? 0) > 0;
  const hasScenarios = (merged.scenarios?.length ?? 0) > 0;
  const showConfidence = shouldShowConfidence(pred?.confidence);
  const isOptions = merged.asset_type !== "stock";

  return (
    <div className="overflow-hidden rounded-xl border border-emerald-500/20 bg-gradient-to-br from-emerald-500/5 via-card to-card shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-muted/20"
      >
        <div className="flex min-w-0 items-center gap-2">
          <div className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
            <Sparkles className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold">
              {isOptions ? "Options plan" : "Stock plan"} · {underlying}
            </h3>
            <p className="line-clamp-2 text-[11px] leading-snug text-muted-foreground">{headline}</p>
          </div>
        </div>
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition", open && "rotate-180")}
        />
      </button>

      {open && (
        <div className="space-y-4 border-t px-4 py-4 text-[12px]">
          {warnings.length > 0 && (
            <div className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <p className="leading-relaxed">{warnings.join(" ")}</p>
            </div>
          )}

          <div className="space-y-1.5">
            <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              What this means
            </div>
            <p className="leading-relaxed text-foreground">{summary}</p>
            {legsSummary && (
              <p className="text-[11px] text-muted-foreground">
                <span className="font-medium text-foreground">Suggested legs:</span> {legsSummary}
              </p>
            )}
          </div>

          {(recommendedTier || (recommendedScore != null && showConfidence)) && (
            <div className="flex flex-wrap items-center gap-2">
              {recommendedTier && (
                <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase", tierTone(recommendedTier))}>
                  {recommendedTier}
                </span>
              )}
              {recommendedScore != null && showConfidence && (
                <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold tabular-nums text-muted-foreground">
                  Score {(recommendedScore * (recommendedScore <= 1 ? 100 : 1)).toFixed(0)}
                </span>
              )}
            </div>
          )}

          {pred && (pred.view || pred.iv_regime || pred.expected_move_pct != null) && (
            <div className="grid grid-cols-2 gap-2">
              {pred.view && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Market view</dt>
                  <dd className="mt-0.5 font-medium">{formatViewLabel(pred.view)}</dd>
                </div>
              )}
              {pred.iv_regime && isOptions && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">IV regime</dt>
                  <dd className="mt-0.5 font-medium capitalize">{pred.iv_regime}</dd>
                </div>
              )}
              {pred.expected_move_pct != null && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Expected move</dt>
                  <dd className="mt-0.5 font-medium tabular-nums">±{Number(pred.expected_move_pct).toFixed(1)}%</dd>
                </div>
              )}
              {merged.spot != null && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Spot</dt>
                  <dd className="mt-0.5 font-medium tabular-nums">{Number(merged.spot).toLocaleString()}</dd>
                </div>
              )}
            </div>
          )}

          {hasRanked && (
            <div className="space-y-2">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Ranked strategies
              </div>
              <ul className="space-y-1.5">
                {(merged.ranked_strategies as RankedStrategy[]).slice(0, 4).map((s, i) => (
                  <li
                    key={`${s.name}-${i}`}
                    className={cn(
                      "rounded-lg border px-3 py-2",
                      s.name === recommendedName && "border-emerald-500/30 bg-emerald-500/5",
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold capitalize">{formatStrategyName(s.name || "strategy")}</span>
                      {s.tier && (
                        <span className={cn("rounded-full border px-1.5 py-px text-[9px] font-semibold uppercase", tierTone(s.tier))}>
                          {s.tier}
                        </span>
                      )}
                    </div>
                    {s.rationale && (
                      <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{s.rationale}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {hasScenarios && (
            <div className="space-y-2">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">If this happens…</div>
              <ul className="space-y-1.5">
                {(merged.scenarios as TradePlanScenario[]).slice(0, 4).map((sc, i) => (
                  <li key={`${sc.name}-${i}`} className="rounded-lg border bg-background/40 px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {sc.strategy_hint && (
                        <span className="font-semibold capitalize text-emerald-700 dark:text-emerald-400">
                          → {formatStrategyName(sc.strategy_hint)}
                        </span>
                      )}
                      {sc.probability != null && (
                        <span className="text-[10px] tabular-nums text-muted-foreground">
                          {fmtPct(Number(sc.probability), 0)} likely
                        </span>
                      )}
                    </div>
                    {sc.trigger && (
                      <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{sc.trigger}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
