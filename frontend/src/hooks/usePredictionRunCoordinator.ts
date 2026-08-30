import { useCallback, useEffect, useRef } from "react";
import {
  api,
  ApiError,
  streamIndexPredictionJob,
  type IndexPredictionRunJobSnapshot,
} from "@/lib/api";
import { mergePipelineLogs } from "@/lib/pipelineLogUtils";
import { abortPredictionRunClient, usePredictionRunStore } from "@/stores/predictionRun";

const RUN_JOB_STORAGE_PREFIX = "vibe-prediction-run-job:";
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

function runJobStorageKey(ticker: string) {
  return `${RUN_JOB_STORAGE_PREFIX}${ticker.toUpperCase()}`;
}

function readStoredRunJobId(ticker: string): string | null {
  try {
    return sessionStorage.getItem(runJobStorageKey(ticker));
  } catch {
    return null;
  }
}

function writeStoredRunJobId(ticker: string, jobId: string) {
  try {
    sessionStorage.setItem(runJobStorageKey(ticker), jobId);
  } catch {
    /* ignore */
  }
}

function clearStoredRunJobId(ticker: string) {
  try {
    sessionStorage.removeItem(runJobStorageKey(ticker));
  } catch {
    /* ignore */
  }
}

function hydrateLogsFromSnapshot(snapshot: IndexPredictionRunJobSnapshot | null | undefined) {
  const logs = snapshot?.logs ?? [];
  if (logs.length) {
    usePredictionRunStore.getState().setPipelineLogs(logs);
  }
}

/**
 * Result of a job-snapshot lookup, distinguishing "the backend definitively
 * says this job_id is invalid/gone" (404/400 — the stored id is stale and
 * must be cleared) from a transient failure (network error — the caller
 * should not treat the stored id as confirmed-dead just because one probe
 * failed).
 */
type JobSnapshotResult =
  | { kind: "found"; snapshot: IndexPredictionRunJobSnapshot }
  | { kind: "not_found" }
  | { kind: "error" };

async function fetchJobSnapshot(jobId: string): Promise<IndexPredictionRunJobSnapshot | null> {
  const result = await fetchJobSnapshotResult(jobId);
  return result.kind === "found" ? result.snapshot : null;
}

async function fetchJobSnapshotResult(jobId: string): Promise<JobSnapshotResult> {
  try {
    const res = await api.getIndexPredictionRunJob(jobId);
    return res.job ? { kind: "found", snapshot: res.job } : { kind: "not_found" };
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 400)) {
      return { kind: "not_found" };
    }
    return { kind: "error" };
  }
}

function formatStreamError(msg: string): string {
  return msg.includes("Network error") || msg.includes("Failed to fetch")
    ? `${msg} Ensure the API is running on port 8899. For faster runs, leave “Refresh all constituents” unchecked.`
    : msg;
}

/** Module-level refs so runPredictionAnalysis works outside Layout. */
const abortRef = { current: null as AbortController | null };
const attachGenRef = { current: 0 };

export function cancelPredictionRunClient(tickerKey: string, message = "Analysis cancelled") {
  abortRef.current?.abort();
  attachGenRef.current += 1;
  clearStoredRunJobId(tickerKey);
  abortPredictionRunClient(message);
}

/**
 * Layout-level coordinator: owns SSE for index prediction runs so navigation
 * away from /prediction does not abort the stream.
 */
export function usePredictionRunCoordinator(ticker = "NIFTY") {
  const reattachCheckedRef = useRef(false);

  const finishRunLocal = useCallback((tickerKey: string) => {
    clearStoredRunJobId(tickerKey);
    usePredictionRunStore.getState().finishRun();
  }, []);

  const attachToJob = useCallback(
    async (
      tickerKey: string,
      jobId: string,
      gen: number,
      options?: { reattach?: boolean; clearLogs?: boolean },
    ) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Validate the job before ever showing "running" — a stored/candidate
      // job_id (from sessionStorage or a caller's own guess) may point at a
      // job that has already finished, errored, or never existed. Only a
      // definitive not-found/invalid response is treated as stale; a
      // transient lookup error falls through and is treated as active so a
      // genuinely in-flight job (or a job just created by this same call)
      // isn't dropped because of one flaky probe.
      const result = await fetchJobSnapshotResult(jobId);
      if (gen !== attachGenRef.current) return;

      if (result.kind === "not_found") {
        clearStoredRunJobId(tickerKey);
        usePredictionRunStore.getState().finishRun();
        return;
      }

      const snapshot = result.kind === "found" ? result.snapshot : null;

      if (snapshot && !ACTIVE_JOB_STATUSES.has(snapshot.status)) {
        if ((snapshot.status === "done" || snapshot.status === "done_with_warnings") && snapshot.artifact) {
          const s = usePredictionRunStore.getState();
          if (snapshot.warnings?.length) {
            snapshot.artifact.warnings = snapshot.warnings;
          }
          s.setRunArtifact(snapshot.artifact);
          s.setPipelineLogs((prev) => mergePipelineLogs(prev, snapshot.artifact?.pipeline_log));
          finishRunLocal(tickerKey);
          return;
        }
        if (snapshot.status === "error") {
          usePredictionRunStore.getState().setRunError(snapshot.error || "Analysis failed");
          finishRunLocal(tickerKey);
          return;
        }
        clearStoredRunJobId(tickerKey);
        usePredictionRunStore.getState().finishRun();
        return;
      }

      const store = usePredictionRunStore.getState();
      store.setRunning(true);
      store.setRunJobId(jobId);
      store.setReattached(Boolean(options?.reattach));
      writeStoredRunJobId(tickerKey, jobId);

      if (options?.clearLogs !== false) {
        store.setPipelineLogs([]);
      }
      if (snapshot?.logs?.length && options?.clearLogs === false) {
        usePredictionRunStore.getState().setPipelineLogs(snapshot.logs);
      } else if (snapshot?.logs?.length && options?.reattach) {
        hydrateLogsFromSnapshot(snapshot);
      }

      const skipLogsBefore = usePredictionRunStore.getState().getLogCount();

      try {
        await streamIndexPredictionJob(
          jobId,
          {
            onLog: (entry) => {
              if (gen !== attachGenRef.current) return;
              usePredictionRunStore.getState().appendPipelineLog(entry);
            },
            onDone: (next) => {
              if (gen !== attachGenRef.current) return;
              const s = usePredictionRunStore.getState();
              s.setStreamReconnecting(false);
              s.setRunArtifact(next);
              s.setPipelineLogs((prev) => mergePipelineLogs(prev, next.pipeline_log));
              finishRunLocal(tickerKey);
            },
            onError: (message) => {
              if (gen !== attachGenRef.current) return;
              const s = usePredictionRunStore.getState();
              s.setStreamReconnecting(false);
              s.setRunError(message);
              finishRunLocal(tickerKey);
            },
          },
          controller.signal,
          {
            onReconnecting: (reconnecting) => {
              if (gen !== attachGenRef.current) return;
              usePredictionRunStore.getState().setStreamReconnecting(reconnecting);
            },
            skipLogsBefore,
          },
        );
      } catch (e) {
        if (controller.signal.aborted || gen !== attachGenRef.current) {
          finishRunLocal(tickerKey);
          return;
        }
        const msg = e instanceof Error ? e.message : "Analysis failed";
        usePredictionRunStore.getState().setRunError(formatStreamError(msg));
        finishRunLocal(tickerKey);
      }
    },
    [finishRunLocal],
  );

  const tryReattach = useCallback(
    async (tickerKey: string) => {
      try {
        const active = await api.getActiveIndexPredictionRun(tickerKey);
        const job = active.job;
        if (job?.job_id && ACTIVE_JOB_STATUSES.has(job.status)) {
          hydrateLogsFromSnapshot(job);
          const gen = ++attachGenRef.current;
          await attachToJob(tickerKey, job.job_id, gen, { reattach: true, clearLogs: false });
          return;
        }

        // The ticker-wide active-run lookup found nothing, but a
        // per-tab stored job_id might still be a real, active job the
        // active-run index hasn't caught up with yet (or one it doesn't
        // track) — check it directly before deciding it's stale. Clearing
        // sessionStorage here unconditionally, before this check, would
        // make the check below permanently unreachable.
        const stored = readStoredRunJobId(tickerKey);
        if (stored) {
          const snapshot = await fetchJobSnapshot(stored);
          if (snapshot?.job_id && ACTIVE_JOB_STATUSES.has(snapshot.status)) {
            hydrateLogsFromSnapshot(snapshot);
            const gen = ++attachGenRef.current;
            await attachToJob(tickerKey, snapshot.job_id, gen, { reattach: true, clearLogs: false });
            return;
          }
        }
        clearStoredRunJobId(tickerKey);
      } catch {
        const stored = readStoredRunJobId(tickerKey);
        if (stored) {
          const snapshot = await fetchJobSnapshot(stored);
          if (snapshot?.job_id && ACTIVE_JOB_STATUSES.has(snapshot.status)) {
            hydrateLogsFromSnapshot(snapshot);
            const gen = ++attachGenRef.current;
            await attachToJob(tickerKey, snapshot.job_id, gen, { reattach: true, clearLogs: false });
            return;
          }
          clearStoredRunJobId(tickerKey);
        }
      }
    },
    [attachToJob],
  );

  useEffect(() => {
    const key = ticker.toUpperCase();
    usePredictionRunStore.getState().setTicker(key);
  }, [ticker]);

  useEffect(() => {
    reattachCheckedRef.current = false;
  }, [ticker]);

  useEffect(() => {
    if (reattachCheckedRef.current) return;
    reattachCheckedRef.current = true;

    const key = ticker.toUpperCase();
    let cancelled = false;

    void (async () => {
      usePredictionRunStore.getState().setCoordinatorReady(false);
      // Kick off reattach without blocking coordinatorReady — hub reload must
      // proceed while an active job streams in the background.
      const reattachPromise = tryReattach(key);
      if (!cancelled) {
        usePredictionRunStore.getState().setCoordinatorReady(true);
      }
      await reattachPromise;
    })();

    return () => {
      cancelled = true;
    };
  }, [ticker, tryReattach]);

  useRegisterPredictionRunAttach(attachToJob);
}

export async function runPredictionAnalysis(
  ticker: string,
  horizonDays: number,
  refreshConstituents: boolean,
  attachToJob: (
    tickerKey: string,
    jobId: string,
    gen: number,
    options?: { reattach?: boolean; clearLogs?: boolean },
  ) => Promise<void>,
): Promise<void> {
  const key = ticker.toUpperCase();
  abortRef.current?.abort();
  const gen = ++attachGenRef.current;

  usePredictionRunStore.getState().beginRun();

  const body = {
    ticker: key,
    horizon_days: horizonDays,
    refresh_constituents: refreshConstituents,
    run_forecast_lab: true,
  };

  try {
    const start = await api.startIndexPredictionRun(body);
    if (gen !== attachGenRef.current) return;
    await attachToJob(key, start.job_id, gen, {
      reattach: Boolean(start.reused),
      clearLogs: !start.reused,
    });
  } catch (e) {
    if (gen !== attachGenRef.current) return;
    const msg = e instanceof Error ? e.message : "Analysis failed";
    usePredictionRunStore.getState().setRunError(msg);
    usePredictionRunStore.getState().finishRun();
  }
}

/** Shared attach handler registered by the Layout coordinator instance. */
let registeredAttachToJob: ((
  tickerKey: string,
  jobId: string,
  gen: number,
  options?: { reattach?: boolean; clearLogs?: boolean },
) => Promise<void>) | null = null;

export function registerPredictionRunAttach(
  fn: ((
    tickerKey: string,
    jobId: string,
    gen: number,
    options?: { reattach?: boolean; clearLogs?: boolean },
  ) => Promise<void>) | null,
) {
  registeredAttachToJob = fn;
}

export async function invokeRunPredictionAnalysis(
  ticker: string,
  horizonDays: number,
  refreshConstituents: boolean,
): Promise<void> {
  if (!registeredAttachToJob) {
    usePredictionRunStore.getState().setRunError("Analysis coordinator not ready — reload the page.");
    return;
  }
  await runPredictionAnalysis(ticker, horizonDays, refreshConstituents, registeredAttachToJob);
}

export function useRegisterPredictionRunAttach(
  attachToJob: (
    tickerKey: string,
    jobId: string,
    gen: number,
    options?: { reattach?: boolean; clearLogs?: boolean },
  ) => Promise<void>,
) {
  useEffect(() => {
    registerPredictionRunAttach(attachToJob);
    return () => registerPredictionRunAttach(null);
  }, [attachToJob]);
}
