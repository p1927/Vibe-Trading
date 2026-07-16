import { useMemo } from "react";
import { cn } from "@/lib/utils";

interface Scenario {
  event?: string;
  outcome?: string;
  label?: string;
  description?: string;
  index_range?: number[] | string;
  probability?: number;
  midpoint_return_pct?: number;
}

function fmtRange(raw: number[] | string | undefined): string | null {
  if (raw == null) return null;
  if (Array.isArray(raw) && raw.length >= 2) {
    const low = Number(raw[0]);
    const high = Number(raw[1]);
    if (Number.isFinite(low) && Number.isFinite(high)) {
      return `${low.toLocaleString("en-IN", { maximumFractionDigits: 0 })} – ${high.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
    }
  }
  if (typeof raw === "string" && raw.trim()) return raw;
  return null;
}

function fmtPct(v: number | null | undefined): string | null {
  if (v == null || !Number.isFinite(v)) return null;
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function titleFor(s: Scenario): string {
  if (s.label?.trim()) return s.label;
  return [s.event, s.outcome].filter(Boolean).join(" · ").replace(/_/g, " ");
}

interface Props {
  scenarios?: Scenario[];
  horizonDays?: number;
  reconciled?: boolean;
}

export function ScenarioTiles({ scenarios = [], horizonDays = 14, reconciled }: Props) {
  const sorted = useMemo(
    () =>
      [...scenarios].sort(
        (a, b) => (Number(b.probability) || 0) - (Number(a.probability) || 0),
      ),
    [scenarios],
  );

  if (!sorted.length) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground shadow-sm">
        No scenarios yet — run analysis to generate event outcomes ranked by likelihood.
      </div>
    );
  }

  const colCount = Math.min(sorted.length, 6);

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        Most likely at left → least likely at right. Ranges are Nifty index points over ~{horizonDays} days.
        {reconciled ? (
          <span className="ml-1 text-emerald-700 dark:text-emerald-400">
            Headline forecast was blended toward these scenarios.
          </span>
        ) : (
          <span className="ml-1">
            When the macro model diverges sharply, the headline is pulled toward the probability-weighted midpoint.
          </span>
        )}
      </p>
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
      >
        {sorted.map((s, i) => {
          const prob = s.probability;
          const range = fmtRange(s.index_range);
          const midRet = fmtPct(s.midpoint_return_pct);
          const isTop = i === 0;
          return (
            <div
              key={`${s.event}-${s.outcome}-${i}`}
              className={cn(
                "flex min-h-[140px] flex-col rounded-xl border bg-card p-3 shadow-sm",
                isTop && "border-emerald-500/40 ring-1 ring-emerald-500/20",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-[11px] font-semibold leading-snug">{titleFor(s)}</p>
                {prob != null && Number.isFinite(prob) ? (
                  <span
                    className={cn(
                      "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold tabular-nums",
                      prob >= 0.35
                        ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                        : "bg-muted text-muted-foreground",
                    )}
                  >
                    {Math.round(prob * 100)}%
                  </span>
                ) : null}
              </div>
              {range ? (
                <p className="mt-2 text-sm font-semibold tabular-nums">{range}</p>
              ) : null}
              {midRet ? (
                <p className="text-[10px] text-muted-foreground">Midpoint {midRet} vs spot</p>
              ) : null}
              {s.description ? (
                <p className="mt-auto pt-2 text-[10px] leading-relaxed text-muted-foreground">
                  {s.description}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
