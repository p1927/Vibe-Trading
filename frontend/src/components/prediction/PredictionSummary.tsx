import { cn } from "@/lib/utils";
import type { IndexPredictionArtifact } from "@/lib/api";

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
  const conf = range.confidence ?? pred.confidence;
  const hitRate = accuracy.direction_hit_rate_14d ?? accuracy.direction_hit_rate;
  const mae = accuracy.mae_14d_pct ?? accuracy.mae_pct;
  const sampleCount = accuracy.sample_count ?? 0;
  const spot = artifact.spot;
  const expected = pred.expected_return_pct;
  const targetLevel =
    spot != null && expected != null && Number.isFinite(spot) && Number.isFinite(expected)
      ? spot * (1 + expected / 100)
      : null;

  const accuracySub =
    sampleCount > 0
      ? `${sampleCount} reconciled · MAE ${mae != null ? `${mae.toFixed(2)}%` : "—"}`
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
    (momentumCov.coverage_pct ?? 0) < 50;

  return (
    <div className="space-y-2">
      {pred.reconciled_with_scenarios &&
      pred.raw_expected_return_pct != null &&
      pred.scenario_anchor_return_pct != null ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          Headline blended with event scenarios (25% Ridge model · 75% scenario anchor). Ridge raw{" "}
          {fmtPct(pred.raw_expected_return_pct)} → anchor {fmtPct(pred.scenario_anchor_return_pct)} → final{" "}
          {fmtPct(pred.expected_return_pct)}. Macro attribution reflects the reconciled headline.
        </div>
      ) : null}
      {lowMomentum ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-[11px] text-red-800 dark:text-red-300">
          Bottom-up momentum missing for {momentumCov?.with_momentum ?? 0}/{momentumCov?.total ?? 0}{" "}
          constituents — forecast leans on sentiment only. Run full analysis to refresh price momentum.
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
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
        sub="Index points at horizon"
      />
      <StatCard
        label="Confidence"
        value={conf != null && Number.isFinite(Number(conf)) ? `${Math.round(Number(conf) * (Number(conf) <= 1 ? 100 : 1))}%` : "—"}
        sub={
          pred.bottom_up_return_pct != null || pred.macro_delta_pct != null
            ? `Bottom-up ${fmtPct(pred.bottom_up_return_pct)} · Macro ${fmtPct(pred.macro_delta_pct)}`
            : undefined
        }
      />
      <StatCard
        label="Direction accuracy"
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
