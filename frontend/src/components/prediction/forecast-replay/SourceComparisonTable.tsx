import type { SourceStyle } from "@/lib/forecastReplay/sourceStyle";

type Stats = Record<string, Record<string, number | string | null>>;

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const num = (v: unknown, d = 2) => (finite(v) ? v.toFixed(d) : "—");
const rate = (v: unknown) => (finite(v) ? `${(v * 100).toFixed(0)}%` : "—");

interface Props {
  ids: string[];
  stats: Stats;
  styles: Record<string, SourceStyle>;
}

/** Server-computed accuracy per source at the chosen horizon (no client recomputation). */
export function SourceComparisonTable({ ids, stats, styles }: Props) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border/50">
      <table className="w-full text-[11px]" data-testid="source-comparison">
        <thead>
          <tr className="border-b bg-muted/30 text-left text-muted-foreground">
            <th className="px-2 py-1 font-medium">Source</th>
            <th className="px-2 py-1 text-right font-medium">n</th>
            <th className="px-2 py-1 text-right font-medium">MAE %</th>
            <th className="px-2 py-1 text-right font-medium">Bias %</th>
            <th className="px-2 py-1 text-right font-medium">Direction hit</th>
            <th className="px-2 py-1 text-right font-medium">In band</th>
          </tr>
        </thead>
        <tbody>
          {ids.map((id) => {
            const s = stats[id];
            return (
              <tr key={id} className="border-b last:border-0">
                <td className="px-2 py-1">
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: styles[id]?.color }} />
                  {styles[id]?.label ?? id}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">{finite(s?.eval_count) ? s.eval_count : "—"}</td>
                <td className="px-2 py-1 text-right tabular-nums">{num(s?.mae_pct)}</td>
                <td className="px-2 py-1 text-right tabular-nums">{num(s?.bias_pct)}</td>
                <td className="px-2 py-1 text-right tabular-nums">{rate(s?.direction_hit_rate)}</td>
                <td className="px-2 py-1 text-right tabular-nums">{rate(s?.in_band_rate)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
