import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  streamExternalPredictionsJob,
  type ExternalPredictionSnapshot,
  type ExternalPredictionsRefreshJobSnapshot,
  type PipelineLogEntry,
} from "@/lib/api";

const REFRESH_TIMEOUT_MS = 12 * 60 * 1000;
const API_START_TIMEOUT_MS = 15 * 1000;
const RUN_JOB_STORAGE_PREFIX = "vibe-external-predictions-run-job:";
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

export type ExternalRefreshPhase = "idle" | "starting" | "running" | "reattaching";

function runJobStorageKey(ticker: string, horizonDays: number) {
  return `${RUN_JOB_STORAGE_PREFIX}${ticker.toUpperCase()}:${horizonDays}`;
}

function readStoredRunJobId(ticker: string, horizonDays: number): string | null {
  try {
    return sessionStorage.getItem(runJobStorageKey(ticker, horizonDays));
  } catch {
    return null;
  }
}

function writeStoredRunJobId(ticker: string, horizonDays: number, jobId: string) {
  try {
    sessionStorage.setItem(runJobStorageKey(ticker, horizonDays), jobId);
  } catch {
    /* ignore */
  }
}

function clearStoredRunJobId(ticker: string, horizonDays: number) {
  try {
    sessionStorage.removeItem(runJobStorageKey(ticker, horizonDays));
  } catch {
    /* ignore */
  }
}

async function fetchJobSnapshot(
  jobId: string,
  signal?: AbortSignal,
): Promise<ExternalPredictionsRefreshJobSnapshot | null> {
  try {
    const res = await api.getExternalPredictionsRefreshJob(jobId, signal);
    return res.job ?? null;
  } catch {
    return null;
  }
}

function apiUnreachableMessage(err: unknown): string {
  if (err instanceof DOMException && err.name === "AbortError") {
    return "API did not respond in time — it may be reloading after a code change. Wait a few seconds, then try Refresh again.";
  }
  const msg = err instanceof Error ? err.message : String(err);
  if (msg.includes("Failed to fetch") || msg.includes("Network error") || msg.includes("NetworkError")) {
    return "Cannot reach the Vibe API (port 8899). It may be stuck reloading — check the terminal or restart ./trade dev.";
  }
  return msg || "Refresh failed";
}

export function useExternalPredictions(horizonDays: number, enabled = true) {
  const [snapshot, setSnapshot] = useState<ExternalPredictionSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshPhase, setRefreshPhase] = useState<ExternalRefreshPhase>("idle");
  const [refreshLogs, setRefreshLogs] = useState<PipelineLogEntry[]>([]);
  const [runJobId, setRunJobId] = useState<string | null>(null);
  const [reattached, setReattached] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const attachGenRef = useRef(0);
  const reattachCheckedRef = useRef(false);

  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getExternalPredictions("NIFTY", horizonDays);
      setSnapshot(res.snapshot ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load external predictions");
    } finally {
      setLoading(false);
    }
  }, [enabled, horizonDays]);

  const finishRefresh = useCallback((tickerKey: string, hz: number) => {
    clearStoredRunJobId(tickerKey, hz);
    setRunJobId(null);
    setReattached(false);
    setRefreshing(false);
    setRefreshPhase("idle");
  }, []);

  const applyTerminalJob = useCallback(
    (jobSnapshot: ExternalPredictionsRefreshJobSnapshot, tickerKey: string, hz: number) => {
      if (jobSnapshot.logs?.length) {
        setRefreshLogs(jobSnapshot.logs);
      }
      if (jobSnapshot.status === "done" && jobSnapshot.snapshot) {
        setSnapshot(jobSnapshot.snapshot);
        setError(null);
      } else if (jobSnapshot.status === "error") {
        setError(jobSnapshot.error || "Refresh failed");
      }
      finishRefresh(tickerKey, hz);
    },
    [finishRefresh],
  );

  const attachToJob = useCallback(
    async (
      tickerKey: string,
      hz: number,
      jobId: string,
      gen: number,
      options?: { reattach?: boolean; clearLogs?: boolean },
    ) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setRefreshing(true);
      setRefreshPhase(options?.reattach ? "reattaching" : "running");
      setRunJobId(jobId);
      setReattached(Boolean(options?.reattach));
      writeStoredRunJobId(tickerKey, hz, jobId);

      if (options?.clearLogs !== false) {
        setRefreshLogs([]);
      }

      const jobSnapshot = await fetchJobSnapshot(jobId);
      if (gen !== attachGenRef.current) return;

      if (jobSnapshot?.logs?.length) {
        if (options?.clearLogs === false || options?.reattach) {
          setRefreshLogs(jobSnapshot.logs);
        }
      }

      if (jobSnapshot && !ACTIVE_JOB_STATUSES.has(jobSnapshot.status)) {
        applyTerminalJob(jobSnapshot, tickerKey, hz);
        return;
      }

      const timeoutId = window.setTimeout(() => {
        controller.abort();
      }, REFRESH_TIMEOUT_MS);

      try {
        await streamExternalPredictionsJob(
          jobId,
          {
            onLog: (entry) => {
              if (gen !== attachGenRef.current) return;
              setRefreshPhase("running");
              setRefreshLogs((prev) => [...prev, entry]);
            },
            onDone: (nextSnapshot) => {
              if (gen !== attachGenRef.current) return;
              setSnapshot(nextSnapshot);
              setError(null);
              finishRefresh(tickerKey, hz);
            },
            onError: (message) => {
              if (gen !== attachGenRef.current) return;
              setError(message);
              finishRefresh(tickerKey, hz);
            },
          },
          controller.signal,
        );
      } catch (e) {
        if (controller.signal.aborted) {
          if (gen === attachGenRef.current) {
            const recovered = await fetchJobSnapshot(jobId);
            if (recovered && !ACTIVE_JOB_STATUSES.has(recovered.status)) {
              applyTerminalJob(recovered, tickerKey, hz);
              return;
            }
            setError("Refresh timed out or was interrupted — try again.");
            finishRefresh(tickerKey, hz);
          }
          return;
        }
        if (gen !== attachGenRef.current) return;
        const recovered = await fetchJobSnapshot(jobId);
        if (recovered && !ACTIVE_JOB_STATUSES.has(recovered.status)) {
          applyTerminalJob(recovered, tickerKey, hz);
          return;
        }
        setError(apiUnreachableMessage(e));
        finishRefresh(tickerKey, hz);
      } finally {
        window.clearTimeout(timeoutId);
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    },
    [applyTerminalJob, finishRefresh],
  );

  const tryReattach = useCallback(
    async (tickerKey: string, hz: number) => {
      const lookupSignal = AbortSignal.timeout(API_START_TIMEOUT_MS);
      try {
        const active = await api.getActiveExternalPredictionsRefresh(tickerKey, hz, lookupSignal);
        const job = active.job;
        if (job?.job_id && ACTIVE_JOB_STATUSES.has(job.status)) {
          setRefreshing(true);
          setRefreshPhase("reattaching");
          if (job.logs?.length) {
            setRefreshLogs(job.logs);
          }
          const gen = ++attachGenRef.current;
          await attachToJob(tickerKey, hz, job.job_id, gen, { reattach: true, clearLogs: false });
          return;
        }
        clearStoredRunJobId(tickerKey, hz);

        const stored = readStoredRunJobId(tickerKey, hz);
        if (stored) {
          const jobSnapshot = await fetchJobSnapshot(stored, lookupSignal);
          if (jobSnapshot?.job_id && ACTIVE_JOB_STATUSES.has(jobSnapshot.status)) {
            setRefreshing(true);
            setRefreshPhase("reattaching");
            if (jobSnapshot.logs?.length) {
              setRefreshLogs(jobSnapshot.logs);
            }
            const gen = ++attachGenRef.current;
            await attachToJob(tickerKey, hz, jobSnapshot.job_id, gen, {
              reattach: true,
              clearLogs: false,
            });
            return;
          }
          if (jobSnapshot) {
            applyTerminalJob(jobSnapshot, tickerKey, hz);
            return;
          }
          clearStoredRunJobId(tickerKey, hz);
        }
      } catch (e) {
        const stored = readStoredRunJobId(tickerKey, hz);
        if (stored) {
          const jobSnapshot = await fetchJobSnapshot(stored);
          if (jobSnapshot?.job_id && ACTIVE_JOB_STATUSES.has(jobSnapshot.status)) {
            setRefreshing(true);
            setRefreshPhase("reattaching");
            if (jobSnapshot.logs?.length) {
              setRefreshLogs(jobSnapshot.logs);
            }
            const gen = ++attachGenRef.current;
            await attachToJob(tickerKey, hz, jobSnapshot.job_id, gen, {
              reattach: true,
              clearLogs: false,
            });
            return;
          }
          if (jobSnapshot) {
            applyTerminalJob(jobSnapshot, tickerKey, hz);
            return;
          }
          clearStoredRunJobId(tickerKey, hz);
        }
        if (stored) {
          setError(apiUnreachableMessage(e));
        }
      }
    },
    [applyTerminalJob, attachToJob],
  );

  const refresh = useCallback(async () => {
    if (!enabled) return;
    const tickerKey = "NIFTY";
    const gen = ++attachGenRef.current;
    setError(null);
    setRefreshing(true);
    setRefreshPhase("starting");

    const startController = new AbortController();
    const startTimeoutId = window.setTimeout(() => startController.abort(), API_START_TIMEOUT_MS);

    try {
      const start = await api.startExternalPredictionsRefresh(
        tickerKey,
        horizonDays,
        startController.signal,
      );
      if (gen !== attachGenRef.current) return;
      await attachToJob(tickerKey, horizonDays, start.job_id, gen, {
        reattach: Boolean(start.reused),
        clearLogs: !start.reused,
      });
    } catch (e) {
      if (gen !== attachGenRef.current) return;
      setError(apiUnreachableMessage(e));
      setRefreshing(false);
      setRefreshPhase("idle");
    } finally {
      window.clearTimeout(startTimeoutId);
    }
  }, [attachToJob, enabled, horizonDays]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    reattachCheckedRef.current = false;
  }, [horizonDays]);

  useEffect(() => {
    if (!enabled) return;
    if (reattachCheckedRef.current) return;
    reattachCheckedRef.current = true;

    const tickerKey = "NIFTY";
    let cancelled = false;

    void (async () => {
      await tryReattach(tickerKey, horizonDays);
      if (cancelled) return;
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled, horizonDays, tryReattach]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return {
    snapshot,
    loading,
    refreshing,
    refreshPhase,
    refreshLogs,
    runJobId,
    reattached,
    error,
    load,
    refresh,
  };
}
