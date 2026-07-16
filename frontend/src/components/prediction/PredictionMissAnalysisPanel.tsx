import { Fragment, useState } from "react";
import type { IndexMissAnalysisReport } from "@/lib/api";
import { DayMoveCauses } from "@/components/prediction/DayMoveCauses";

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

interface Props {
  report: IndexMissAnalysisReport | null;
  loading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
  highlightDate?: string | null;
}

export function PredictionMissAnalysisPanel({
  report,
  loading,
  error,
  onRefresh,
  highlightDate,
}: Props) {
  const [expandedDate, setExpandedDate] = useState<string | null>(highlightDate ?? null);

  if (loading && !report) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground">
        Analyzing wrong predictions (T0 vs maturity factor diff)…
      </div>
    );
  }

  if (!report) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground">
        {error ? (
          <span className="text-amber-700 dark:text-amber-400">{error}</span>
        ) : (
          "Miss analysis not available yet."
        )}
      </div>
    );
  }

  const summary = report.summary ?? {};
  const misses = report.misses ?? [];
  const patterns = summary.top_miss_patterns ?? [];
  const categories = summary.miss_categories ?? {};

  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Prediction miss root-cause
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Compares macro factors at prediction date (T0) vs maturity (T0+{report.horizon_days ?? 14}d) for
            every direction miss — learn what changed while the forecast was live.
          </p>
        </div>
        {onRefresh ? (
          <button
            type="button"
            onClick={onRefresh}
            className="rounded-md border border-border/60 px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted/50"
          >
            Re-run analysis
          </button>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="text-[11px]">
          <span className="text-muted-foreground">Direction hit rate</span>
          <p className="text-lg font-semibold tabular-nums">
            {summary.direction_hit_rate != null
              ? `${Math.round(summary.direction_hit_rate * 100)}%`
              : "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Misses</span>
          <p className="text-lg font-semibold tabular-nums">
            {summary.miss_count ?? misses.length} / {report.eval_count ?? "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">MAE</span>
          <p className="text-lg font-semibold tabular-nums">{fmtPct(summary.mae_pct)}</p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Top miss type</span>
          <p className="text-lg font-semibold">
            {patterns[0]?.category?.replace(/_/g, " ") ?? "—"}
          </p>
        </div>
      </div>

      {Object.keys(categories).length ? (
        <div className="flex flex-wrap gap-2">
          {Object.entries(categories).map(([cat, count]) => (
            <span
              key={cat}
              className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[10px] tabular-nums"
            >
              {cat.replace(/_/g, " ")}: {count}
            </span>
          ))}
        </div>
      ) : null}

      {patterns.length ? (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px]">
          <p className="font-medium text-amber-900 dark:text-amber-200">Systematic patterns</p>
          <ul className="mt-1 space-y-1 text-muted-foreground">
            {patterns.map((p) => (
              <li key={p.category}>
                <span className="font-medium text-foreground">{p.category?.replace(/_/g, " ")}</span> ({p.count}
                ×): {p.action}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {misses.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="py-1.5 pr-3">T0</th>
                <th className="py-1.5 pr-3">Maturity</th>
                <th className="py-1.5 pr-3">Predicted</th>
                <th className="py-1.5 pr-3">Actual</th>
                <th className="py-1.5 pr-3">Category</th>
              </tr>
            </thead>
            <tbody>
              {misses.map((row) => {
                const key = row.prediction_date ?? "";
                const open = expandedDate === key;
                const highlighted = highlightDate === key;
                return (
                  <Fragment key={key}>
                    <tr
                      className={`cursor-pointer border-b border-border/40 hover:bg-muted/30 ${
                        highlighted ? "bg-red-500/10" : ""
                      }`}
                      onClick={() => setExpandedDate(open ? null : key)}
                    >
                      <td className="py-1.5 pr-3 tabular-nums">{row.prediction_date}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{row.maturity_date}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtPct(row.predicted_return_pct)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtPct(row.actual_return_pct)}</td>
                      <td className="py-1.5">{row.miss_category?.replace(/_/g, " ") ?? "—"}</td>
                    </tr>
                    {open ? (
                      <tr className="border-b border-border/40 bg-muted/20">
                        <td colSpan={5} className="px-2 py-2">
                          {row.learning_note ? (
                            <p className="mb-2 text-[11px] text-foreground">{row.learning_note}</p>
                          ) : null}
                          <div className="grid gap-3 md:grid-cols-2">
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">
                                Factor drift T0 → maturity
                              </p>
                              <ul className="space-y-0.5">
                                {(row.factor_delta_horizon ?? []).map((d) => (
                                  <li key={d.factor}>
                                    {d.label}: {d.t0} → {d.t1} (Δ {fmtPct(d.delta)})
                                  </li>
                                ))}
                              </ul>
                            </div>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">Headlines at maturity</p>
                              {(row.headlines_at_maturity ?? []).length ? (
                                <ul className="space-y-0.5 text-muted-foreground">
                                  {row.headlines_at_maturity!.map((h) => (
                                    <li key={h.title}>• {h.title}</li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="text-muted-foreground">No headlines fetched</p>
                              )}
                            </div>
                          </div>
                          {(row.causal_hypotheses ?? []).length ? (
                            <div className="mt-3">
                              <DayMoveCauses causalHypotheses={row.causal_hypotheses} compact />
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground">No direction misses in the current eval window.</p>
      )}
    </div>
  );
}
