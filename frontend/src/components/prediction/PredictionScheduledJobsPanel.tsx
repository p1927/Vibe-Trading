import { useCallback, useEffect, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type IndexPredictionJob, type IndexPredictionJobsResponse } from "@/lib/api";

function fmtNextRun(ms: number | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  try {
    return new Date(ms).toLocaleString("en-IN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

export function PredictionScheduledJobsPanel() {
  const [jobs, setJobs] = useState<IndexPredictionJob[]>([]);
  const [env, setEnv] = useState<IndexPredictionJobsResponse["env"]>({});
  const [masterEnvOn, setMasterEnvOn] = useState(false);
  const [executorRunning, setExecutorRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getIndexPredictionJobs();
      setJobs(res.jobs ?? []);
      setEnv(res.env ?? {});
      setMasterEnvOn(Boolean(res.master_scheduler_env_enabled ?? res.env?.vibe_trading_enable_scheduler));
      setExecutorRunning(Boolean(res.executor_is_running ?? res.master_scheduler_running));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load scheduled jobs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (job: IndexPredictionJob) => {
    const id = job.id;
    if (!id) return;
    setBusyId(id);
    setError(null);
    try {
      if (job.paused || job.status === "cancelled" || job.status === "failed") {
        await api.resumeIndexPredictionJob(id);
      } else {
        await api.pauseIndexPredictionJob(id);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Job update failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Scheduled jobs (prediction pipeline)
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Cron jobs that refresh factors, archive research, and retrain the model. Pause any job you do not need.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted/50"
        >
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
          Refresh
        </button>
      </div>

      <div className="mb-3 flex flex-wrap gap-2 text-[10px]">
        <span
          className={cn(
            "rounded-full px-2 py-0.5",
            masterEnvOn ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground",
          )}
        >
          Master scheduler: {masterEnvOn ? "ENABLED" : "DISABLED"} (VIBE_TRADING_ENABLE_SCHEDULER)
        </span>
        <span
          className={cn(
            "rounded-full px-2 py-0.5",
            executorRunning ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground",
          )}
        >
          Executor: {executorRunning ? "RUNNING" : masterEnvOn ? "NOT STARTED" : "OFF"}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
          Index jobs: {env?.index_research_enable_scheduler ? "registered" : "not registered"}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
          Live monitor: {env?.index_monitor_enable_scheduler ? "ON" : "OFF"}
        </span>
      </div>

      {!masterEnvOn ? (
        <p className="mb-3 text-[11px] text-amber-700 dark:text-amber-400">
          Master scheduler is disabled in env — jobs are listed but will not run until
          VIBE_TRADING_ENABLE_SCHEDULER=1 and the API is restarted.
        </p>
      ) : !executorRunning ? (
        <p className="mb-3 text-[11px] text-amber-700 dark:text-amber-400">
          Master scheduler is enabled but the executor is not running — restart the API or check startup logs.
        </p>
      ) : null}

      {error ? <p className="mb-2 text-[11px] text-red-600 dark:text-red-400">{error}</p> : null}

      {loading && !jobs.length ? (
        <p className="text-[11px] text-muted-foreground">Loading scheduled jobs…</p>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => {
            const paused = job.paused || job.status === "cancelled";
            const failed = job.status === "failed";
            const showResume = paused || failed;
            return (
              <div
                key={job.id}
                className={cn(
                  "flex flex-col gap-2 rounded-lg border px-3 py-2 sm:flex-row sm:items-center sm:justify-between",
                  showResume && "opacity-70",
                  failed && "border-red-500/30",
                )}
              >
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-medium">{job.label || job.id}</p>
                  <p className="text-[10px] text-muted-foreground">{job.description}</p>
                  <p
                    className={cn(
                      "mt-0.5 font-mono text-[10px]",
                      failed ? "text-red-600 dark:text-red-400" : "text-muted-foreground",
                    )}
                  >
                    {job.schedule} · next {fmtNextRun(job.next_run_at)} · {job.status}
                    {failed ? " — click Resume to re-enable polling" : ""}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={busyId === job.id}
                  onClick={() => void toggle(job)}
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-[10px]",
                    showResume
                      ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-400"
                      : "border-amber-500/40 text-amber-800 dark:text-amber-300",
                  )}
                >
                  {showResume ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
                  {showResume ? "Resume" : "Pause"}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
