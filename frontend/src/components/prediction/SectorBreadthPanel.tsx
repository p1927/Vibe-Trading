import { useMemo } from "react";
import type { SectorBreadth } from "@/lib/api";

interface Props {
  breadth?: SectorBreadth | null;
}

export function SectorBreadthPanel({ breadth }: Props) {
  const sectors = useMemo(() => {
    const by = breadth?.by_sector ?? {};
    return Object.entries(by)
      .map(([name, score]) => ({ name, score }))
      .sort((a, b) => Math.abs(b.score) - Math.abs(a.score));
  }, [breadth]);

  if (!breadth || (!sectors.length && breadth.mean_sentiment == null)) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground shadow-sm">
        Sector breadth not available yet — run analysis with constituent sentiment.
      </div>
    );
  }

  const maxAbs = Math.max(...sectors.map((s) => Math.abs(s.score)), 0.01);

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Sector breadth
        </p>
        <p className="text-[11px] text-muted-foreground">
          {breadth.sector_count ?? sectors.length} sectors
          {breadth.mean_sentiment != null ? (
            <span className="ml-2 font-medium text-foreground">
              mean {breadth.mean_sentiment >= 0 ? "+" : ""}
              {breadth.mean_sentiment.toFixed(3)}
            </span>
          ) : null}
        </p>
      </div>
      <ul className="mt-3 space-y-1.5">
        {sectors.map((row) => {
          const pct = (Math.abs(row.score) / maxAbs) * 100;
          const positive = row.score >= 0;
          return (
            <li key={row.name} className="flex items-center gap-2 text-[11px]">
              <span className="w-28 shrink-0 truncate text-muted-foreground" title={row.name}>
                {row.name}
              </span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted/50">
                <div
                  className={`h-full rounded-full ${positive ? "bg-emerald-500/70" : "bg-red-500/70"}`}
                  style={{ width: `${Math.max(4, pct)}%` }}
                />
              </div>
              <span className="w-12 shrink-0 text-right tabular-nums font-medium">
                {row.score >= 0 ? "+" : ""}
                {row.score.toFixed(2)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
