import { useEffect, useMemo, useState } from "react";
import { Activity, CheckCircle2, Circle, Loader2, Timer } from "lucide-react";
import type { IndexTrackScoreboardReport } from "@/lib/api";
import {
  BACKTEST_COMBINER_IDS,
  CANONICAL_TRACK_IDS,
  trackDisplayLabel,
} from "@/lib/trackScoreboardReplayUtils";
import { cn } from "@/lib/utils";

const PIPELINE_STEPS = [
  { id: "connect", label: "Connect to scoreboard API", afterMs: 0 },
  { id: "history", label: "Load 730d aligned factor history", afterMs: 3_000 },
  { id: "walkforward", label: "Walk-forward OOS eval (10 tracks × ~88 dates)", afterMs: 12_000 },
  { id: "combiners", label: "Score 8 combiners + promotion gates", afterMs: 90_000 },
  { id: "charts", label: "Build replay charts + nifty series", afterMs: 120_000 },
  { id: "live", label: "Attach live hub forecast snapshot", afterMs: 135_000 },
] as const;

function formatElapsed(ms: number): string {
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return `${min}m ${rem}s`;
}

interface Props {
  open: boolean;
  loading: boolean;
  recomputing: boolean;
  startedAt: number | null;
  report: IndexTrackScoreboardReport | null;
  horizonDays: number;
  error?: string | null;
}

export function TrackScoreboardProgressPanel({
  open,
  loading,
  recomputing,
  startedAt,
  report,
  horizonDays,
  error,
}: Props) {
  const [now, setNow] = useState(() => Date.now());

  const active = loading || recomputing;

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [active]);

  const elapsedMs = startedAt && active ? Math.max(0, now - startedAt) : 0;

  const activeStepIndex = useMemo(() => {
    if (!active) return PIPELINE_STEPS.length;
    let idx = 0;
    for (let i = 0; i < PIPELINE_STEPS.length; i += 1) {
      if (elapsedMs >= PIPELINE_STEPS[i].afterMs) idx = i;
    }
    return idx;
  }, [active, elapsedMs]);

  if (!open) return null;

  const lastAsOf = report?.as_of ?? (report as { as_of?: string } | null)?.as_of;
  const evalCount = report?.eval_count ?? 0;

  return (
    <aside
      className={cn(
        "flex h-[calc(100vh-5rem)] w-full shrink-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm",
        "lg:sticky lg:top-4 lg:w-80 xl:w-96",
      )}
      aria-label="Track scoreboard activity"
    >
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <Activity className="h-4 w-4 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Scoreboard activity</p>
          <p className="text-[10px] text-muted-foreground">
            {active
              ? recomputing && evalCount > 0
                ? "Refreshing walk-forward — showing cached results"
                : "Running per-track walk-forward backtest"
              : evalCount > 0
                ? `Last run · ${evalCount} OOS eval dates · ${horizonDays}d horizon`
                : "Walk-forward status for forecast tracks"}
          </p>
        </div>
        {active ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : null}
      </div>

      {active ? (
        <div className="border-b bg-primary/5 px-3 py-2 text-[10px] text-muted-foreground">
          <div className="flex items-center gap-2">
            <Timer className="h-3.5 w-3.5 shrink-0" />
            <span>
              Elapsed <span className="font-mono font-medium text-foreground">{formatElapsed(elapsedMs)}</span>
              {" · "}
              Usually 2–3 min for a full recompute
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all duration-700 ease-out"
              style={{
                width: `${Math.min(95, Math.max(8, (elapsedMs / 150_000) * 100))}%`,
              }}
            />
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="border-b bg-red-500/10 px-3 py-2 text-[10px] text-red-700 dark:text-red-400">{error}</div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Pipeline</p>
        <ul className="space-y-2">
          {PIPELINE_STEPS.map((step, idx) => {
            const done = !active || idx < activeStepIndex;
            const current = active && idx === activeStepIndex;
            return (
              <li key={step.id} className="flex items-start gap-2 text-[11px]">
                {done ? (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
                ) : current ? (
                  <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
                ) : (
                  <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/40" />
                )}
                <span className={cn(current ? "font-medium text-foreground" : done ? "text-muted-foreground" : "text-muted-foreground/60")}>
                  {step.label}
                </span>
              </li>
            );
          })}
        </ul>

        <p className="mb-2 mt-4 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Forecast tracks ({CANONICAL_TRACK_IDS.length})
        </p>
        <ul className="space-y-1">
          {CANONICAL_TRACK_IDS.map((tid, idx) => {
            const trackDone = !active || idx < Math.floor((activeStepIndex / PIPELINE_STEPS.length) * CANONICAL_TRACK_IDS.length);
            const row = report?.tracks?.[tid];
            return (
              <li
                key={tid}
                className={cn(
                  "flex items-center justify-between rounded-md px-2 py-1 text-[10px]",
                  trackDone && row?.eval_count ? "bg-emerald-500/10" : "bg-muted/30",
                )}
              >
                <span className="truncate capitalize">{trackDisplayLabel(tid)}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {row?.eval_count ? `n=${row.eval_count}` : active ? "…" : "—"}
                </span>
              </li>
            );
          })}
        </ul>

        <p className="mb-2 mt-4 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Combiners ({BACKTEST_COMBINER_IDS.length})
        </p>
        <p className="text-[10px] text-muted-foreground">
          {active
            ? "Scored after all track eval rows are collected."
            : report?.combiners
              ? `${Object.keys(report.combiners).length} combiner metrics in cache`
              : "Run recompute to score combiners."}
        </p>
      </div>

      <div className="border-t px-3 py-2 text-[10px] text-muted-foreground">
        {report?.history_start && report?.history_end ? (
          <p>
            History window: {report.history_start} → {report.history_end}
            {report.history_rows ? ` · ${report.history_rows} rows` : ""}
          </p>
        ) : (
          <p>730 trading days · eval every 5 sessions · nested walk-forward</p>
        )}
        {lastAsOf ? (
          <p className="mt-1">Cached as of {String(lastAsOf).slice(0, 19).replace("T", " ")} UTC</p>
        ) : null}
        {report?.needs_refresh && !active ? (
          <p className="mt-1 text-amber-700 dark:text-amber-400">Cache stale — recompute recommended.</p>
        ) : null}
      </div>
    </aside>
  );
}
