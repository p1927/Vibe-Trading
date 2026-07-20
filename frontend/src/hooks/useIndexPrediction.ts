import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type IndexFactorCatalogResponse,
  type IndexPredictionArtifact,
} from "@/lib/api";
import {
  artifactLogMatchesAsOf,
  mergePipelineLogs,
} from "@/lib/pipelineLogUtils";
import { invokeRunPredictionAnalysis } from "@/hooks/usePredictionRunCoordinator";
import { usePredictionRunStore } from "@/stores/predictionRun";

export interface UseIndexPredictionState {
  artifact: IndexPredictionArtifact | null;
  loading: boolean;
  running: boolean;
  runJobId: string | null;
  reattached: boolean;
  error: string | null;
  pipelineLogs: import("@/lib/api").PipelineLogEntry[];
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
  const [hubArtifact, setHubArtifact] = useState<IndexPredictionArtifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [hubError, setHubError] = useState<string | null>(null);
  const [pipelinePanelOpen, setPipelinePanelOpen] = useState(true);
  const [factorCatalog, setFactorCatalog] = useState<IndexFactorCatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const fetchGenRef = useRef(0);

  const running = usePredictionRunStore((s) => s.running);
  const runJobId = usePredictionRunStore((s) => s.runJobId);
  const reattached = usePredictionRunStore((s) => s.reattached);
  const pipelineLogs = usePredictionRunStore((s) => s.pipelineLogs);
  const runError = usePredictionRunStore((s) => s.runError);
  const runArtifact = usePredictionRunStore((s) => s.runArtifact);
  const coordinatorReady = usePredictionRunStore((s) => s.coordinatorReady);

  const artifact = useMemo(
    () => runArtifact ?? hubArtifact,
    [runArtifact, hubArtifact],
  );

  const error = runError ?? hubError;

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
    if (!coordinatorReady) return;

    const gen = ++fetchGenRef.current;
    const storeRunning = usePredictionRunStore.getState().running;
    setLoading(true);
    if (!storeRunning) {
      setHubError(null);
    }
    try {
      const res = await api.getIndexPrediction(ticker, horizonDays);
      if (gen !== fetchGenRef.current) return;

      const stillRunning = usePredictionRunStore.getState().running;
      if (stillRunning) {
        if (res.status === "ok" && res.artifact) {
          setHubArtifact(res.artifact);
        }
        return;
      }

      if (res.status === "ok" && res.artifact) {
        setHubArtifact(res.artifact);
        const hubLog = res.artifact.pipeline_log ?? [];
        if (hubLog.length && artifactLogMatchesAsOf(hubLog, res.artifact.as_of)) {
          usePredictionRunStore.getState().setPipelineLogs(hubLog);
        }
      } else if (!usePredictionRunStore.getState().runArtifact) {
        setHubArtifact(null);
        if (!stillRunning) {
          usePredictionRunStore.getState().setPipelineLogs([]);
        }
        setHubError(res.message || "No index prediction available");
      }
    } catch (e) {
      if (gen !== fetchGenRef.current) return;
      const stillRunning = usePredictionRunStore.getState().running;
      if (stillRunning) return;
      if (!usePredictionRunStore.getState().runArtifact) {
        setHubArtifact(null);
        usePredictionRunStore.getState().setPipelineLogs([]);
      }
      setHubError(e instanceof Error ? e.message : "Failed to load prediction");
    } finally {
      if (gen === fetchGenRef.current) {
        setLoading(false);
      }
    }
  }, [ticker, horizonDays, coordinatorReady]);

  useEffect(() => {
    if (coordinatorReady) {
      void reload();
    }
  }, [reload, coordinatorReady]);

  const applyArtifact = useCallback((next: IndexPredictionArtifact | null) => {
    if (usePredictionRunStore.getState().running) return;
    if (!next) {
      setHubArtifact(null);
      return;
    }
    setHubArtifact((prev) => {
      const incomingLog = next.pipeline_log ?? [];
      const keepPrevLog =
        prev?.pipeline_log?.length &&
        (!incomingLog.length || !artifactLogMatchesAsOf(incomingLog, next.as_of));
      const pipeline_log = keepPrevLog ? prev.pipeline_log : incomingLog.length ? incomingLog : prev?.pipeline_log;
      return { ...next, pipeline_log };
    });
    usePredictionRunStore.getState().setPipelineLogs((prev) => mergePipelineLogs(prev, next.pipeline_log));
  }, []);

  const runAnalysis = useCallback(
    async (days: number, refreshConstituents = false) => {
      setPipelinePanelOpen(true);
      await invokeRunPredictionAnalysis(ticker, days, refreshConstituents);
    },
    [ticker],
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
