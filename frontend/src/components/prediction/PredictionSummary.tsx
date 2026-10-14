import { cn } from "@/lib/utils";
import type { IndexPredictionArtifact } from "@/lib/api";
import { tradeUiDeepLink } from "@/lib/trade-ui";

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

function fmtNum(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function viewTone(view: string | null | undefined): string {
  const v = (view || "").toLowerCase();
  if (v.includes("bull")) return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";
  if (v.includes("bear")) return "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400";
  return "border-muted bg-muted/40 text-muted-foreground";
}

/** The band's provenance as the server recorded it (D401); an artifact written before D401 has none. */
export function bandSourceLabel(range: { half_width_pct?: number; band_source?: string } | null | undefined): string {
  if (range?.band_source === "walk_forward_oos") return "80% conformal, walk-forward OOS error";
  if (range?.band_source === "prior") return "prior width, not measured (under 20 OOS evaluations)";
  if (range?.band_source) return `band source: ${range.band_source}`; // a source this page does not know is named, not hidden
  return "width not recorded (run predates D401): run analysis again";
}

interface CardProps {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
  flash?: boolean;
}

function StatCard({ label, value, sub, tone, flash }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border p-4 shadow-sm transition-colors duration-500",
        tone,
        flash && "ring-2 ring-primary/40",
      )}
    >
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      {sub ? <p className="mt-0.5 text-[11px] opacity-80">{sub}</p> : null}
    </div>
  );
}

interface Props {
  artifact: IndexPredictionArtifact;
  flashReturn?: boolean;
  horizonDays?: number;
}

export function PredictionSummary({ artifact, flashReturn, horizonDays = 14 }: Props) {
  const pred = artifact.prediction || {};
  const range = pred.range || {};
  const accuracy = artifact.accuracy || {};
  const regime = artifact.regime || {};
  const view = String(pred.view || "neutral");
  const halfWidth = range.half_width_pct;
  const directionConf = pred.direction_confidence;
  const modelDirectionScore = pred.direction_model_score ?? pred.direction_confidence_raw;
  const hitRate =
    accuracy.direction_hit_rate_walk_forward ??
    accuracy.direction_hit_rate_14d ??
    accuracy.direction_hit_rate;
  const mae = accuracy.mae_14d_pct ?? accuracy.mae_pct;
  const sampleCount = accuracy.sample_count ?? 0;
  const evalCount = pred.direction_eval_count ?? accuracy.eval_count;
  const spot = artifact.spot;
  const expected = pred.expected_return_pct;
  const directionView = String(pred.direction_view || pred.view || "neutral");
  const lowDirectionConf =
    directionConf != null &&
    Number.isFinite(Number(directionConf)) &&
    Number(directionConf) < 0.55;
  const directionReturnConflict =
    expected != null &&
    Number.isFinite(expected) &&
    ((directionView.includes("bull") && expected < -0.15) ||
      (directionView.includes("bear") && expected > 0.15));
  const targetLevel =
    spot != null && expected != null && Number.isFinite(spot) && Number.isFinite(expected)
      ? spot * (1 + expected / 100)
      : null;

  const accuracySub =
    evalCount != null && Number(evalCount) > 0
      ? `Walk-forward OOS · ${evalCount} eval rows · MAE ${mae != null ? `${mae.toFixed(2)}%` : "—"}`
      : sampleCount > 0
        ? `Reconciled ledger · ${sampleCount} matured forecasts · MAE ${mae != null ? `${mae.toFixed(2)}%` : "—"}`
        : "Forecasts need horizon to mature; calibration runs nightly";

  const regimeSub = [
    regime.india_vix != null ? `VIX ${regime.india_vix.toFixed(2)}` : null,
    regime.trend_20d ? `20d ${regime.trend_20d}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const momentumCov = pred.momentum_coverage;
  const lowMomentum =
    momentumCov != null &&
    momentumCov.total != null &&
    momentumCov.total > 0 &&
    momentumCov.coverage_pct != null && // an unreported coverage is unknown, not 0%
    momentumCov.coverage_pct < 50;

  const constituentsAgeDays = artifact.constituents_as_of
    ? (Date.now() - new Date(artifact.constituents_as_of).getTime()) / (24 * 60 * 60 * 1000)
    : null;
  const constituentsStale = constituentsAgeDays != null && constituentsAgeDays > 3;

  return (
    <div className="space-y-2">
      {artifact.spot_error ? (
        <div className="rounded-lg border border-red-500/35 bg-red-500/10 px-3 py-2 text-[11px] text-red-800 dark:text-red-300">
          Live Nifty spot unavailable — {artifact.spot_error}. Re-login INDmoney in{" "}
          <a
            href={tradeUiDeepLink("openalgo")}
            target="_blank"
            rel="noreferrer"
            className="font-medium underline underline-offset-2"
          >
            OpenAlgo
          </a>{" "}
          then run analysis again.
        </div>
      ) : null}
      {artifact.warnings?.length ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          <p className="font-semibold">Run completed with warnings:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {artifact.warnings.map((warning, idx) => (
              <li key={idx}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {artifact.as_of ? (
        <p className="text-[10px] text-muted-foreground">
          Pipeline snapshot {new Date(artifact.as_of).toLocaleString()}
          {artifact.spot_source ? ` · spot source: ${artifact.spot_source}` : ""}
          {artifact.constituents_as_of ? (
            <span className={constituentsStale ? "text-amber-700 dark:text-amber-400" : undefined}>
              {" · constituents as of "}
              {new Date(artifact.constituents_as_of).toLocaleString()}
              {constituentsStale
                ? ` (${Math.floor(constituentsAgeDays!)}d old — refresh for current signals)`
                : ""}
            </span>
          ) : null}
        </p>
      ) : null}
      {pred.reconciled_with_scenarios &&
      pred.raw_expected_return_pct != null &&
      pred.scenario_anchor_return_pct != null ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          Headline blended with event scenarios (25% Ridge model · 75% scenario anchor). Ridge raw{" "}
          {fmtPct(pred.raw_expected_return_pct)} → anchor {fmtPct(pred.scenario_anchor_return_pct)} → final{" "}
          {fmtPct(pred.expected_return_pct)}. Macro attribution reflects the reconciled headline.
        </div>
      ) : null}
      {pred.debate_merged && pred.debate?.expected_return_pct != null ? (
        <div className="rounded-lg border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-[11px] text-violet-950 dark:text-violet-200" data-testid="debate-blend">
          Headline includes agent debate blend (60% debate · 40% quant). The debate{" "}
          {pred.provenance?.debate_as_of ? `of ${String(pred.provenance.debate_as_of).slice(0, 10)} ` : ""}stated{" "}
          {fmtPct(pred.debate.expected_return_pct)}
          {pred.debate.view ? ` (view ${pred.debate.view})` : ""}. Quant-only return was{" "}
          {fmtPct(pred.quant?.expected_return_pct)}; displayed {fmtPct(pred.expected_return_pct)}. Equation
          card shows quant macro coefficients.
        </div>
      ) : pred.debate_merged && !pred.debate ? (
        <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground" data-testid="debate-unrecorded">
          An agent debate was merged into this headline; this artifact does not record its figures (run analysis again to see them).
        </div>
      ) : pred.debate_merged ? (
        <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground" data-testid="debate-no-figure">
          An agent debate was read{pred.provenance?.debate_as_of ? ` (${String(pred.provenance.debate_as_of).slice(0, 10)})` : ""}
          {pred.debate?.view ? `, view ${pred.debate.view}` : ""}, but it stated no return figure: the headline return is the quant value
          {pred.provenance?.direction === "debate" ? " (its confidence blends into the direction)" : ""}.
        </div>
      ) : null}
      {pred.model_missing_features?.length ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200" data-testid="missing-features">
          {pred.model_missing_features.length} model input{pred.model_missing_features.length === 1 ? " was" : "s were"} missing from this
          snapshot and read at the training mean (no effect from them):{" "}
          {pred.model_missing_features.join(", ")}.
        </div>
      ) : null}
      {lowMomentum ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-[11px] text-red-800 dark:text-red-300">
          Bottom-up momentum missing for {momentumCov?.with_momentum ?? 0}/{momentumCov?.total ?? 0}{" "}
          constituents — forecast leans on sentiment only. Run full analysis to refresh price momentum.
        </div>
      ) : null}
      {pred.sign_conflict ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          Macro Ridge and scenario anchor disagree on direction — forecast held neutral with reduced confidence.
        </div>
      ) : null}
      {pred.data_quality_warning?.message ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          {pred.data_quality_warning.message}
          {pred.data_quality_warning.min_pct != null
            ? ` (${pred.data_quality_warning.min_pct}% vs ${pred.data_quality_warning.threshold_pct ?? 90}% gate).`
            : "."}
          {pred.data_quality_warning.macro_trust_multiplier != null
            ? ` Macro Ridge weight ×${pred.data_quality_warning.macro_trust_multiplier}.`
            : null}
        </div>
      ) : null}
      {pred.flow_coverage && !pred.data_quality_warning?.message ? (
        <div
          className={
            pred.flow_coverage.passes_gate
              ? "rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground"
              : "rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200"
          }
        >
          FII/DII/PCR flow coverage{" "}
          {pred.flow_coverage.min_pct != null ? `${pred.flow_coverage.min_pct}%` : "—"}
          {pred.flow_coverage.passes_gate ? " — gate pass (full macro trust)." : " — below gate."}
        </div>
      ) : null}
      {pred.cause_stress_index != null &&
      Number(pred.cause_stress_index) >= 60 &&
      (pred.cause_stress_label === "elevated" || pred.cause_stress_label === "event_driven") ? (
        <div className="rounded-lg border border-orange-500/35 bg-orange-500/10 px-3 py-2 text-[11px] text-orange-950 dark:text-orange-200">
          Cause stress elevated ({Math.round(Number(pred.cause_stress_index))}/100
          {pred.cause_stress_label ? ` · ${String(pred.cause_stress_label).replace(/_/g, " ")}` : ""}). News/vol
          regime may invalidate a stale forecast — run full analysis or check Track Scoreboard before acting.
        </div>
      ) : null}
      {(lowDirectionConf || directionReturnConflict) && !pred.sign_conflict ? (
        <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          Calibrated direction confidence is modest (
          {directionConf != null ? `${Math.round(Number(directionConf) * 100)}%` : "—"}) — raw model score{" "}
          {modelDirectionScore != null ? `${Math.round(Number(modelDirectionScore) * 100)}%` : "—"} is capped to
          walk-forward OOS hit rate ({hitRate != null ? `${Math.round(Number(hitRate) * 100)}%` : "—"}).
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
        <StatCard
        label={`${horizonDays}d target (Nifty)`}
        value={fmtNum(targetLevel)}
        sub={`${fmtPct(expected)} from spot ${fmtNum(spot)}`}
        tone={viewTone(view)}
        flash={flashReturn}
      />
      <StatCard
        label="Expected range"
        value={`${fmtNum(range.low)} – ${fmtNum(range.high)}`}
        sub={`Index points at horizon · ${bandSourceLabel(range)}`}
      />
      <StatCard
        label="Model direction score"
        value={
          modelDirectionScore != null && Number.isFinite(Number(modelDirectionScore))
            ? `${Math.round(Number(modelDirectionScore) * 100)}%`
            : "—"
        }
        sub="Raw Ridge logit before OOS calibration"
      />
      <StatCard
        label="Direction confidence"
        value={
          directionConf != null && Number.isFinite(Number(directionConf))
            ? `${Math.round(Number(directionConf) * 100)}%`
            : "—"
        }
        sub={
          pred.direction_view
            ? `View ${String(pred.direction_view)} · calibrated to OOS`
            : "Calibrated to walk-forward OOS"
        }
      />
      <StatCard
        label="Range band (± half-width)"
        value={halfWidth != null && Number.isFinite(Number(halfWidth)) ? `±${Number(halfWidth).toFixed(2)}%` : "—"}
        sub={
          pred.bottom_up_return_pct != null || pred.macro_delta_pct != null
            ? `Bottom-up ${fmtPct(pred.bottom_up_return_pct)} · Macro ${fmtPct(pred.macro_delta_pct)}`
            : undefined
        }
      />
      <StatCard
        label="Direction accuracy (OOS)"
        value={hitRate != null && Number.isFinite(Number(hitRate)) ? `${Math.round(Number(hitRate) * 100)}%` : "—"}
        sub={accuracySub}
      />
      <StatCard
        label="Regime"
        value={String(regime.label || regime.regime || "—").replace(/_/g, " ")}
        sub={regimeSub || undefined}
        tone={viewTone(String(regime.label || ""))}
        />
      </div>
    </div>
  );
}
