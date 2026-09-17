import { useEffect, useRef, useState } from "react";
import { Loader2, Play } from "lucide-react";
import {
  api,
  type ForecastEngineRunLogEntry,
  type ForecastEngineRunResult,
  type ForecastEngineRunSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  ticker?: string;
  recipe: string;
  horizon?: number;
  /** Called when a past run row is clicked, so the parent can point
   * `ForecastEnginePredictedVsActualChart` at that run's detail. */
  onSelectRun?: (runId: string | null) => void;
  selectedRunId?: string | null;
}

/** Pick a start/end date range, kick off an on-demand forecast_engine evaluation run, watch it
 * work live (SSE log stream), and browse past runs for this recipe — the on-demand counterpart
 * to the always-overwritten "latest" evaluation `ForecastEnginePredictedVsActualChart` shows by
 * default (docs/add/forecast_engine.md). */
export function ForecastEngineRunPanel({ ticker = "NIFTY", recipe, horizon = 5, onSelectRun, selectedRunId }: Props) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "queued" | "running" | "done" | "error">("idle");
  const [logs, setLogs] = useState<ForecastEngineRunLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<ForecastEngineRunSummary[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const running = status === "queued" || status === "running";

  async function loadRuns() {
    setLoadingRuns(true);
    try {
      const res = await api.getForecastEngineRuns(ticker, recipe);
      setRuns(res.runs ?? []);
    } catch {
      setRuns([]);
    } finally {
      setLoadingRuns(false);
    }
  }

  useEffect(() => {
    void loadRuns();
    setJobId(null);
    setStatus("idle");
    setLogs([]);
    setError(null);
  }, [ticker, recipe]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  async function handleRun() {
    if (!start || !end || running) return;
    setError(null);
    setLogs([]);
    setStatus("queued");
    try {
      const res = await api.startForecastEngineRun({ ticker, recipe, start, end, horizon });
      setJobId(res.job_id);
      setStatus(res.job_status === "done" ? "done" : res.job_status === "error" ? "error" : "running");

      const controller = new AbortController();
      abortRef.current = controller;
      await api.streamForecastEngineRunJob(
        res.job_id,
        {
          onLog: (entry) => setLogs((prev) => [...prev, entry]),
          onDone: (result: ForecastEngineRunResult) => {
            setStatus("done");
            void loadRuns();
            onSelectRun?.(result.run_id);
          },
          onError: (message) => {
            setStatus("error");
            setError(message);
          },
        },
        controller.signal,
      );
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : "Failed to start run");
    }
  }

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Run a new backtest
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          Start
          <input
            type="date"
            value={start}
            onChange={(event) => setStart(event.target.value)}
            className="rounded-md border bg-background px-3 py-2 text-sm"
            aria-label="Run start date"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          End
          <input
            type="date"
            value={end}
            onChange={(event) => setEnd(event.target.value)}
            className="rounded-md border bg-background px-3 py-2 text-sm"
            aria-label="Run end date"
          />
        </label>
        <button
          type="button"
          onClick={() => void handleRun()}
          disabled={!start || !end || running}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition disabled:opacity-50"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {running ? "Running…" : "Run"}
        </button>
      </div>

      {jobId && (status === "running" || status === "queued" || logs.length > 0) ? (
        <div
          ref={logRef}
          className="mt-3 max-h-32 overflow-y-auto rounded-md border bg-muted/30 p-2 font-mono text-[10px] leading-relaxed text-muted-foreground"
        >
          {logs.length === 0 ? (
            <p>Waiting for the worker to start…</p>
          ) : (
            logs.map((entry, i) => (
              <p key={i} className={entry.level === "error" ? "text-red-600 dark:text-red-400" : undefined}>
                {entry.message}
              </p>
            ))
          )}
        </div>
      ) : null}
      {error ? <p className="mt-2 text-[11px] text-red-600 dark:text-red-400">{error}</p> : null}

      <p className="mt-4 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Past runs
      </p>
      {loadingRuns ? (
        <p className="mt-2 text-[11px] text-muted-foreground">Loading past runs…</p>
      ) : runs.length === 0 ? (
        <p className="mt-2 text-[11px] text-muted-foreground">No runs yet for this recipe.</p>
      ) : (
        <div className="mt-2 divide-y divide-border/60 rounded-md border">
          {runs.map((run) => (
            <button
              key={run.run_id}
              type="button"
              onClick={() => onSelectRun?.(run.run_id === selectedRunId ? null : run.run_id)}
              className={cn(
                "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[11px] transition-colors hover:bg-muted/50",
                run.run_id === selectedRunId ? "bg-muted/60" : undefined,
              )}
            >
              <span className="font-medium">
                {run.run_start ?? "?"} → {run.run_end ?? "?"}
              </span>
              <span className="text-muted-foreground">
                {run.n_observations ?? "?"} obs · {run.passes_gate ? "beats naive" : "no edge"} ·{" "}
                {run.generated_at ? new Date(run.generated_at).toLocaleString() : ""}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
