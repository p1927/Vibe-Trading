import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Disc, ListOrdered, PlayCircle, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  api,
  type ConstituentInfo,
  type PipelineLogEntry,
  type RecordingJobSnapshot,
  type RecordingResult,
  type ReplayCalendarDay,
} from "@/lib/api";
import { SimulatorInstrumentPicker } from "@/components/simulator/SimulatorInstrumentPicker";
import { SimulatorLiveIndexPanel } from "@/components/simulator/SimulatorLiveIndexPanel";
import { SimulatorOptionChainPanel } from "@/components/simulator/SimulatorOptionChainPanel";
import {
  SimulatorReplayCalendar,
  type ReplayRange,
} from "@/components/simulator/SimulatorReplayCalendar";
import { SimulatorReplayClock } from "@/components/simulator/SimulatorReplayClock";
import { SimulatorReplayDetailPanel } from "@/components/simulator/SimulatorReplayDetailPanel";
import { StockHistoryCoveragePanel } from "@/components/simulator/StockHistoryCoveragePanel";
import {
  RecordingConfigProvider,
  SimulatorRecordingFields,
} from "@/components/simulator/SimulatorRecordingFields";
import {
  DEFAULT_CATEGORY_INTERVALS,
  DEFAULT_EQUITY_INTERVALS,
  DEFAULT_HISTORICAL_CONFIG,
  DEFAULT_WS_THROTTLE_HZ,
  historicalIntervalKeyToApi,
  historicalLookbackKeyToDays,
  type HistoricalIntervalKey,
  type HistoricalLookbackKey,
} from "@/lib/intervalOptions";

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"];

function StatCard({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-xl border bg-card p-4 shadow-sm", className)}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function statusBadge(status: string | undefined) {
  const s = (status || "idle").toLowerCase();
  const styles: Record<string, string> = {
    queued: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
    waiting: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
    waiting_for_open: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
    running: "bg-blue-500/15 text-blue-800 dark:text-blue-200",
    done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    error: "bg-red-500/15 text-red-800 dark:text-red-200",
    idle: "bg-muted text-muted-foreground",
  };
  const labels: Record<string, string> = {
    queued: "Starting…",
    waiting: "Waiting for Market Open",
    waiting_for_open: "Waiting for Market Open",
    running: "Recording",
    done: "Market Closed — Done",
    error: "Error",
    idle: "Idle",
  };
  return (
    <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-medium", styles[s] || styles.idle)}>
      {labels[s] || s}
    </span>
  );
}

function logLevelColor(level: string | undefined): string {
  const l = (level || "info").toLowerCase();
  if (l === "error") return "text-red-500";
  if (l === "warn" || l === "warning") return "text-amber-500";
  return "text-muted-foreground";
}

function ProgressBar({ pct }: { pct: number | null | undefined }) {
  const value = Math.max(0, Math.min(1, pct ?? 0));
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-primary transition-[width] duration-500"
        style={{ width: `${(value * 100).toFixed(1)}%` }}
      />
    </div>
  );
}

export function Simulator() {
  const [selected, setSelected] = useState<string[]>(UNDERLYINGS);
  const [selectedEquities, setSelectedEquities] = useState<string[]>([]);
  const [waitForOpen, setWaitForOpen] = useState(false);
  // Per-category recording intervals (seconds) + WS LTP throttle (Hz).
  // Lifted from the disclosure so the page passes them into
  // api.startRecording rather than the now-removed poll_interval_s.
  const [categoryIntervals, setCategoryIntervals] = useState<Record<string, number>>({
    ...DEFAULT_CATEGORY_INTERVALS,
  });
  // Per-equity (NIFTY50) intervals — defaults to *off* per the plan so
  // the user opts in. Shared cadence across all selected equities.
  const [equityIntervals, setEquityIntervals] = useState<Record<string, number>>({
    ...DEFAULT_EQUITY_INTERVALS,
  });
  // Historical candle config. Pulled once per equity at session start.
  const [historicalConfig, setHistoricalConfigState] = useState<{
    interval: HistoricalIntervalKey;
    lookback_days: HistoricalLookbackKey;
  }>(DEFAULT_HISTORICAL_CONFIG);
  const setHistoricalConfig = useCallback(
    (
      next:
        | {
            interval: HistoricalIntervalKey;
            lookback_days: HistoricalLookbackKey;
          }
        | null,
    ) => {
      if (next === null) {
        setHistoricalConfigState(DEFAULT_HISTORICAL_CONFIG);
      } else {
        setHistoricalConfigState(next);
      }
    },
    [],
  );
  const [wsThrottleHz, setWsThrottleHz] = useState<number | null>(DEFAULT_WS_THROTTLE_HZ);
  const setCategoryInterval = useCallback((category: string, seconds: number) => {
    setCategoryIntervals((prev) => ({ ...prev, [category]: seconds }));
  }, []);
  const setEquityInterval = useCallback((category: string, seconds: number) => {
    setEquityIntervals((prev) => ({ ...prev, [category]: seconds }));
  }, []);
  const setWsThrottle = useCallback((hz: number | null) => {
    setWsThrottleHz(hz);
  }, []);
  const [job, setJob] = useState<RecordingJobSnapshot | null>(null);
  const [logs, setLogs] = useState<PipelineLogEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [calendarDays, setCalendarDays] = useState<ReplayCalendarDay[]>([]);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  // Distinct from calendarError: the token simply isn't set up yet, not a
  // fetch failure — no "Retry" button, since retrying can't fix a missing
  // env var.
  const [replayNotConfigured, setReplayNotConfigured] = useState<string | null>(null);
  const [replayRange, setReplayRange] = useState<ReplayRange | null>(null);
  const [armedRange, setArmedRange] = useState<ReplayRange | null>(null);
  const [replaySpeed, setReplaySpeed] = useState<number>(1);
  const [replayLoop, setReplayLoop] = useState<boolean>(true);
  const [armingDay, setArmingDay] = useState<string | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [selectedReplayDay, setSelectedReplayDay] = useState<ReplayCalendarDay | null>(null);
  // Phase 9: option-chain drawer toggle.
  const [showChain, setShowChain] = useState(false);

  // Which symbol drives the live chart + option chain, independent of the
  // Record checkboxes — any of the three indices are always chartable, plus
  // the full NIFTY-50 constituent list (not just whatever's picked for
  // recording — the picker's selection only controls what gets *recorded*,
  // this controls what the chart *shows*). Bare NSE tickers like
  // "RELIANCE"; the market-data endpoints accept exchange="NSE" for those,
  // same as "NSE_INDEX" for the indices.
  const [primarySymbol, setPrimarySymbol] = useState<{ symbol: string; exchange: string }>({
    symbol: "NIFTY",
    exchange: "NSE_INDEX",
  });
  const [nifty50, setNifty50] = useState<ConstituentInfo[]>([]);
  useEffect(() => {
    api
      .getRecordingConstituents()
      .then((res) => setNifty50(res.constituents || []))
      .catch(() => {});
  }, []);
  const chartableSymbols = useMemo(
    () => [
      ...UNDERLYINGS.map((u) => ({ symbol: u, exchange: "NSE_INDEX" })),
      ...nifty50.map((c) => ({ symbol: c.symbol, exchange: "NSE" })),
    ],
    [nifty50],
  );
  useEffect(() => {
    const stillValid = chartableSymbols.some(
      (s) => s.symbol === primarySymbol.symbol && s.exchange === primarySymbol.exchange,
    );
    if (!stillValid) setPrimarySymbol(chartableSymbols[0] ?? { symbol: "NIFTY", exchange: "NSE_INDEX" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartableSymbols]);

  // Whether the underlying's real trading session is open right now, as
  // reported by the live index panel's poll (null = unknown/check failed).
  // Drives the "market closed — replay the latest day" banner below.
  const [marketOpen, setMarketOpen] = useState<boolean | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  const isActive =
    job?.status === "queued" ||
    job?.status === "waiting_for_open" ||
    job?.status === "running";
  const isWaitingForOpen = job?.status === "waiting_for_open";
  const displayStatus = isWaitingForOpen ? "waiting_for_open" : job?.status;
  // Phase C: prefer the structured ``next_open_at`` field from the job
  // snapshot (set by ``set_waiting_for_open_at`` in recording_jobs.py)
  // over the legacy log-entry fallback. The fallback still works for
  // jobs persisted before Phase C, but new waits have an empty log
  // stream until the scheduled wake fires.
  const nextOpenAtLabel =
    job?.next_open_at ||
    (([...logs].reverse().find((entry) => entry?.stage === "waiting")
      ?.detail as { next_open_at?: string } | undefined)?.next_open_at) ||
    "—";

  const loadCalendar = useCallback(() => {
    setCalendarError(null);
    setReplayNotConfigured(null);
    api
      .getReplayCalendar()
      .then((res) => {
        setCalendarDays(res.days || []);
        if (res.configured === false) {
          setReplayNotConfigured(
            res.message || "Replay isn't configured on this OpenAlgo instance yet.",
          );
        }
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : "Failed to load calendar";
        setCalendarError(
          /failed to fetch|networkerror|load failed/i.test(message)
            ? "Can't reach the recording API to load the calendar."
            : message,
        );
      });
  }, []);

  const attachStream = useCallback((jobId: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    api
      .streamRecordingJob(
        jobId,
        {
          onLog: (entry) => setLogs((prev) => [...prev, entry]),
          onDone: (result: RecordingResult) => {
            setJob((prev) => (prev ? { ...prev, status: "done", result } : prev));
            loadCalendar();
          },
          onError: (message) => {
            setJob((prev) => (prev ? { ...prev, status: "error", error: message } : prev));
          },
        },
        controller.signal,
      )
      .catch(() => {});
  }, [loadCalendar]);

  useEffect(() => {
    loadCalendar();
    api
      .getActiveRecording()
      .then((res) => {
        if (res.job) {
          setJob(res.job);
          setLogs(res.job.logs || []);
          attachStream(res.job.job_id);
        }
      })
      .catch(() => {});
    // A replay armed in a prior session (or by another tab) leaves
    // STOCK_SIMULATOR_MODE=replay set on the OpenAlgo side even after this
    // page reloads — without this, the page boots with armedRange=null and
    // drifts out of sync with the server until the user re-arms manually.
    // Best-effort: swallow errors (e.g. SIMULATOR_CONTROL_TOKEN not
    // configured) since this is a background reconciliation, not a
    // user-initiated action.
    api
      .getReplayStatus()
      .then((res) => {
        const replay = (res.replay ?? null) as Record<string, unknown> | null;
        if (!replay || replay.mode !== "replay") return;
        const clock = (replay.clock || {}) as Record<string, unknown>;
        const replayDate = typeof clock.replay_date === "string" ? clock.replay_date : null;
        if (!replayDate) return;
        // The status endpoint doesn't cleanly distinguish an explicit
        // start+end range arm from the "last N trading days" week-mode
        // default, so reconcile conservatively as a single-day arm at the
        // current replay date rather than guessing a wider range.
        const armed: ReplayRange = { start: replayDate, end: replayDate };
        setReplayRange(armed);
        setArmedRange(armed);
      })
      .catch(() => {});
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [logs]);

  useEffect(() => {
    if (!isActive) return;
    const interval = window.setInterval(() => {
      if (!job) return;
      api
        .getRecordingJob(job.job_id)
        .then((res) => {
          if (res.job) setJob(res.job);
        })
        .catch(() => {});
    }, 5000);
    return () => window.clearInterval(interval);
  }, [isActive, job?.job_id]);

  const startRecording = async () => {
    setBusy(true);
    setError(null);
    setLogs([]);
    try {
      const res = await api.startRecording({
        underlyings: [...selected, ...selectedEquities],
        equities: selectedEquities,
        category_intervals: categoryIntervals,
        equity_intervals: equityIntervals,
        ws_throttle_hz: wsThrottleHz,
        historical_config: historicalConfig
          ? {
              interval: historicalIntervalKeyToApi(historicalConfig.interval),
              lookback_days: historicalLookbackKeyToDays(historicalConfig.lookback_days),
            }
          : null,
        wait_for_open: waitForOpen,
      });
      const snap = await api.getRecordingJob(res.job_id);
      setJob(snap.job ?? { job_id: res.job_id, status: res.job_status });
      attachStream(res.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start recording");
    } finally {
      setBusy(false);
    }
  };

  const stopRecording = async () => {
    if (!job) return;
    // Optimistic flip so the UI visibly reacts within one frame —
    // ``complete_job`` on the server fires within ~1s of Stop and the
    // SSE ``done`` event will catch up shortly; this just removes the
    // window where the user wonders whether the click was received.
    // The recorder worker is now detached from the end-of-session
    // supplement, so the SSE ``done`` event lands essentially
    // immediately in practice — this is belt-and-braces UX.
    const previousStatus = job.status;
    setJob((prev) => (prev ? { ...prev, status: "done" } : prev));
    setBusy(true);
    try {
      await api.stopRecording(job.job_id);
    } catch (err) {
      // POST failed (network or 409 because the job already finished).
      // Roll back to the pre-click status — the SSE stream will
      // overwrite it with the real state on the next tick either way.
      setJob((prev) => (prev ? { ...prev, status: previousStatus } : prev));
      setError(err instanceof Error ? err.message : "Failed to stop recording");
    } finally {
      setBusy(false);
    }
  };

  const armReplay = async (range: ReplayRange) => {
    setArmingDay(range.start);
    setReplayError(null);
    try {
      const res = await api.startReplay(range.start, {
        speed: replaySpeed,
        loop: replayLoop,
        end_date: range.end !== range.start ? range.end : undefined,
      });
      const replay = (res.replay ?? null) as Record<string, unknown> | null;
      const clock = (replay?.clock || {}) as Record<string, unknown>;
      const armedStart =
        typeof clock.replay_date === "string" ? clock.replay_date : range.start;
      const armed: ReplayRange = { start: armedStart, end: range.end };
      setReplayRange(armed);
      setArmedRange(armed);
    } catch (err) {
      setReplayError(err instanceof Error ? err.message : "Failed to arm replay");
    } finally {
      setArmingDay(null);
    }
  };

  const handleSimulatorStopped = useCallback(() => {
    setArmedRange(null);
  }, []);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Stock Simulator</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Record a live trading day from INDmoney (real option-chain greeks/OI/IV, market depth,
          index ticks), then replay that day through the simulator.
        </p>
      </div>

      {/* Primary chart symbol: any index, or any NIFTY-50 constituent —
          independent of what's checked for recording below. This only
          controls what the chart/chain show, not what gets recorded. */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          Chart symbol
          <select
            value={`${primarySymbol.exchange}:${primarySymbol.symbol}`}
            onChange={(e) => {
              const [exchange, symbol] = e.target.value.split(":");
              setPrimarySymbol({ symbol, exchange });
            }}
            className="max-w-[220px] rounded border bg-background px-1.5 py-1 text-xs"
            data-testid="simulator-primary-symbol"
          >
            <optgroup label="Indices">
              {UNDERLYINGS.map((u) => (
                <option key={u} value={`NSE_INDEX:${u}`}>
                  {u}
                </option>
              ))}
            </optgroup>
            {nifty50.length > 0 && (
              <optgroup label="NIFTY 50 equities">
                {[...nifty50]
                  .sort((a, b) => a.symbol.localeCompare(b.symbol))
                  .map((c) => (
                    <option key={c.symbol} value={`NSE:${c.symbol}`}>
                      {c.symbol} — {c.name}
                    </option>
                  ))}
              </optgroup>
            )}
          </select>
        </label>
      </div>

      {marketOpen === false && !armedRange && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          <span>
            Market is closed right now.
            {calendarDays[0]
              ? ` Replay the most recent recorded session (${calendarDays[0].date}) to see it moving.`
              : " Record a full session (below) to enable replay."}
          </span>
          {calendarDays[0] ? (
            <button
              type="button"
              onClick={() => armReplay({ start: calendarDays[0].date, end: calendarDays[0].date })}
              disabled={Boolean(armingDay)}
              className="shrink-0 rounded border border-amber-500/40 px-2 py-0.5 font-medium hover:bg-amber-500/10 disabled:opacity-50"
              data-testid="replay-latest-day"
            >
              {armingDay ? "Arming…" : `Replay ${calendarDays[0].date}`}
            </button>
          ) : null}
        </div>
      )}

      {/* Phase 9: live index panel + option chain toggle. Follows the
          "Chart symbol" selector above. */}
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1 basis-full sm:basis-0">
          <SimulatorLiveIndexPanel
            symbol={primarySymbol.symbol}
            exchange={primarySymbol.exchange}
            isRecordingActive={isActive}
            isReplayArmed={Boolean(armedRange)}
            height={240}
            onSessionOpenChange={setMarketOpen}
          />
        </div>
        <button
          type="button"
          onClick={() => setShowChain(true)}
          className="inline-flex h-9 items-center gap-1.5 self-start rounded-lg border bg-background px-3 text-sm hover:bg-muted/50"
          title="Show live option chain"
          data-testid="open-option-chain"
        >
          <ListOrdered className="h-3.5 w-3.5" />
          Option Chain
        </button>
      </div>
      <SimulatorOptionChainPanel
        symbol={primarySymbol.symbol}
        exchange={primarySymbol.exchange}
        open={showChain}
        onClose={() => setShowChain(false)}
        recordingActive={isActive}
        isReplayArmed={Boolean(armedRange)}
      />

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <StatCard title="Record">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <span title={isActive ? "Stop the recording to change what's being recorded" : undefined}>
              <SimulatorInstrumentPicker
                indices={UNDERLYINGS}
                selectedIndices={selected}
                onChangeIndices={setSelected}
                selectedEquities={selectedEquities}
                onChangeEquities={setSelectedEquities}
                disabled={isActive}
              />
            </span>
            <label
              className="flex items-center gap-1.5 text-sm text-muted-foreground"
              title={isActive ? "Stop the recording to change this" : undefined}
            >
              <input
                type="checkbox"
                checked={waitForOpen}
                disabled={isActive}
                onChange={(e) => setWaitForOpen(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-border"
              />
              Wait for market open
            </label>
          </div>
          <div className="flex items-center gap-2">
            {statusBadge(displayStatus)}
            {isWaitingForOpen ? (
              <span
                className="text-[11px] text-amber-800 dark:text-amber-200"
                title={`Next NSE open at ${nextOpenAtLabel}`}
              >
                {nextOpenAtLabel === "—"
                  ? "Awaiting next NSE open"
                  : `Next open at ${nextOpenAtLabel.slice(11, 16)} IST`}
              </span>
            ) : null}
            {isActive ? (
              <button
                type="button"
                onClick={stopRecording}
                disabled={busy}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-red-500/40 bg-background px-3 text-sm text-red-600 hover:bg-red-500/10 disabled:opacity-50"
              >
                <Square className="h-3.5 w-3.5" />
                Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={startRecording}
                disabled={busy || selected.length + selectedEquities.length === 0}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                <Disc className="h-3.5 w-3.5" />
                Start Recording
              </button>
            )}
          </div>
        </div>

        <RecordingConfigProvider
          config={{
            categoryIntervals,
            equityIntervals,
            wsThrottleHz,
            historicalConfig,
            setCategoryInterval,
            setEquityInterval,
            setWsThrottle,
            setHistoricalConfig,
          }}
        >
          <SimulatorRecordingFields disabled={isActive} />
        </RecordingConfigProvider>
        {job ? (
          <div className="mt-4 space-y-3">
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span>
                {job.underlyings?.join(", ") || "—"} · session {job.session_date || "today"}
              </span>
              <span>{Math.round((job.session_pct_complete ?? 0) * 100)}% of trading day recorded</span>
            </div>
            <ProgressBar pct={job.session_pct_complete} />

            <div
              ref={logRef}
              className="h-56 overflow-auto rounded-lg border bg-background/60 p-3 font-mono text-[11px] leading-relaxed"
            >
              {logs.length === 0 ? (
                <p className="text-muted-foreground">No log entries yet.</p>
              ) : (
                logs.map((entry, i) => (
                  <div key={i} className={cn("flex gap-2", logLevelColor(entry.level))}>
                    <span className="shrink-0 text-muted-foreground/60">
                      {entry.at ? new Date(entry.at).toLocaleTimeString() : ""}
                    </span>
                    <span>{entry.message}</span>
                  </div>
                ))
              )}
            </div>

            {job.status === "error" && job.error ? (
              <p className="text-sm text-destructive">{job.error}</p>
            ) : null}
            {job.status === "done" && job.result ? (
              <p className="text-sm text-muted-foreground">
                Stopped: {job.result.stopped_reason} · {job.result.cycles} cycles
                {job.result.errors?.length ? ` · ${job.result.errors.length} errors` : ""}
              </p>
            ) : null}
          </div>
        ) : (
          <p className="mt-3 text-sm text-muted-foreground">
            No recording in progress. Recording stops automatically at market close (15:30 IST).
          </p>
        )}
      </StatCard>

      <StatCard title="Replay">
        <div className="space-y-4">
          {replayNotConfigured ? (
            <div className="rounded-lg border border-muted-foreground/20 bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
              Replay not available — {replayNotConfigured}
            </div>
          ) : calendarError ? (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
              <span>{calendarError}</span>
              <button
                type="button"
                onClick={loadCalendar}
                className="shrink-0 rounded border border-destructive/40 px-2 py-0.5 text-[10px] font-medium hover:bg-destructive/10"
              >
                Retry
              </button>
            </div>
          ) : null}

          <SimulatorReplayCalendar
            days={calendarDays}
            range={replayRange}
            armedRange={armedRange}
            onRangeSelect={(range) => setReplayRange(range)}
            onDaySelect={setSelectedReplayDay}
            selectedDate={selectedReplayDay?.date ?? null}
          />

          <SimulatorReplayClock
            armedRange={armedRange}
            onStop={handleSimulatorStopped}
          />

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background/60 p-4">
            <div className="flex flex-wrap items-center gap-4 text-xs">
              <label className="flex items-center gap-1.5">
                Speed
                <select
                  value={replaySpeed}
                  onChange={(e) => setReplaySpeed(Number(e.target.value))}
                  disabled={Boolean(armedRange)}
                  className="rounded border bg-background px-1.5 py-0.5 text-xs"
                  data-testid="simulator-speed"
                >
                  {[0.5, 1, 2, 5, 10, 25, 50].map((s) => (
                    <option key={s} value={s}>{s}×</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={replayLoop}
                  onChange={(e) => setReplayLoop(e.target.checked)}
                  disabled={Boolean(armedRange)}
                  className="h-3.5 w-3.5 rounded border-border"
                  data-testid="simulator-loop"
                />
                {/* While armed, describe what's actually looping (armedRange),
                    not the pending calendar selection (replayRange) — the
                    calendar stays clickable while armed, so replayRange can
                    drift away from what this checkbox (disabled) controls. */}
                Loop {(() => {
                  const active = armedRange ?? replayRange;
                  return active && active.start !== active.end ? "range" : "at 15:30";
                })()}
              </label>
            </div>
            <button
              type="button"
              onClick={() => replayRange && armReplay(replayRange)}
              disabled={!replayRange || Boolean(armingDay) || Boolean(armedRange)}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              data-testid="simulator-arm"
            >
              <PlayCircle className="h-3.5 w-3.5" />
              {armingDay
                ? "Arming…"
                : armedRange
                ? `Armed · ${armedRange.start}${armedRange.end !== armedRange.start ? ` → ${armedRange.end}` : ""}`
                : replayRange
                ? `Arm replay · ${replayRange.start}${replayRange.end !== replayRange.start ? ` → ${replayRange.end}` : ""}`
                : "Select a date"}
            </button>
          </div>

          {replayError ? <p className="text-[11px] text-destructive">{replayError}</p> : null}
          <p className="text-[11px] text-muted-foreground">
            Configures the simulator's replay clock on the running OpenAlgo instance
            directly — no restart needed. OpenAlgo then serves quotes from this clock
            as if it were the live market; the chart in the option chain panel updates
            accordingly.
          </p>
        </div>
      </StatCard>

      <SimulatorReplayDetailPanel day={selectedReplayDay} onClose={() => setSelectedReplayDay(null)} />

      <StatCard title="Data coverage">
        <p className="mb-3 text-[11px] text-muted-foreground">
          Per-week bucket availability for the simulator. White cells = data
          missing; click a cell to backfill it via the registered writer.
        </p>
        <StockHistoryCoveragePanel includeOptional />
      </StatCard>
    </div>
  );
}
