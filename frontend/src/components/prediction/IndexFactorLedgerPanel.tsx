import type { IndexPredictionHistoryRow } from "@/lib/api";

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

interface Props {
  daily: IndexPredictionHistoryRow[];
  intraday?: IndexPredictionHistoryRow[];
  horizonDays?: number;
}

export function IndexFactorLedgerPanel({ daily, intraday = [], horizonDays = 14 }: Props) {
  const allRows = [...daily, ...intraday].sort((a, b) => b.predicted_at.localeCompare(a.predicted_at));

  if (!allRows.length) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground">
        Prediction ledger empty — run analysis to append the first forecast row.
      </div>
    );
  }

  const matured = allRows.filter((r) => r.actual_return_pct != null).length;

  return (
    <div className="space-y-2 rounded-xl border bg-card p-4 shadow-sm">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Index prediction ledger
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {allRows.length} entries · {matured} matured with realised {horizonDays}d return · nightly
          reconciliation fills Actual column.
        </p>
      </div>
      <div className="max-h-[320px] overflow-auto">
        <table className="w-full text-left text-[11px]">
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-[10px] uppercase tracking-wide text-muted-foreground">
              <th className="py-1.5 pr-2">When</th>
              <th className="py-1.5 pr-2">Spot</th>
              <th className="py-1.5 pr-2">Forecast</th>
              <th className="py-1.5 pr-2">Return</th>
              <th className="py-1.5 pr-2">Bottom-up</th>
              <th className="py-1.5 pr-2">Macro</th>
              <th className="py-1.5 pr-2">Actual</th>
              <th className="py-1.5">Hit</th>
            </tr>
          </thead>
          <tbody>
            {allRows.slice(0, 50).map((row) => (
              <tr key={row.predicted_at} className="border-b border-border/40">
                <td className="py-1.5 pr-2 tabular-nums whitespace-nowrap">
                  {row.predicted_at.slice(0, 16).replace("T", " ")}
                </td>
                <td className="py-1.5 pr-2 tabular-nums">{fmtLevel(row.spot_at_prediction)}</td>
                <td className="py-1.5 pr-2 tabular-nums">{fmtLevel(row.implied_level)}</td>
                <td className="py-1.5 pr-2 tabular-nums">{fmtPct(row.expected_return_pct)}</td>
                <td className="py-1.5 pr-2 tabular-nums">{fmtPct(row.bottom_up_return_pct)}</td>
                <td className="py-1.5 pr-2 tabular-nums">{fmtPct(row.macro_delta_pct)}</td>
                <td className="py-1.5 pr-2 tabular-nums">
                  {row.actual_return_pct != null ? fmtPct(row.actual_return_pct) : "—"}
                </td>
                <td className="py-1.5">
                  {row.direction_correct == null ? "—" : row.direction_correct ? "✓" : "✗"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
