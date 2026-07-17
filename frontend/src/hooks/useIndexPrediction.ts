import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  streamIndexPredictionJob,
  streamIndexPredictionRun,
  type IndexFactorCatalogResponse,
  type IndexPredictionArtifact,
  type PipelineLogEntry,
} from "@/lib/api";
import {
  artifactLogMatchesAsOf,
  mergePipelineLogs,
} from "@/lib/pipelineLogUtils";

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

export interface UseIndexPredictionState {
  artifact: IndexPredictionArtifact | null;
  loading: boolean;
  running: boolean;
  runJobId: string | null;
  reattached: boolean;
  error: string | null;
  pipelineLogs: PipelineLogEntry[];
  pipelinePanelOpen: boolean;
  factorCatalog: IndexFactorCatalogResponse | null;
  catalogLoading: boolean;
  setPipelinePanelOpen: (open: boolean) => void;
  runAnalysis: (horizonDays: number, refreshConstituents?: boolean) => Promise<void>;
  reload: () => Promise<void>;
  applyArtifact: (next: IndexPredictionArtifact | null) => void;
}

export function useIndexPrediction(
  ticker = "NIFTY",
  horizonDays = 14,
): UseIndexPredictionState {
  const [artifact, setArtifact] = useState<IndexPredictionArtifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runJobId, setRunJobId] = useState<string | null>(null);
  const [reattached, setReattached] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pipelineLogs, setPipelineLogs] = useState<PipelineLogEntry[]>([]);
  const [pipelinePanelOpen, setPipelinePanelOpen] = useState(true);
  const [factorCatalog, setFactorCatalog] = useState<IndexFactorCatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const attachGenRef = useRef(0);
  const fetchGenRef = useRef(0);
  const reattachCheckedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setCatalogLoading(true);
    void api
      .getIndexPredictionFactors()
      .then((res) => {
        if (!cancelled && res.status === "ok") setFactorCatalog(res);
      })
      .catch(() => {
        if (!cancelled) setFactorCatalog(null);
      })
      .finally(() => {
        if (!cancelled) setCatalogLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const finishRun = useCallback(() => {
    clearStoredRunJobId(ticker);
    setRunning(false);
    setRunJobId(null);
    setReattached(false);
  }, [ticker]);

  const attachToJob = useCallback(
    async (jobId: string, gen: number, options?: { reattach?: boolean; clearLogs?: boolean }) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setRunning(true);
      setRunJobId(jobId);
      setReattached(Boolean(options?.reattach));
      setPipelinePanelOpen(true);
      writeStoredRunJobId(ticker, jobId);
      if (options?.clearLogs !== false) {
        setPipelineLogs([]);
      }

      try {
        await streamIndexPredictionJob(
          jobId,
          {
            onLog: (entry) => {
              if (gen !== attachGenRef.current) return;
              setPipelineLogs((prev) => [...prev, entry]);
            },
            onDone: (next) => {
              if (gen !== attachGenRef.current) return;
              setArtifact(next);
              setPipelineLogs((prev) => mergePipelineLogs(prev, next.pipeline_log));
              finishRun();
            },
            onError: (message) => {
              if (gen !== attachGenRef.current) return;
              setError(message);
              finishRun();
            },
          },
          controller.signal,
        );
      } catch (e) {
        if (controller.signal.aborted) return;
        if (gen !== attachGenRef.current) return;
        const msg = e instanceof Error ? e.message : "Analysis failed";
        setError(
          msg.includes("Network error") || msg.includes("Failed to fetch")
            ? `${msg} Ensure the API is running on port 8899. For faster runs, leave “Refresh all constituents” unchecked.`
            : msg,
        );
        finishRun();
      }
    },
    [finishRun, ticker],
  );

  const reload = useCallback(async () => {
    const gen = ++fetchGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getIndexPrediction(ticker, horizonDays);
      if (gen !== fetchGenRef.current) return;
      if (attachGenRef.current > 0) return;
      if (res.status === "ok" && res.artifact) {
        setArtifact(res.artifact);
        const hubLog = res.artifact.pipeline_log ?? [];
        if (hubLog.length && artifactLogMatchesAsOf(hubLog, res.artifact.as_of)) {
          setPipelineLogs(hubLog);
        }
      } else {
        setArtifact(null);
        setPipelineLogs([]);
        setError(res.message || "No index prediction available");
      }
    } catch (e) {
      if (gen !== fetchGenRef.current) return;
      if (attachGenRef.current > 0) return;
      setArtifact(null);
      setPipelineLogs([]);
      setError(e instanceof Error ? e.message : "Failed to load prediction");
    } finally {
      if (gen === fetchGenRef.current) {
        setLoading(false);
      }
    }
  }, [ticker, horizonDays]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    reattachCheckedRef.current = false;
  }, [ticker]);

  useEffect(() => {
    if (reattachCheckedRef.current) return;
    reattachCheckedRef.current = true;

    let cancelled = false;

    const tryReattach = async () => {
      try {
        const active = await api.getActiveIndexPredictionRun(ticker);
        if (cancelled) return;
        const job = active.job;
        if (job?.job_id && ACTIVE_JOB_STATUSES.has(job.status)) {
          const gen = ++attachGenRef.current;
          await attachToJob(job.job_id, gen, { reattach: true });
          return;
        }
        clearStoredRunJobId(ticker);
      } catch {
        const stored = readStoredRunJobId(ticker);
        if (stored && !cancelled) {
          const gen = ++attachGenRef.current;
          await attachToJob(stored, gen, { reattach: true });
        }
      }
    };

    void tryReattach();
    return () => {
      cancelled = true;
    };
  }, [attachToJob, ticker]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const applyArtifact = useCallback((next: IndexPredictionArtifact | null) => {
    if (attachGenRef.current > 0) return;
    if (!next) {
      setArtifact(null);
      return;
    }
    setArtifact((prev) => {
      const incomingLog = next.pipeline_log ?? [];
      const keepPrevLog =
        prev?.pipeline_log?.length &&
        (!incomingLog.length || !artifactLogMatchesAsOf(incomingLog, next.as_of));
      const pipeline_log = keepPrevLog ? prev.pipeline_log : incomingLog.length ? incomingLog : prev?.pipeline_log;
      return { ...next, pipeline_log };
    });
    setPipelineLogs((prev) => mergePipelineLogs(prev, next.pipeline_log));
  }, []);

  const runAnalysis = useCallback(
    async (days: number, refreshConstituents = true) => {
      abortRef.current?.abort();
      fetchGenRef.current += 1;
      const gen = ++attachGenRef.current;

      setRunning(true);
      setRunJobId(null);
      setReattached(false);
      setError(null);
      setPipelineLogs([]);
      setPipelinePanelOpen(true);

      const body = {
        ticker,
        horizon_days: days,
        refresh_constituents: refreshConstituents,
      };

      try {
        const start = await api.startIndexPredictionRun(body);
        if (gen !== attachGenRef.current) return;
        await attachToJob(start.job_id, gen, {
          reattach: Boolean(start.reused),
          clearLogs: true,
        });
      } catch (e) {
        if (gen !== attachGenRef.current) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
          const controller = new AbortController();
          abortRef.current = controller;
          try {
            await streamIndexPredictionRun(
              body,
              {
                onLog: (entry) => {
                  if (gen !== attachGenRef.current) return;
                  setPipelineLogs((prev) => [...prev, entry]);
                },
                onDone: (next) => {
                  if (gen !== attachGenRef.current) return;
                  setArtifact(next);
                  setPipelineLogs((prev) => mergePipelineLogs(prev, next.pipeline_log));
                },
                onError: (message) => {
                  if (gen !== attachGenRef.current) return;
                  setError(message);
                },
              },
              controller.signal,
            );
          } catch (legacyErr) {
            if (controller.signal.aborted) return;
            if (gen !== attachGenRef.current) return;
            const msg = legacyErr instanceof Error ? legacyErr.message : "Analysis failed";
            setError(msg);
          } finally {
            if (!controller.signal.aborted && gen === attachGenRef.current) {
              setRunning(false);
              setRunJobId(null);
            }
          }
          return;
        }
        const msg = e instanceof Error ? e.message : "Analysis failed";
        setError(msg);
        setRunning(false);
        setRunJobId(null);
      }
    },
    [attachToJob, ticker],
  );

  return {
    artifact,
    loading,
    running,
    runJobId,
    reattached,
    error,
    pipelineLogs,
    pipelinePanelOpen,
    factorCatalog,
    catalogLoading,
    setPipelinePanelOpen,
    runAnalysis,
    reload,
    applyArtifact,
  };
}
