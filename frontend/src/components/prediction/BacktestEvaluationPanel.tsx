import { Fragment, useState } from "react";
import type { IndexBacktestReport } from "@/lib/api";
import { NiftyHistoricalChart } from "@/components/charts/NiftyHistoricalChart";
import { DayMoveCauses } from "@/components/prediction/DayMoveCauses";

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function fmtNum(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

interface Props {
  report: IndexBacktestReport | null;
  loading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
  onMissSelect?: (date: string) => void;
}

export function BacktestEvaluationPanel({ report, loading, error, onRefresh, onMissSelect }: Props) {
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const [expandedDrop, setExpandedDrop] = useState<string | null>(null);

  if (loading && !report) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground">
        Running historical backtest on past factor data…
      </div>
    );
  }

  if (!report) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground">
        {error ? (
          <span className="text-amber-700 dark:text-amber-400">{error}</span>
        ) : (
          "Historical backtest not available yet."
        )}
      </div>
    );
  }

  const metrics = report.metrics ?? {};
  const daily = [...(report.daily_evaluations ?? [])].reverse().slice(0, 30);
  const drawdowns = report.major_drawdowns ?? [];

  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Historical evaluation (walk-forward)
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {report.history_start} → {report.history_end} · {report.history_rows} trading rows ·{" "}
            {report.eval_count} out-of-sample predictions ({report.horizon_days}d horizon). Click the chart
            for ranked causes (FII outflows, oil, US risk, news) — not just post-event metrics.
          </p>
        </div>
        {onRefresh ? (
          <button
            type="button"
            onClick={onRefresh}
            className="rounded-md border border-border/60 px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted/50"
          >
            Re-run backtest
          </button>
        ) : null}
      </div>

      <div className="rounded-lg border border-violet-500/25 bg-violet-500/10 px-3 py-2 text-[11px] text-violet-900 dark:text-violet-200">
        Macro-only walk-forward — validates the Ridge overlay, not the live hybrid headline (bottom-up +
        scenario reconciliation). Compare to ledger accuracy once forecasts mature.
        {(report.limitations ?? [])[0] ? ` ${report.limitations![0]}` : ""}
      </div>

      <NiftyHistoricalChart
        series={report.nifty_series ?? []}
        majorDrawdowns={report.major_drawdowns}
        height={300}
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="text-[11px]">
          <span className="text-muted-foreground">Out-of-sample MAE</span>
          <p className="text-lg font-semibold tabular-nums">{fmtPct(metrics.mae_pct)}</p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Direction hit rate</span>
          <p className="text-lg font-semibold tabular-nums">
            {metrics.direction_hit_rate != null
              ? `${Math.round(metrics.direction_hit_rate * 100)}%`
              : "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">In-sample R² (train)</span>
          <p className="text-lg font-semibold tabular-nums">
            {metrics.in_sample_r2 != null ? metrics.in_sample_r2.toFixed(3) : "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Train MAE</span>
          <p className="text-lg font-semibold tabular-nums">{fmtPct(metrics.in_sample_mae_pct)}</p>
        </div>
      </div>

      {report.limitations?.length ? (
        <ul className="space-y-1 text-[11px] text-amber-700 dark:text-amber-400">
          {report.limitations.map((line) => (
            <li key={line}>• {line}</li>
          ))}
        </ul>
      ) : null}

      {report.factor_audit?.length ? (
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Factor data quality
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="py-1 pr-3">Factor</th>
                  <th className="py-1 pr-3">Coverage</th>
                  <th className="py-1">Note</th>
                </tr>
              </thead>
              <tbody>
                {report.factor_audit
                  .filter((f) => (f.coverage_pct ?? 0) > 0)
                  .slice(0, 12)
                  .map((f) => (
                    <tr key={f.factor} className="border-b border-border/40">
                      <td className="py-1 pr-3">{f.label ?? f.factor}</td>
                      <td className="py-1 pr-3 tabular-nums">{f.coverage_pct?.toFixed(0)}%</td>
                      <td className="py-1 text-muted-foreground">
                        {f.is_static ? "static value" : ""}
                        {f.in_macro_keys === false ? " not in model keys" : ""}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {drawdowns.length ? (
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Major Nifty drawdowns (1d ≤ −1%)
          </p>
          <p className="mb-2 text-[11px] text-muted-foreground">
            Largest single-day drops — expand for ranked causes (flows, crude, global risk, headlines).
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="py-1.5 pr-3">Date</th>
                  <th className="py-1.5 pr-3">Nifty</th>
                  <th className="py-1.5 pr-3">1d move</th>
                  <th className="py-1.5">Top factor moves</th>
                </tr>
              </thead>
              <tbody>
                {drawdowns.map((row) => {
                  const open = expandedDrop === row.date;
                  const topDrivers = (row.factor_drivers ?? []).slice(0, 3);
                  return (
                    <Fragment key={row.date}>
                      <tr
                        className="cursor-pointer border-b border-border/40 hover:bg-muted/30"
                        onClick={() => setExpandedDrop(open ? null : row.date ?? null)}
                      >
                        <td className="py-1.5 pr-3 tabular-nums">{row.date}</td>
                        <td className="py-1.5 pr-3 tabular-nums">{fmtNum(row.spot)}</td>
                        <td className="py-1.5 pr-3 tabular-nums text-red-600 dark:text-red-400">
                          {fmtPct(row.realized_1d_pct)}
                        </td>
                        <td className="py-1.5 text-muted-foreground">
                          {topDrivers.length
                            ? topDrivers
                                .map((d) => `${d.label}: ${fmtPct(d.change_pct)}`)
                                .join(" · ")
                            : "—"}
                        </td>
                      </tr>
                      {open ? (
                        <tr className="border-b border-border/40 bg-muted/20">
                          <td colSpan={4} className="px-2 py-2">
                            <DayMoveCauses causalHypotheses={row.causal_hypotheses} compact />
                            <div className="mt-3 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                              <div>
                                <p className="mb-1 font-medium text-muted-foreground">Factor moves (d/d)</p>
                                <ul className="space-y-0.5">
                                  {(row.factor_drivers ?? []).map((d) => (
                                    <li key={d.factor}>
                                      {d.label}: {d.prev} → {d.current} ({fmtPct(d.change_pct)})
                                    </li>
                                  ))}
                                </ul>
                              </div>
                              <div>
                                <p className="mb-1 font-medium text-muted-foreground">Worst index contributors</p>
                                {(row.worst_contributors ?? []).length ? (
                                  <ul className="space-y-1">
                                    {row.worst_contributors!.map((c) => (
                                      <li key={c.symbol}>
                                        <span className="font-medium">{c.symbol}</span>{" "}
                                        {fmtPct(c.return_1d_pct)} (wt {c.weight_pct?.toFixed(1)}% →{" "}
                                        {fmtPct(c.index_contribution_pct)} on index)
                                        {(c.headlines ?? []).length ? (
                                          <ul className="ml-2 mt-0.5 text-muted-foreground">
                                            {c.headlines!.map((h) => (
                                              <li key={h.title}>• {h.title}</li>
                                            ))}
                                          </ul>
                                        ) : null}
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p className="text-muted-foreground">Run news backfill for headline replay</p>
                                )}
                              </div>
                              <div>
                                <p className="mb-1 font-medium text-muted-foreground">Calendar / events</p>
                                {(row.calendar_events ?? []).length ? (
                                  <ul className="space-y-0.5">
                                    {row.calendar_events!.map((e) => (
                                      <li key={e.event}>{e.description}</li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p className="text-muted-foreground">No flagged calendar events</p>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Day-by-day attribution (recent eval dates)
        </p>
        <p className="mb-2 text-[11px] text-muted-foreground">
          Each row: model trained only on data before that date. Drivers = largest factor moves vs prior
          day. Calendar = scheduled market events. Company news replay requires daily research archives (not
          yet stored).
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="py-1.5 pr-3">Date</th>
                <th className="py-1.5 pr-3">Nifty</th>
                <th className="py-1.5 pr-3">Predicted {report.horizon_days}d</th>
                <th className="py-1.5 pr-3">Actual {report.horizon_days}d</th>
                <th className="py-1.5 pr-3">Error</th>
                <th className="py-1.5">Hit</th>
              </tr>
            </thead>
            <tbody>
              {daily.map((row) => {
                const open = expandedDate === row.date;
                return (
                  <Fragment key={row.date}>
                    <tr
                      className="cursor-pointer border-b border-border/40 hover:bg-muted/30"
                      onClick={() => setExpandedDate(open ? null : row.date ?? null)}
                    >
                      <td className="py-1.5 pr-3 tabular-nums">{row.date}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtNum(row.spot)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">
                        {fmtPct(row.predicted_return_pct)}
                        <span className="ml-1 text-muted-foreground">→ {fmtNum(row.implied_level)}</span>
                      </td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtPct(row.actual_forward_return_pct)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtPct(row.error_pct)}</td>
                      <td className="py-1.5">{row.direction_correct ? "✓" : "✗"}</td>
                    </tr>
                    {open ? (
                      <tr className="border-b border-border/40 bg-muted/20">
                        <td colSpan={6} className="px-2 py-2">
                          {!row.direction_correct && row.miss_category ? (
                            <div className="mb-2 rounded border border-red-500/20 bg-red-500/5 px-2 py-1.5 text-[11px]">
                              <span className="font-medium">Miss: {row.miss_category.replace(/_/g, " ")}</span>
                              {row.learning_note ? (
                                <p className="mt-0.5 text-muted-foreground">{row.learning_note}</p>
                              ) : null}
                              {onMissSelect && row.date ? (
                                <button
                                  type="button"
                                  className="mt-1 text-[10px] text-primary underline"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onMissSelect(row.date!);
                                  }}
                                >
                                  Open in miss analysis panel
                                </button>
                              ) : null}
                            </div>
                          ) : null}
                          <div className="grid gap-3 md:grid-cols-2">
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">Factor moves (d/d at T0)</p>
                              <ul className="space-y-0.5">
                                {(row.factor_drivers ?? []).map((d) => (
                                  <li key={d.factor}>
                                    {d.label}: {d.prev} → {d.current} ({fmtPct(d.change_pct)})
                                  </li>
                                ))}
                              </ul>
                            </div>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">
                                Horizon drift T0 → {row.maturity_date ?? "maturity"}
                              </p>
                              {(row.factor_delta_horizon ?? []).length ? (
                                <ul className="space-y-0.5">
                                  {row.factor_delta_horizon!.map((d) => (
                                    <li key={d.factor}>
                                      {d.label}: {d.t0} → {d.t1} (Δ {fmtPct(d.delta)})
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="text-muted-foreground">No horizon diff recorded</p>
                              )}
                            </div>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">Calendar / events</p>
                              {(row.calendar_events ?? []).length ? (
                                <ul className="space-y-0.5">
                                  {row.calendar_events!.map((e) => (
                                    <li key={e.event}>{e.description}</li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="text-muted-foreground">No flagged calendar events</p>
                              )}
                              <p className="mt-2 text-muted-foreground">
                                1d realized move: {fmtPct(row.realized_1d_pct)}
                              </p>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
