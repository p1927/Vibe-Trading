import type { IndexAccuracy, IndexPredictionArtifact, IndexPredictionHistoryRow } from "@/lib/api";

interface Props {
  artifact: IndexPredictionArtifact;
  history: IndexPredictionHistoryRow[];
}

export function PredictionLearningPanel({ artifact, history }: Props) {
  const accuracy = (artifact.accuracy || {}) as IndexAccuracy;
  const eq = artifact.prediction?.equation;
  const sampleCount = accuracy.sample_count ?? 0;
  const hitRate = accuracy.direction_hit_rate_14d ?? accuracy.direction_hit_rate;
  const mae = accuracy.mae_14d_pct ?? accuracy.mae_pct;

  const totalForecasts = history.length;
  const matured = history.filter((r) => r.actual_return_pct != null).length;

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Learning from history
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="text-[11px]">
          <span className="text-muted-foreground">Reconciled forecasts</span>
          <p className="text-lg font-semibold tabular-nums">{sampleCount}</p>
          <p className="text-[10px] text-muted-foreground">
            {matured} of {totalForecasts} in ledger matured
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Direction hit rate (14d)</span>
          <p className="text-lg font-semibold tabular-nums">
            {hitRate != null ? `${Math.round(hitRate * 100)}%` : "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">MAE (14d)</span>
          <p className="text-lg font-semibold tabular-nums">
            {mae != null ? `${mae.toFixed(2)}%` : "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Macro model</span>
          <p className="text-lg font-semibold tabular-nums">
            {eq?.coefficients ? Object.keys(eq.coefficients).length : 0} terms
          </p>
          <p className="text-[10px] text-muted-foreground">
            R² {eq?.r2_walk_forward != null ? eq.r2_walk_forward.toFixed(3) : "—"}
            {accuracy.retrained ? " · retrained" : ""}
          </p>
        </div>
      </div>
      {sampleCount === 0 ? (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Forecasts need to reach their horizon before direction accuracy populates. Nightly calibration
          reconciles ledger rows automatically.
        </p>
      ) : null}
      {totalForecasts > 0 ? (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary/70"
              style={{ width: `${totalForecasts ? (matured / totalForecasts) * 100 : 0}%` }}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
