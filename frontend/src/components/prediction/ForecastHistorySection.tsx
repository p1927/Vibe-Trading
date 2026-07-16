import type { IndexPredictionHistoryMeta, IndexPredictionHistoryRow } from "@/lib/api";
import { IndexForecastLevelChart } from "@/components/charts/IndexForecastLevelChart";
import { IndexForecastReturnChart } from "@/components/charts/IndexForecastReturnChart";

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

function fmtLevel(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

interface Props {
  daily: IndexPredictionHistoryRow[];
  intraday: IndexPredictionHistoryRow[];
  meta?: IndexPredictionHistoryMeta;
  horizonDays?: number;
  onOpenCounterfactual?: () => void;
}

export function ForecastHistorySection({
  daily,
  intraday,
  meta,
  horizonDays = 14,
  onOpenCounterfactual,
}: Props) {
  const chartRows = daily.length ? daily : intraday.slice(0, 1);
  const revisions = meta?.intraday_revisions ?? intraday.length;
  const uniqueDays = meta?.unique_days ?? daily.length;

  return (
    <div className="space-y-3">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Forecast history
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {uniqueDays < 2
            ? `${uniqueDays} calendar day in ledger — charts show today's forecast; more daily points accumulate after each Run analysis on different days.`
            : `${uniqueDays} days of ${horizonDays}d-horizon forecasts.`}
          {revisions > 1 ? ` ${revisions} intraday refreshes logged for today.` : ""}
          {onOpenCounterfactual ? (
            <>
              {" "}
              <button
                type="button"
                onClick={onOpenCounterfactual}
                className="text-primary underline-offset-2 hover:underline"
              >
                View counterfactual decomposition
              </button>{" "}
              for matured misses.
            </>
          ) : null}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <IndexForecastLevelChart rows={chartRows} horizonDays={horizonDays} height={240} />
        <IndexForecastReturnChart rows={chartRows} height={240} />
      </div>

      {daily.length >= 1 ? (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b bg-muted/30 text-[10px] uppercase tracking-wide text-muted-foreground">
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2">Spot</th>
                <th className="px-3 py-2">{horizonDays}d target</th>
                <th className="px-3 py-2">Return</th>
                <th className="px-3 py-2">Bottom-up</th>
                <th className="px-3 py-2">Macro</th>
                <th className="px-3 py-2">Actual</th>
              </tr>
            </thead>
            <tbody>
              {[...daily].reverse().map((row) => (
                <tr key={row.predicted_at} className="border-b border-border/40">
                  <td className="px-3 py-2 tabular-nums">{row.predicted_at.slice(0, 10)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmtLevel(row.spot_at_prediction)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmtLevel(row.implied_level)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmtPct(row.expected_return_pct)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmtPct(row.bottom_up_return_pct)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmtPct(row.macro_delta_pct)}</td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.actual_return_pct != null ? fmtPct(row.actual_return_pct) : "pending"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {revisions > 0 && intraday.length > 0 ? (
        <div className="rounded-xl border bg-card p-3">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Today&apos;s model revisions ({revisions})
          </p>
          <p className="mt-1 mb-2 text-[11px] text-muted-foreground">
            Live poll / light refresh updates on the same day — watch the forecast drift intraday.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="py-1.5 pr-3 font-medium">Time</th>
                  <th className="py-1.5 pr-3 font-medium">Spot</th>
                  <th className="py-1.5 pr-3 font-medium">{horizonDays}d target</th>
                  <th className="py-1.5 pr-3 font-medium">Return %</th>
                  <th className="py-1.5 font-medium">Trigger</th>
                </tr>
              </thead>
              <tbody>
                {[...intraday]
                  .sort((a, b) => b.predicted_at.localeCompare(a.predicted_at))
                  .slice(0, 30)
                  .map((row) => (
                    <tr key={row.predicted_at} className="border-b border-border/40">
                      <td className="py-1.5 pr-3 tabular-nums">{fmtTime(row.predicted_at)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtLevel(row.spot_at_prediction)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtLevel(row.implied_level)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{fmtPct(row.expected_return_pct)}</td>
                      <td className="py-1.5 text-muted-foreground">{row.refresh ?? "—"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
