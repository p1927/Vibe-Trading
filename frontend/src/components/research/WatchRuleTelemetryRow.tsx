import type { WatchRuleTelemetry } from "@/lib/api";
import { cn } from "@/lib/utils";

function fmtInr(value: number | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `₹${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPct(value: number | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function currentValueText(rule: WatchRuleTelemetry): string {
  const { metric, current, threshold, distance } = rule;
  if (!rule.quote_available) return "No live quote";

  if (metric === "spot_move_pct") {
    const parts = [fmtInr(current.ltp)];
    if (current.baseline_ltp != null) {
      parts.push(`baseline ${fmtInr(current.baseline_ltp)}`);
    }
    if (current.move_pct != null) {
      parts.push(fmtPct(current.move_pct));
    }
    if (distance.remaining != null && !distance.fired) {
      parts.push(`${distance.remaining.toFixed(2)}% to trigger`);
    }
    return parts.join(" · ");
  }

  if (metric === "level_above" || metric === "level_below") {
    const op = metric === "level_above" ? "≥" : "≤";
    const parts = [`${current.ltp?.toFixed(2) ?? "—"} / ${op}${threshold}`];
    if (distance.remaining != null && !distance.fired) {
      parts.push(`${distance.remaining.toFixed(2)} pts away`);
    }
    return parts.join(" · ");
  }

  if (metric === "oi_change_pct" || metric === "volume_spike_pct") {
    const field = metric === "oi_change_pct" ? current.oi : current.volume;
    const parts = [`${field ?? "—"}`, fmtPct(current.move_pct)];
    if (distance.remaining != null && !distance.fired) {
      parts.push(`${distance.remaining.toFixed(2)}% to trigger`);
    }
    return parts.join(" · ");
  }

  if (metric === "session_close") {
    return "Timer rule — no live value";
  }

  return fmtInr(current.ltp);
}

function statusChip(rule: WatchRuleTelemetry): { label: string; className: string } {
  if (!rule.quote_available) {
    return { label: "No quote", className: "border-muted text-muted-foreground" };
  }
  if (rule.distance.fired) {
    return {
      label: "Triggered",
      className: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    };
  }
  return {
    label: "Watching",
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  };
}

interface Props {
  rule: WatchRuleTelemetry;
  className?: string;
}

export function WatchRuleTelemetryRow({ rule, className }: Props) {
  const chip = statusChip(rule);

  return (
    <div className={cn("rounded border bg-background/50 px-2 py-1.5", className)}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-medium tabular-nums">{rule.symbol}</span>
            <span
              className={cn(
                "rounded-full border px-1.5 py-px text-[9px] font-semibold uppercase",
                chip.className,
              )}
            >
              {chip.label}
            </span>
          </div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">{rule.condition_text}</div>
          <div className="mt-1 text-[10px] tabular-nums text-foreground">{currentValueText(rule)}</div>
        </div>
      </div>
    </div>
  );
}
