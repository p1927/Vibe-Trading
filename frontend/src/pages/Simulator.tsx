import { useCallback, useEffect, useRef, useState } from "react";
import { Circle, Disc, ListOrdered, PlayCircle, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  api,
  type PipelineLogEntry,
  type RecordingJobSnapshot,
  type RecordingResult,
} from "@/lib/api";
import { SimulatorLiveIndexPanel } from "@/components/simulator/SimulatorLiveIndexPanel";
import { SimulatorOptionChainPanel } from "@/components/simulator/SimulatorOptionChainPanel";

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
    running: "bg-blue-500/15 text-blue-800 dark:text-blue-200",
    done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    error: "bg-red-500/15 text-red-800 dark:text-red-200",
    idle: "bg-muted text-muted-foreground",
  };
  const labels: Record<string, string> = {
    queued: "Starting…",
    waiting: "Waiting for Market Open",
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
  const [waitForOpen, setWaitForOpen] = useState(false);
  const [job, setJob] = useState<RecordingJobSnapshot | null>(null);
  const [logs, setLogs] = useState<PipelineLogEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<string[]>([]);
  const [replayingDay, setReplayingDay] = useState<string | null>(null);
  const [replayStatus, setReplayStatus] = useState<Record<string, unknown> | null>(null);
  // Phase 9: option-chain drawer toggle.
  const [showChain, setShowChain] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  const isActive = job?.status === "queued" || job?.status === "running";
  const isWaiting = job?.status === "running" && logs[logs.length - 1]?.stage === "waiting";
  const displayStatus = isWaiting ? "waiting" : job?.status;

  const loadSessions = useCallback(() => {
    api
      .listRecordingSessions()
      .then((res) => setSessions(res.sessions || []))
      .catch(() => {});
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
            loadSessions();
          },
          onError: (message) => {
            setJob((prev) => (prev ? { ...prev, status: "error", error: message } : prev));
          },
        },
        controller.signal,
      )
      .catch(() => {});
  }, [loadSessions]);

  useEffect(() => {
    loadSessions();
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

  const toggleUnderlying = (u: string) => {
    setSelected((prev) => (prev.includes(u) ? prev.filter((x) => x !== u) : [...prev, u]));
  };

  const startRecording = async () => {
    setBusy(true);
    setError(null);
    setLogs([]);
    try {
      const res = await api.startRecording({
        underlyings: selected,
        poll_interval_s: 10,
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
    setBusy(true);
    try {
      await api.stopRecording(job.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop recording");
    } finally {
      setBusy(false);
    }
  };

  const startReplay = async (day: string) => {
    setReplayingDay(day);
    setReplayError(null);
    try {
      const res = await api.startReplay(day);
      setReplayStatus(res.replay ?? null);
    } catch (err) {
      setReplayError(err instanceof Error ? err.message : "Failed to start replay");
    } finally {
      setReplayingDay(null);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Stock Simulator</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Record a live trading day from INDmoney (real option-chain greeks/OI/IV, market depth,
          index ticks), then replay that day through the simulator.
        </p>
      </div>

      {/* Phase 9: live index panel + option chain toggle. Auto-switches
          underlying when `selected[0]` changes (driven by Record checkboxes). */}
      <div className="flex flex-wrap items-start gap-3">
        <SimulatorLiveIndexPanel
          symbol={selected[0] ?? "NIFTY"}
          exchange="NSE_INDEX"
          isRecordingActive={isActive}
        />
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
        symbol={selected[0] ?? "NIFTY"}
        exchange="NSE_INDEX"
        open={showChain}
        onClose={() => setShowChain(false)}
        recordingActive={isActive}
      />

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <StatCard title="Record">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            {UNDERLYINGS.map((u) => (
              <label key={u} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={selected.includes(u)}
                  disabled={isActive}
                  onChange={() => toggleUnderlying(u)}
                  className="h-3.5 w-3.5 rounded border-border"
                />
                {u}
              </label>
            ))}
            <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
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
                disabled={busy || selected.length === 0}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                <Disc className="h-3.5 w-3.5" />
                Start Recording
              </button>
            )}
          </div>
        </div>

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
        {sessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No recorded sessions yet. Record a full trading day to enable replay.
          </p>
        ) : (
          <ul className="divide-y divide-border/60">
            {sessions.map((day) => (
              <li key={day} className="flex items-center justify-between py-2 text-sm">
                <span className="flex items-center gap-2">
                  <Circle className="h-2 w-2 fill-emerald-500 text-emerald-500" />
                  {day}
                </span>
                <button
                  type="button"
                  onClick={() => startReplay(day)}
                  disabled={replayingDay === day}
                  className="inline-flex h-7 items-center gap-1.5 rounded-md border bg-background px-2.5 text-xs hover:bg-muted/50 disabled:opacity-50"
                >
                  <PlayCircle className="h-3 w-3" />
                  {replayingDay === day ? "Starting…" : "Start Replay"}
                </button>
              </li>
            ))}
          </ul>
        )}

        {replayError ? <p className="mt-3 text-sm text-destructive">{replayError}</p> : null}
        {replayStatus ? (
          <div className="mt-3 rounded-lg border bg-background/60 p-3 text-[11px]">
            <p className="font-medium text-foreground">Replay armed on OpenAlgo</p>
            <pre className="mt-1 overflow-auto text-muted-foreground">
              {JSON.stringify(replayStatus, null, 2)}
            </pre>
          </div>
        ) : null}
        <p className="mt-3 text-[11px] text-muted-foreground">
          Starts the simulator's replay clock on the running OpenAlgo instance directly — no
          restart needed. Watch it in OpenAlgo's own UI once armed (Option Chain / quotes on the
          stock_simulator broker).
        </p>
      </StatCard>
    </div>
  );
}
