import type { IndexAccuracy, IndexPredictionArtifact, IndexPredictionHistoryRow } from "@/lib/api";

interface Props {
  artifact: IndexPredictionArtifact;
  history: IndexPredictionHistoryRow[];
}

export function PredictionLearningPanel({ artifact, history }: Props) {
  const accuracy = (artifact.accuracy || {}) as IndexAccuracy;
  const eq = artifact.prediction?.equation;
  const newsCal = artifact.news_shock_calibration;
  const overlay = artifact.event_overlay;
  const sampleCount = accuracy.sample_count ?? 0;
  const hitRate =
    accuracy.direction_hit_rate_walk_forward ??
    accuracy.direction_hit_rate_14d ??
    accuracy.direction_hit_rate;
  const mae = accuracy.mae_14d_pct ?? accuracy.mae_pct;

  const totalForecasts = history.length;
  const matured = history.filter((r) => r.actual_return_pct != null).length;
  const newsFeatureStatus = newsCal?.news_event_features_status ?? "pending";
  const overlayStatus = newsCal?.news_event_overlay_status ?? "pending";
  const reconciledStories = newsCal?.reconciled_total ?? 0;
  const activeTopics = overlay?.active_topics ?? [];

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

      <div className="mt-4 grid gap-3 border-t pt-3 sm:grid-cols-3">
        <div className="text-[11px]">
          <span className="text-muted-foreground">News Ridge block</span>
          <p className="font-medium capitalize tabular-nums">{newsFeatureStatus}</p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Event overlay</span>
          <p className="font-medium capitalize tabular-nums">{overlayStatus}</p>
          {overlay?.return_pct != null && Math.abs(overlay.return_pct) > 0.001 ? (
            <p className="text-[10px] text-muted-foreground">
              Active now: {overlay.return_pct > 0 ? "+" : ""}
              {overlay.return_pct.toFixed(2)}%
            </p>
          ) : null}
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Reconciled news stories</span>
          <p className="font-medium tabular-nums">{reconciledStories}</p>
          {activeTopics.length ? (
            <p className="text-[10px] text-muted-foreground">
              Topics: {activeTopics.map((t) => t.topic).filter(Boolean).join(", ")}
            </p>
          ) : null}
        </div>
      </div>

      {sampleCount === 0 ? (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Forecasts need to reach their horizon before direction accuracy populates. Nightly calibration
          reconciles ledger rows and news shock tables automatically.
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
