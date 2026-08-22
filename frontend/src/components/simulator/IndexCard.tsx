import { Line, LineChart, ReferenceLine, ResponsiveContainer } from "recharts";
import { cn } from "@/lib/utils";

export interface IndexCardProps {
  name: string;
  price: number | null;
  change: number | null;
  changePct: number | null;
  /** Closing/last-traded values, oldest first — rendered as a chromeless sparkline. */
  sparkline: number[];
  /** Short freshness label, e.g. "LIVE" or "EOD". */
  badge?: string;
  loading?: boolean;
  error?: string | null;
}

function fmtPrice(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function fmtChange(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** Compact ticker card: name, price, change, and a chromeless sparkline —
 * the "quick glance" style used by the market-overview strip this mirrors. */
export function IndexCard({ name, price, change, changePct, sparkline, badge, loading, error }: IndexCardProps) {
  const up = (change ?? 0) >= 0;
  const color = up ? "#16a34a" : "#dc2626";
  const points = sparkline.map((v, i) => ({ i, v }));
  const baseline = sparkline.length > 0 ? sparkline[0] : undefined;

  return (
    <div className="rounded-xl border bg-card p-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold">{name}</p>
        {badge && (
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[9px] font-medium text-muted-foreground">
            {badge}
          </span>
        )}
      </div>
      {error ? (
        <p className="mt-2 text-[11px] text-destructive">{error}</p>
      ) : loading ? (
        <p className="mt-2 text-[11px] text-muted-foreground">Loading…</p>
      ) : (
        <>
          <p className="mt-1 text-lg font-semibold tabular-nums">{fmtPrice(price)}</p>
          <p className={cn("text-xs tabular-nums", up ? "text-emerald-600" : "text-red-600")}>
            {fmtChange(change)} {fmtPct(changePct)}
          </p>
          {points.length > 1 && (
            <div className="mt-2 h-10 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={points} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                  {baseline != null && (
                    <ReferenceLine y={baseline} stroke="currentColor" strokeDasharray="2 3" className="text-muted-foreground/40" />
                  )}
                  <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </div>
  );
}
