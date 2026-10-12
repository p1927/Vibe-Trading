import type { SourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayRecord } from "@/lib/forecastReplay/types";
import { forecastReturnPct, keyPoint } from "@/lib/forecastReplay/view";
import { fmtNiftyLevel } from "@/lib/factorHistoryUtils";
import { fmtPct } from "@/lib/trackScoreboardUtils";
import { RecordBadges, SameForecastNote } from "./RecordBadges";

interface Props {
  records: ReplayRecord[];
  horizon: number;
  styles: Record<string, SourceStyle>;
}

/** What each source said on the anchor day versus what happened (matured return and the delta). */
export function DayDetail({ records, horizon, styles }: Props) {
  if (!records.length) return null;
  return (
    <div className="overflow-x-auto rounded-lg border border-border/50">
      <table className="w-full text-[11px]" data-testid="day-detail">
        <thead>
          <tr className="border-b bg-muted/30 text-left text-muted-foreground">
            <th className="px-2 py-1 font-medium">Source</th>
            <th className="px-2 py-1 text-right font-medium">Spot</th>
            <th className="px-2 py-1 text-right font-medium">Forecast</th>
            <th className="px-2 py-1 text-right font-medium">Target date</th>
            <th className="px-2 py-1 text-right font-medium">Actual</th>
            <th className="px-2 py-1 text-right font-medium">Error</th>
            <th className="px-2 py-1 text-right font-medium">Band</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => {
            const p = keyPoint(r, horizon);
            if (!p) return null;
            const actualPct = p.matured && p.actual_level != null && r.spot_at_issue > 0 ? (p.actual_level / r.spot_at_issue - 1) * 100 : null;
            return (
              <tr key={`${r.source_id}-${r.issued_at}`} className="border-b last:border-0">
                <td className="px-2 py-1">
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: styles[r.source_id]?.color }} />
                  {styles[r.source_id]?.label ?? r.source_id}
                  {r.approximate_path ? <span className="ml-1 text-muted-foreground">approx.</span> : null}
                  <RecordBadges records={[r]} />
                  <SameForecastNote record={r} styles={styles} />
                </td>
                <td className="px-2 py-1 text-right tabular-nums">{fmtNiftyLevel(r.spot_at_issue)}</td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {fmtNiftyLevel(p.p50_level)} <span className="text-muted-foreground">{p.horizon_days}d</span> <span className="text-muted-foreground">{fmtPct(forecastReturnPct(r, horizon))}</span>
                </td>
                <td className="px-2 py-1 text-right tabular-nums">{p.target_date}</td>
                <td className="px-2 py-1 text-right tabular-nums">{actualPct != null ? fmtPct(actualPct) : r.points.some((q) => q.matured) ? `Pending (${r.points.filter((q) => q.matured).length}/${r.points.length} points matured)` : "Pending"}</td>
                <td className={`px-2 py-1 text-right tabular-nums ${p.error_pct != null ? (p.in_band === false ? "text-amber-700 dark:text-amber-400" : "") : ""}`}>
                  {p.error_pct != null ? fmtPct(p.error_pct) : "—"}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {p.in_band == null ? "—" : p.in_band ? "inside" : "outside"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
