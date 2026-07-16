import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  streamIndexPredictionRun,
  type IndexFactorCatalogResponse,
  type IndexPredictionArtifact,
  type PipelineLogEntry,
} from "@/lib/api";

export interface UseIndexPredictionState {
  artifact: IndexPredictionArtifact | null;
  loading: boolean;
  running: boolean;
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
  const [error, setError] = useState<string | null>(null);
  const [pipelineLogs, setPipelineLogs] = useState<PipelineLogEntry[]>([]);
  const [pipelinePanelOpen, setPipelinePanelOpen] = useState(true);
  const [factorCatalog, setFactorCatalog] = useState<IndexFactorCatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);

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

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getIndexPrediction(ticker, horizonDays);
      if (res.status === "ok" && res.artifact) {
        setArtifact(res.artifact);
        if (res.artifact.pipeline_log?.length) {
          setPipelineLogs(res.artifact.pipeline_log);
        }
      } else {
        setArtifact(null);
        setError(res.message || "No index prediction available");
      }
    } catch (e) {
      setArtifact(null);
      setError(e instanceof Error ? e.message : "Failed to load prediction");
    } finally {
      setLoading(false);
    }
  }, [ticker, horizonDays]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const runAnalysis = useCallback(
    async (days: number, refreshConstituents = true) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setRunning(true);
      setError(null);
      setPipelineLogs([]);
      setPipelinePanelOpen(true);

      try {
        await streamIndexPredictionRun(
          {
            ticker,
            horizon_days: days,
            refresh_constituents: refreshConstituents,
          },
          {
            onLog: (entry) => {
              setPipelineLogs((prev) => [...prev, entry]);
            },
            onDone: (next) => {
              setArtifact(next);
              if (next.pipeline_log?.length) {
                setPipelineLogs(next.pipeline_log);
              }
            },
            onError: (message) => {
              setError(message);
            },
          },
          controller.signal,
        );
      } catch (e) {
        if (controller.signal.aborted) return;
        const msg = e instanceof Error ? e.message : "Analysis failed";
        setError(
          msg.includes("Network error") || msg.includes("Failed to fetch")
            ? `${msg} Ensure the API is running on port 8899. For faster runs, leave “Refresh all constituents” unchecked.`
            : msg,
        );
      } finally {
        if (!controller.signal.aborted) {
          setRunning(false);
        }
      }
    },
    [ticker],
  );

  return {
    artifact,
    loading,
    running,
    error,
    pipelineLogs,
    pipelinePanelOpen,
    factorCatalog,
    catalogLoading,
    setPipelinePanelOpen,
    runAnalysis,
    reload,
    applyArtifact: setArtifact,
  };
}
