import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Pause, Play, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { replayPollMs } from "@/lib/simSpeed";
import type { ReplayRange } from "@/components/simulator/SimulatorReplayCalendar";

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

function friendlyError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return "Can't reach the recording API — retrying…";
  }
  return message;
}

const SESSION_OPEN_MIN = 9 * 60 + 15;
const SESSION_CLOSE_MIN = 15 * 60 + 30;
const SESSION_SPAN_MIN = SESSION_CLOSE_MIN - SESSION_OPEN_MIN;

// Width of each minute cell inside the coverage strip. 100% / total session
// minutes → ~0.267% per cell. Using per-minute divs would balloon the DOM
// with 375 nodes; instead we merge contiguous covered/gap minutes into
// ranges and render one div per range — typically a handful.
const COVERAGE_MINUTE_PCT = 100 / SESSION_SPAN_MIN;

function progressPct(d: Date | null): number {
  if (!d) return 0;
  const minutes = istMinutesOfDay(d);
  if (SESSION_SPAN_MIN <= 0) return 0;
  return Math.max(0, Math.min(1, (minutes - SESSION_OPEN_MIN) / SESSION_SPAN_MIN));
}

interface CoverageRange {
  /** Minute-of-day (IST) where this range starts. */
  startMin: number;
  /** Number of consecutive minutes in this range. */
  length: number;
  /** True if the range is "covered" (green), false if it's a gap (transparent). */
  covered: boolean;
}

/** Merge contiguous covered/gap minutes into ranges so we can render one
 * absolutely-positioned div per range instead of 375 per-minute cells. */
function buildCoverageRanges(covered: Set<number>): CoverageRange[] {
  const ranges: CoverageRange[] = [];
  let currentStart: number | null = null;
  let currentHas = false;
  let currentLen = 0;
  for (let m = 0; m < SESSION_SPAN_MIN; m++) {
    const minute = SESSION_OPEN_MIN + m;
    const has = covered.has(minute);
    if (currentStart === null) {
      currentStart = minute;
      currentHas = has;
      currentLen = 1;
      continue;
    }
    if (has === currentHas) {
      currentLen += 1;
    } else {
      ranges.push({ startMin: currentStart, length: currentLen, covered: currentHas });
      currentStart = minute;
      currentHas = has;
      currentLen = 1;
    }
  }
  if (currentStart !== null) {
    ranges.push({ startMin: currentStart, length: currentLen, covered: currentHas });
  }
  return ranges;
}

/** IST minute-of-day from an ISO string like `2024-04-15T10:00:00+05:30`. */
function istMinuteFromIso(value: unknown): number | null {
  if (typeof value !== "string" || !value) return null;
  const d = new Date(value);
  if (isNaN(d.getTime())) return null;
  return istMinutesOfDay(d);
}

function pctToHHMMSS(pct: number): string {
  const clamped = Math.max(0, Math.min(1, pct));
  const totalMinutes = SESSION_OPEN_MIN + clamped * SESSION_SPAN_MIN;
  const hour = Math.floor(totalMinutes / 60);
  const minute = Math.floor(totalMinutes % 60);
  const second = Math.round((totalMinutes % 1) * 60);
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`;
}

export function SimulatorReplayClock({
  armedRange,
  onStop,
}: {
  armedRange: ReplayRange | null;
  onStop: () => void;
}) {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<"pause" | "resume" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scrubPct, setScrubPct] = useState<number | null>(null);
  const [seeking, setSeeking] = useState(false);
  const [coverage, setCoverage] = useState<Set<number>>(() => new Set());
  const abortRef = useRef<AbortController | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);

  // Poll status while armed, at a cadence that scales with replay speed
  // (capped at 4/sec) — a self-rescheduling loop rather than a fixed
  // setInterval, since the right delay depends on the speed the previous
  // response just reported and can change live (see replayPollMs).
  useEffect(() => {
    if (!armedRange) {
      setStatus(null);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    let inFlight = false;
    let timeoutId: number | undefined;
    const scheduleNext = (delayMs: number) => {
      if (controller.signal.aborted) return;
      timeoutId = window.setTimeout(tick, delayMs);
    };
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      let nextDelayMs = 1000;
      try {
        const res = await api.getReplayStatus();
        if (!controller.signal.aborted) {
          setStatus(res.replay ?? null);
          setError(null);
          const clockInfo = res.replay?.clock as Record<string, unknown> | undefined;
          nextDelayMs = replayPollMs(
            typeof clockInfo?.speed === "number" ? clockInfo.speed : undefined,
          );
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(friendlyError(err));
        }
      } finally {
        inFlight = false;
        scheduleNext(nextDelayMs);
      }
    };
    tick();
    return () => {
      controller.abort();
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [armedRange]);

  const clock = (status?.clock || {}) as Record<string, unknown>;
  const simNow = parseSimNow(clock.sim_now);
  const speed = typeof clock.speed === "number" ? clock.speed : 1;
  const loop = clock.loop === true;
  const paused = clock.paused === true;
  const completed = clock.completed === true;
  const replayDate =
    typeof clock.replay_date === "string" ? clock.replay_date : armedRange?.start ?? null;
  const weekDates = Array.isArray(clock.week_dates) ? (clock.week_dates as string[]) : [];
  const weekIndex = typeof clock.week_index === "number" ? clock.week_index : 0;
  const isRange = Boolean(armedRange && armedRange.start !== armedRange.end);

  const livePct = useMemo(() => progressPct(simNow), [simNow]);
  const pct = scrubPct ?? livePct;
  const coverageRanges = useMemo(() => buildCoverageRanges(coverage), [coverage]);

  // Once a seek lands, drop the local override so subsequent status polls
  // (server truth) drive the bar again.
  useEffect(() => {
    if (!draggingRef.current && !seeking) setScrubPct(null);
  }, [simNow, seeking]);

  // Fetch per-minute recording coverage for the armed day. Refetched only
  // when replay_date changes (or first arms). Bars come from
  // /trade/hub/index-history/bars — minute-resolution, so each bar's start
  // minute + (bar_minutes-1) trailing minutes are marked covered.
  useEffect(() => {
    if (!replayDate) {
      setCoverage(new Set());
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    (async () => {
      try {
        const since = `${replayDate}T09:15:00+05:30`;
        const until = `${replayDate}T15:30:00+05:30`;
        const res = await api.getHubIndexHistoryBars({
          symbol: "NIFTY",
          exchange: "NSE_INDEX",
          since_ist: since,
          until_ist: until,
        });
        if (cancelled) return;
        const next = new Set<number>();
        if (res.status === "ok" && Array.isArray(res.bars)) {
          for (const bar of res.bars) {
            const startMin = istMinuteFromIso(bar.ts_ist);
            if (startMin === null) continue;
            // bar_minutes ≥ 1; clamp the span to session bounds so a stray
            // pre/post-market bar doesn't paint outside the slider.
            const span = Math.max(1, Number(bar.bar_minutes ?? 1));
            const endMin = Math.min(SESSION_CLOSE_MIN, startMin + span);
            for (let m = Math.max(SESSION_OPEN_MIN, startMin); m < endMin; m++) {
              next.add(m);
            }
          }
        }
        setCoverage(next);
      } catch {
        // Silent — if the endpoint is unreachable, the bar just stays muted.
        // Don't raise a banner here; the status poll already surfaces fetch
        // failures from the replay service itself.
        if (!cancelled) setCoverage(new Set());
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [replayDate]);

  function pctFromClientX(clientX: number): number {
    const el = trackRef.current;
    if (!el) return pct;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return pct;
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  }

  async function commitSeek(target: number) {
    setSeeking(true);
    setError(null);
    try {
      const res = await api.seekReplay(pctToHHMMSS(target));
      setStatus(res.replay ?? null);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setSeeking(false);
      setScrubPct(null);
    }
  }

  function handlePointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (completed) return;
    draggingRef.current = true;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setScrubPct(pctFromClientX(e.clientX));
  }

  function handlePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!draggingRef.current) return;
    setScrubPct(pctFromClientX(e.clientX));
  }

  function handlePointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    const target = pctFromClientX(e.clientX);
    setScrubPct(target);
    void commitSeek(target);
  }

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
      setError(err instanceof Error ? friendlyError(err) : `${action} failed`);
    } finally {
      setBusy(null);
    }
  }

  if (!armedRange) {
    return (
      <div className="rounded-lg border bg-background/60 p-4 text-[11px] text-muted-foreground">
        Select a day (or a start + end range) on the calendar and press{" "}
        <span className="font-medium text-foreground">Arm replay</span> to start the simulator clock.
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border bg-background/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-0.5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Sim clock
          </p>
          <p className="font-mono text-lg tabular-nums">
            {formatHHMM(simNow)}
            <span className="ml-2 text-[11px] text-muted-foreground">IST · {replayDate}</span>
          </p>
          {isRange ? (
            <p className="text-[10px] text-muted-foreground">
              Range {armedRange.start} → {armedRange.end}
              {weekDates.length > 0 ? ` · day ${weekIndex + 1} of ${weekDates.length}` : ""}
            </p>
          ) : null}
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

      {/* Day progress bar — drag to scrub the sim clock to that point in time */}
      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>09:15</span>
          <span>
            {seeking ? "Seeking…" : `${Math.round(pct * 100)}% of day`}
          </span>
          <span>15:30</span>
        </div>
        <div
          ref={trackRef}
          role="slider"
          aria-label="Sim clock scrubber"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct * 100)}
          tabIndex={completed ? -1 : 0}
          className={cn(
            "group relative h-3 w-full touch-none select-none",
            completed ? "cursor-not-allowed" : "cursor-pointer",
          )}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onKeyDown={(e) => {
            if (completed) return;
            if (e.key === "ArrowLeft") {
              e.preventDefault();
              void commitSeek(Math.max(0, pct - 0.01));
            } else if (e.key === "ArrowRight") {
              e.preventDefault();
              void commitSeek(Math.min(1, pct + 0.01));
            }
          }}
        >
          <div
            className="absolute inset-y-0 top-[3px] h-1.5 w-full overflow-hidden rounded-full bg-muted"
            data-testid="simulator-coverage-track"
          >
            {/* Progress fill (current clock position) sits at the bottom of the
                stack; coverage cells paint on top so the user sees the green
                "data available" strip regardless of where the clock currently
                is. The fill's right edge is still the visible clock marker. */}
            <div
              className={cn(
                "h-full rounded-full",
                scrubPct === null && !seeking ? "transition-[width] duration-700" : "",
                paused ? "bg-amber-500" : "bg-primary",
              )}
              style={{ width: `${(pct * 100).toFixed(1)}%` }}
            />
            {coverageRanges.map((r, i) => (
              <div
                key={`${r.startMin}-${i}`}
                data-testid="simulator-coverage-cell"
                data-covered={r.covered ? "1" : "0"}
                className={cn(
                  "absolute inset-y-0",
                  r.covered ? "bg-emerald-500/60" : "bg-transparent",
                )}
                style={{
                  left: `${((r.startMin - SESSION_OPEN_MIN) * COVERAGE_MINUTE_PCT).toFixed(2)}%`,
                  width: `${(r.length * COVERAGE_MINUTE_PCT).toFixed(2)}%`,
                }}
              />
            ))}
          </div>
          <div
            className={cn(
              "absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background shadow transition-opacity",
              paused ? "bg-amber-500" : "bg-primary",
              "opacity-0 group-hover:opacity-100",
              (draggingRef.current || scrubPct !== null) && "opacity-100",
            )}
            style={{ left: `${(pct * 100).toFixed(1)}%` }}
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

      {error ? (
        <p
          className={cn(
            "flex items-center gap-1.5 text-[11px]",
            error.endsWith("…") ? "text-amber-600 dark:text-amber-400" : "text-destructive",
          )}
        >
          {error.endsWith("…") && (
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
          )}
          {error}
        </p>
      ) : null}
    </div>
  );
}
