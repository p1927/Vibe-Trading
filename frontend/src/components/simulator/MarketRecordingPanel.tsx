import { useCallback, useEffect, useState } from "react";
import { Disc, Square } from "lucide-react";
import { api, type TickRecordingJob } from "@/lib/api";

/** Start/stop UI for `stock_simulator`'s tick-recording jobs (append-only ticks into the
 * generic `market_ticks` table), scoped to one market tab. `kind="index"` records a country's
 * headline indices (market-hours gated); `kind="fx"` records USD-anchored currency pairs
 * (no gate, trades ~24/5). In-memory job state on the simulator side — a simulator restart
 * silently drops any running job, so this polls `/active` rather than trusting local state alone. */
export function MarketRecordingPanel({
  kind,
  country,
  label,
}: {
  kind: "index" | "fx";
  country?: string;
  label: string;
}) {
  const [jobs, setJobs] = useState<TickRecordingJob[]>([]);
  const [intervalSeconds, setIntervalSeconds] = useState(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .getActiveMarketTickRecordings()
      .then((res) => {
        const all = res.jobs ?? [];
        setJobs(all.filter((j) => j.kind === kind && (kind === "fx" || j.country === country)));
      })
      .catch(() => {});
  }, [kind, country]);

  useEffect(() => {
    refresh();
    const interval = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.startMarketTickRecording({
        kind,
        country: kind === "index" ? country : undefined,
        interval_seconds: intervalSeconds,
      });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start recording");
    } finally {
      setBusy(false);
    }
  };

  const stop = async (jobId: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.stopMarketTickRecording(jobId);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop recording");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          Poll every
          <input
            type="number"
            min={5}
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(Math.max(5, Number(e.target.value) || 5))}
            className="w-16 rounded border bg-background px-1.5 py-1 text-xs"
          />
          seconds
        </label>
        <button
          type="button"
          onClick={start}
          disabled={busy}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Disc className="h-3.5 w-3.5" />
          Start recording {label}
        </button>
      </div>

      {error ? <p className="text-[11px] text-destructive">{error}</p> : null}

      {jobs.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No {label} recording in progress. {kind === "index" ? "Only polls while the market is open." : "FX polls around the clock."}
        </p>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => (
            <div
              key={job.job_id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-background/60 p-2.5 text-[11px]"
            >
              <div className="min-w-0">
                <p className="font-medium text-foreground">
                  {job.symbols?.join(", ") || "—"} · every {job.interval_seconds}s
                </p>
                <p className="text-muted-foreground">
                  {job.polls} polls · {job.errors} errors
                  {job.last_error ? ` · ${job.last_error}` : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() => stop(job.job_id)}
                disabled={busy}
                className="inline-flex h-7 shrink-0 items-center gap-1 rounded border border-red-500/40 bg-background px-2 text-[11px] text-red-600 hover:bg-red-500/10 disabled:opacity-50"
              >
                <Square className="h-3 w-3" />
                Stop
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
