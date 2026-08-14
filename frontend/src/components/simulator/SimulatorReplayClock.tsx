import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

function parseSimNow(value: unknown): Date | null {
  if (typeof value !== "string" || !value) return null;
  const d = new Date(value);
  return isNaN(d.getTime()) ? null : d;
}

const IST_TIME_ZONE = "Asia/Kolkata";

function formatHHMM(d: Date | null): string {
  if (!d) return "—";
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: IST_TIME_ZONE,
  });
}

function istMinutesOfDay(d: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: IST_TIME_ZONE,
  }).formatToParts(d);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return hour * 60 + minute;
}

function progressPct(d: Date | null): number {
  if (!d) return 0;
  const minutes = istMinutesOfDay(d);
  const open = 9 * 60 + 15;
  const close = 15 * 60 + 30;
  const span = close - open;
  if (span <= 0) return 0;
  return Math.max(0, Math.min(1, (minutes - open) / span));
}

export function SimulatorReplayClock({
  armedDate,
  onStop,
}: {
  armedDate: string | null;
  onStop: () => void;
}) {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<"pause" | "resume" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Poll status every second while armed
  useEffect(() => {
    if (!armedDate) {
      setStatus(null);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const tick = async () => {
      try {
        const res = await api.getReplayStatus();
        if (!controller.signal.aborted) setStatus(res.replay ?? null);
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Failed to fetch status");
        }
      }
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => {
      controller.abort();
      window.clearInterval(id);
    };
  }, [armedDate]);

  const clock = (status?.clock || {}) as Record<string, unknown>;
  const simNow = parseSimNow(clock.sim_now);
  const speed = typeof clock.speed === "number" ? clock.speed : 1;
  const loop = clock.loop === true;
  const paused = clock.paused === true;
  const completed = clock.completed === true;
  const replayDate = typeof clock.replay_date === "string" ? clock.replay_date : armedDate;

  const pct = useMemo(() => progressPct(simNow), [simNow]);

  async function handle(action: "pause" | "resume" | "stop") {
    setBusy(action);
    setError(null);
    try {
      const res =
        action === "pause" ? await api.pauseReplay() :
        action === "resume" ? await api.resumeReplay() :
        await api.stopReplay();
      setStatus(res.replay ?? null);
      if (action === "stop") onStop();
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    } finally {
      setBusy(null);
    }
  }

  if (!armedDate) {
    return (
      <div className="rounded-lg border bg-background/60 p-3 text-[11px] text-muted-foreground">
        Select a day on the calendar and press <span className="font-medium text-foreground">Arm replay</span> to start the simulator clock.
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border bg-background/60 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-0.5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Sim clock
          </p>
          <p className="font-mono text-lg tabular-nums">
            {formatHHMM(simNow)}
            <span className="ml-2 text-[11px] text-muted-foreground">IST · {replayDate}</span>
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-medium",
              paused
                ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                : completed
                ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                : "bg-blue-500/15 text-blue-700 dark:text-blue-300",
            )}
          >
            {paused ? "Paused" : completed ? "Day ended" : "Running"}
          </span>
          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
            {speed}×{loop ? " · loop" : ""}
          </span>
        </div>
      </div>

      {/* Day progress bar */}
      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>09:15</span>
          <span>{Math.round(pct * 100)}% of day</span>
          <span>15:30</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-700",
              paused ? "bg-amber-500" : "bg-primary",
            )}
            style={{ width: `${(pct * 100).toFixed(1)}%` }}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {paused ? (
          <button
            type="button"
            onClick={() => handle("resume")}
            disabled={busy !== null}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            data-testid="simulator-resume"
          >
            <Play className="h-3 w-3" /> Resume
          </button>
        ) : (
          <button
            type="button"
            onClick={() => handle("pause")}
            disabled={busy !== null || completed}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-background px-3 text-xs hover:bg-muted/50 disabled:opacity-50"
            data-testid="simulator-pause"
          >
            <Pause className="h-3 w-3" /> Pause
          </button>
        )}
        <button
          type="button"
          onClick={() => handle("stop")}
          disabled={busy !== null}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-red-500/40 bg-background px-3 text-xs text-red-600 hover:bg-red-500/10 disabled:opacity-50"
          data-testid="simulator-stop"
        >
          <Square className="h-3 w-3" /> {busy === "stop" ? "Stopping…" : "Stop simulator"}
        </button>
      </div>

      {error ? <p className="text-[11px] text-destructive">{error}</p> : null}
    </div>
  );
}
