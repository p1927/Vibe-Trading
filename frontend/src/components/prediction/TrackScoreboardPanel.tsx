import { FlaskConical, Loader2, PanelRight } from "lucide-react";
import { useState } from "react";
import type { IndexTrackScoreboardReport } from "@/lib/api";
import { fmtHitRate, fmtPct } from "@/lib/trackScoreboardUtils";
import {
  BACKTEST_COMBINER_IDS,
  CANONICAL_TRACK_IDS,
  EXPERIMENTAL_TRACK_IDS,
  trackDisplayLabel,
} from "@/lib/trackScoreboardReplayUtils";
import { TrackScoreboardChart } from "@/components/charts/TrackScoreboardChart";
import { TrackScoreboardEvalPanel } from "@/components/prediction/TrackScoreboardEvalPanel";
import { TrackScoreboardReplaySection } from "@/components/prediction/TrackScoreboardReplaySection";
import { cn } from "@/lib/utils";

interface Props {
  report: IndexTrackScoreboardReport | null;
  loading?: boolean;
  recomputing?: boolean;
  error?: string | null;
  onRefresh?: () => void;
  onRunForecastLab?: () => void;
  forecastLabLoading?: boolean;
  horizonDays?: number;
  onHorizonChange?: (days: number) => void;
  progressPanelOpen?: boolean;
  onToggleProgressPanel?: () => void;
}

export function TrackScoreboardPanel({
  report,
  loading,
  recomputing,
  error,
  onRefresh,
  onRunForecastLab,
  forecastLabLoading,
  horizonDays = 14,
  onHorizonChange,
  progressPanelOpen,
  onToggleProgressPanel,
}: Props) {
  const [showCombiners, setShowCombiners] = useState(false);

  const promo = report?.promotion;
  const insufficient = promo?.auto_promote_allowed === false;
  const evalCount = report?.eval_count ?? promo?.eval_count ?? 0;
  const showInitialLoad = loading && !report;
  const showStaleWhileRefresh = recomputing && Boolean(report);

  if (showInitialLoad) {
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4 shadow-sm">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              Forecast track lab — scoreboard
            </p>
            <p className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
              Loading cached scoreboard, then running walk-forward if needed…
            </p>
          </div>
          {onToggleProgressPanel ? (
            <button
              type="button"
              onClick={onToggleProgressPanel}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[11px] font-medium",
                progressPanelOpen ? "border-primary/40 bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted/50",
              )}
            >
              <PanelRight className="h-3.5 w-3.5" />
              Activity
            </button>
          ) : null}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="h-64 animate-pulse rounded-xl border bg-muted/30 lg:col-span-2" />
          <div className="h-48 animate-pulse rounded-xl border bg-muted/20" />
          <div className="h-48 animate-pulse rounded-xl border bg-muted/20" />
        </div>
        <p className="text-center text-[11px] text-muted-foreground">
          See the <span className="font-medium text-foreground">Scoreboard activity</span> panel on the right for
          live progress (2–3 min for a full recompute).
        </p>
      </div>
    );
  }

  if (!report || report.status === "error") {
    return (
      <div className="rounded-xl border bg-card p-6 text-[12px] text-muted-foreground">
        {error ?? report?.message ?? "Track scoreboard unavailable — recompute or run index research first."}
      </div>
    );
  }

  const tracks = CANONICAL_TRACK_IDS.map((tid) => [tid, report.tracks?.[tid] ?? { track_id: tid, eval_count: 0 }] as const);
  const experimentalTracks = EXPERIMENTAL_TRACK_IDS.map(
    (tid) => [tid, report.tracks?.[tid] ?? { track_id: tid, eval_count: 0, experimental: true }] as const,
  );
  const combiners = BACKTEST_COMBINER_IDS.map((cid) => [cid, report.combiners?.[cid] ?? { track_id: cid, eval_count: 0 }] as const);
  const live = report.live;
  const eventOverlayUnavailable =
    report.tracks?.event_overlay?.backtest_eligible !== false &&
    (report.tracks?.event_overlay?.eval_count ?? 0) === 0 &&
    (report.limitations ?? []).some((line) => line.includes("event_overlay"));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border bg-card p-4 shadow-sm">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Forecast track lab — scoreboard
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {report.history_start} → {report.history_end} · {evalCount} OOS eval dates · {horizonDays}d horizon
            {report.history_rows ? ` · ${report.history_rows} history rows` : ""}
            {report.hybrid_eval_count != null
              ? ` · ${report.hybrid_eval_count} hybrid constituent days`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
        {onToggleProgressPanel ? (
          <button
            type="button"
            onClick={onToggleProgressPanel}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[11px] font-medium",
              progressPanelOpen ? "border-primary/40 bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted/50",
            )}
          >
            <PanelRight className="h-3.5 w-3.5" />
            Activity
          </button>
        ) : null}
        {onRunForecastLab ? (
          <button
            type="button"
            onClick={onRunForecastLab}
            disabled={forecastLabLoading || loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-[11px] font-medium text-muted-foreground hover:bg-muted/50 disabled:opacity-50"
            title="Run forecast lab tracks for today's prediction (manual — not part of Run analysis)"
          >
            {forecastLabLoading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <FlaskConical className="h-3.5 w-3.5" />
            )}
            {forecastLabLoading ? "Forecast lab…" : "Run forecast lab"}
          </button>
        ) : null}
        {onRefresh ? (
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading || forecastLabLoading}
            className="rounded-md border border-border/60 px-3 py-1.5 text-[11px] font-medium text-muted-foreground hover:bg-muted/50 disabled:opacity-50"
          >
            {loading ? "Recomputing…" : "Recompute scoreboard"}
          </button>
        ) : null}
        {onHorizonChange ? (
          <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
            Horizon
            <select
              value={horizonDays}
              onChange={(e) => onHorizonChange(Number(e.target.value))}
              className="rounded-md border border-border/60 bg-background px-2 py-1 text-[11px]"
            >
              {[7, 14, 21, 30].map((d) => (
                <option key={d} value={d}>
                  {d}d
                </option>
              ))}
            </select>
          </label>
        ) : null}
        </div>
      </div>

      {showStaleWhileRefresh ? (
        <div className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-[11px] text-foreground">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
          Refreshing walk-forward scoreboard ({horizonDays}d horizon, 730d history) — showing last cached results
          until the new run finishes.
        </div>
      ) : null}

      {insufficient ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          Direction skill is informational only — {evalCount} eval rows (need ≥
          {promo?.min_eval_count_required ?? 60} for auto-promotion). Headline stays quant_only until gates pass.
        </div>
      ) : null}

      {eventOverlayUnavailable ? (
        <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-[11px] text-blue-950 dark:text-blue-100">
          <p className="font-medium">News shock track ({trackDisplayLabel("event_overlay")}) needs historical data</p>
          <p className="mt-1 text-muted-foreground">
            Backfill 2006+ macro, flows, and news factors, then rebuild shock calibration:
            {" "}
            <code className="rounded bg-muted/60 px-1 py-0.5 text-[10px]">
              python scripts/backfill_historical_macro.py --start 2006-01-01
            </code>
            {" → "}
            <code className="rounded bg-muted/60 px-1 py-0.5 text-[10px]">
              python scripts/backfill_historical_flows.py
            </code>
            {" → "}
            <code className="rounded bg-muted/60 px-1 py-0.5 text-[10px]">
              python scripts/backfill_historical_news.py
            </code>
            {" → "}
            <code className="rounded bg-muted/60 px-1 py-0.5 text-[10px]">
              python scripts/rebuild_news_shock_calibration.py
            </code>
          </p>
        </div>
      ) : null}

      {(report.limitations?.length ?? 0) > 0 ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          {report.limitations!.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      ) : null}

      {report.live_enrichment_error ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          Live track snapshot unavailable: {report.live_enrichment_error}
        </div>
      ) : null}

      {report.live_enrichment_note ? (
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
          {report.live_enrichment_note} — run index analysis with INDEX_PREDICTION_LAB_ENABLED=1 for live tracks.
        </div>
      ) : null}

      <TrackScoreboardReplaySection report={report} horizonDays={horizonDays} />

      <TrackScoreboardEvalPanel report={report} horizonDays={horizonDays} />

      <div className="rounded-xl border bg-card p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold">Return % scoreboard (all tracks)</p>
            <p className="text-[10px] text-muted-foreground">
              TradingView lightweight-charts — scroll/wheel to zoom · In/Out/Fit on each chart. OOS predicted vs
              realised {horizonDays}d return at each eval date.
            </p>
          </div>
          <label className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <input
              type="checkbox"
              checked={showCombiners}
              onChange={(e) => setShowCombiners(e.target.checked)}
              className="rounded border-border"
            />
            Show combiners
          </label>
        </div>
        <TrackScoreboardChart chart={report.chart} showCombiners={showCombiners} height={320} />
      </div>

      {live?.forecast_tracks ? (
        <div className="rounded-xl border bg-card p-4 shadow-sm">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Live track snapshot
          </p>
          <p className="mt-1 text-[10px] text-muted-foreground">
            As of {String(live.as_of ?? "").slice(0, 16)}
            {live.spot != null ? ` · spot ${Number(live.spot).toLocaleString("en-IN")}` : ""}
            {live.cause_stress_index != null
              ? ` · cause stress ${live.cause_stress_index} (${live.cause_stress_label ?? "—"})`
              : ""}
            {live.active_combiner ? ` · combiner ${live.active_combiner}` : ""}
            {live.headline_source ? ` · headline ${live.headline_source}` : ""}
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(live.forecast_tracks).map(([tid, row]) => {
              const r = row as { expected_return_pct?: number; view?: string; available?: boolean };
              const unavailable = r.available === false;
              return (
                <div
                  key={tid}
                  className={cn(
                    "rounded-lg border border-border/50 px-3 py-2 text-[11px]",
                    unavailable && "opacity-50",
                  )}
                >
                  <p className="font-medium capitalize">
                    {tid.replace(/_/g, " ")}
                    {unavailable ? (
                      <span className="ml-1 text-[9px] font-normal text-muted-foreground">(unavailable)</span>
                    ) : null}
                  </p>
                  <p className="tabular-nums text-base font-semibold">
                    {unavailable ? "—" : fmtPct(r.expected_return_pct)}
                  </p>
                  <p className="text-muted-foreground">{unavailable ? "—" : r.view ?? "—"}</p>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {report.track_catalog ? (
        <div className="rounded-xl border bg-card p-4 shadow-sm">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Track catalog
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {CANONICAL_TRACK_IDS.map((tid) => {
              const cat = report.track_catalog?.[tid];
              if (!cat?.implementation) return null;
              return (
                <div key={tid} className="rounded-lg border border-border/40 px-3 py-2 text-[10px]">
                  <p className="font-medium capitalize">{tid.replace(/_/g, " ")}</p>
                  <p className="text-muted-foreground">{cat.implementation}</p>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border bg-card p-4 shadow-sm lg:col-span-2">
          <p className="mb-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Track implementations ({CANONICAL_TRACK_IDS.length} live · {BACKTEST_COMBINER_IDS.length} combiners)
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {CANONICAL_TRACK_IDS.map((tid) => {
              const cat = report.track_catalog?.[tid];
              const row = report.tracks?.[tid];
              return (
                <div key={tid} className="rounded-lg border border-border/50 px-3 py-2 text-[10px]">
                  <p className="font-medium capitalize">{tid.replace(/_/g, " ")}</p>
                  <p className="text-muted-foreground">{cat?.implementation ?? "—"}</p>
                  <p className="mt-1 tabular-nums">
                    OOS n={row?.eval_count ?? 0}
                    {tid === "debate_numeric" ? " · live hub only" : ""}
                  </p>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-xl border bg-card p-4 shadow-sm">
          <p className="mb-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Track metrics (OOS)
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="py-1 pr-2">Track</th>
                  <th className="py-1 pr-2">MAE</th>
                  <th className="py-1 pr-2">Direction</th>
                  <th className="py-1 pr-2">Hits</th>
                  <th className="py-1 pr-2">Misses</th>
                  <th className="py-1">n</th>
                </tr>
              </thead>
              <tbody>
                {tracks.map(([tid, row]) => (
                  <tr key={tid} className="border-b border-border/40">
                    <td className="py-1.5 pr-2 font-medium">
                      {tid.replace(/_/g, " ")}
                      {tid === "debate_numeric" ? (
                        <span className="ml-1 text-[9px] text-muted-foreground">(live only)</span>
                      ) : row.backtest_eligible === false ? (
                        <span className="ml-1 text-[9px] text-muted-foreground">(no backtest)</span>
                      ) : null}
                    </td>
                    <td className="py-1.5 pr-2 tabular-nums">{fmtPct(row.mae_pct)}</td>
                    <td className="py-1.5 pr-2 tabular-nums">{fmtHitRate(row.direction_hit_rate)}</td>
                    <td className="py-1.5 pr-2 tabular-nums text-emerald-600 dark:text-emerald-400">
                      {row.direction_hit_count ?? "—"}
                    </td>
                    <td className="py-1.5 pr-2 tabular-nums text-red-600 dark:text-red-400">
                      {row.direction_miss_count ?? "—"}
                    </td>
                    <td className="py-1.5 tabular-nums">{row.eval_count ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-xl border bg-card p-4 shadow-sm">
          <p className="mb-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Combiner promotion
          </p>
          <p className="mb-2 text-[11px] text-muted-foreground">
            Quant baseline direction: {fmtHitRate(promo?.quant_direction_hit_rate)}
            {promo?.quant_mae_pct != null ? ` · MAE ${fmtPct(promo.quant_mae_pct)}` : ""}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="py-1 pr-2">Combiner</th>
                  <th className="py-1 pr-2">Dir%</th>
                  <th className="py-1 pr-2">Δ vs quant</th>
                  <th className="py-1">Promote</th>
                </tr>
              </thead>
              <tbody>
                {combiners.map(([cid, row]) => {
                  const verdict = promo?.verdicts?.[cid];
                  return (
                    <tr key={cid} className="border-b border-border/40">
                      <td className="py-1.5 pr-2">{cid.replace(/_/g, " ")}</td>
                      <td className="py-1.5 pr-2 tabular-nums">{fmtHitRate(row.direction_hit_rate)}</td>
                      <td className="py-1.5 pr-2 tabular-nums">
                        {verdict?.delta_vs_quant_pp != null ? `${verdict.delta_vs_quant_pp} pp` : "—"}
                      </td>
                      <td className="py-1.5">
                        {verdict?.promoted ? (
                          <span className="text-emerald-600 dark:text-emerald-400">yes</span>
                        ) : (
                          <span className="text-muted-foreground">
                            {verdict?.mae_passes === false ? "mae" : "no"}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {(promo?.promoted_combiners?.length ?? 0) > 0 ? (
            <p className="mt-2 text-[10px] text-emerald-700 dark:text-emerald-400">
              Promoted: {promo!.promoted_combiners!.join(", ")}
            </p>
          ) : (
            <p className="mt-2 text-[10px] text-muted-foreground">Report-only — headline stays quant_only.</p>
          )}
        </div>

        <div className="rounded-xl border bg-card p-4 shadow-sm lg:col-span-2">
          <p className="mb-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            ML experiment tracks (opt-in)
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="py-1 pr-2">Track</th>
                  <th className="py-1 pr-2">MAE</th>
                  <th className="py-1 pr-2">Direction</th>
                  <th className="py-1">n</th>
                </tr>
              </thead>
              <tbody>
                {experimentalTracks.map(([tid, row]) => (
                  <tr key={tid} className="border-b border-border/40">
                    <td className="py-1.5 pr-2 font-medium">
                      {trackDisplayLabel(tid)}
                      <span className="ml-1 text-[9px] text-muted-foreground">(ML)</span>
                    </td>
                    <td className="py-1.5 pr-2 tabular-nums">{fmtPct(row.mae_pct)}</td>
                    <td className="py-1.5 pr-2 tabular-nums">{fmtHitRate(row.direction_hit_rate)}</td>
                    <td className="py-1.5 tabular-nums">{row.eval_count ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground">
            ML tracks run in parallel with quant_ridge when libraries are installed. Disable with
            INDEX_PREDICTION_EXPERIMENTAL_TRACKS=0.
          </p>
        </div>
      </div>
    </div>
  );
}
