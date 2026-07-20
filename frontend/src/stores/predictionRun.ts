import { create } from "zustand";
import type { IndexPredictionArtifact, PipelineLogEntry } from "@/lib/api";

interface PredictionRunState {
  ticker: string;
  running: boolean;
  runJobId: string | null;
  reattached: boolean;
  pipelineLogs: PipelineLogEntry[];
  runError: string | null;
  runArtifact: IndexPredictionArtifact | null;
  /** True after Layout coordinator finishes initial active-job check. */
  coordinatorReady: boolean;

  setTicker: (ticker: string) => void;
  setRunning: (running: boolean) => void;
  setRunJobId: (jobId: string | null) => void;
  setReattached: (reattached: boolean) => void;
  setPipelineLogs: (logs: PipelineLogEntry[] | ((prev: PipelineLogEntry[]) => PipelineLogEntry[])) => void;
  appendPipelineLog: (entry: PipelineLogEntry) => void;
  setRunError: (error: string | null) => void;
  setRunArtifact: (artifact: IndexPredictionArtifact | null) => void;
  setCoordinatorReady: (ready: boolean) => void;
  finishRun: () => void;
  beginRun: () => void;
  reset: () => void;
}

export const usePredictionRunStore = create<PredictionRunState>((set) => ({
  ticker: "NIFTY",
  running: false,
  runJobId: null,
  reattached: false,
  pipelineLogs: [],
  runError: null,
  runArtifact: null,
  coordinatorReady: false,

  setTicker: (ticker) => set({ ticker: ticker.toUpperCase() }),
  setRunning: (running) => set({ running }),
  setRunJobId: (runJobId) => set({ runJobId }),
  setReattached: (reattached) => set({ reattached }),
  setPipelineLogs: (logs) =>
    set((s) => ({
      pipelineLogs: typeof logs === "function" ? logs(s.pipelineLogs) : logs,
    })),
  appendPipelineLog: (entry) =>
    set((s) => ({ pipelineLogs: [...s.pipelineLogs, entry] })),
  setRunError: (runError) => set({ runError }),
  setRunArtifact: (runArtifact) => set({ runArtifact }),
  setCoordinatorReady: (coordinatorReady) => set({ coordinatorReady }),
  finishRun: () =>
    set({
      running: false,
      runJobId: null,
      reattached: false,
    }),
  beginRun: () =>
    set({
      running: true,
      runJobId: null,
      reattached: false,
      runError: null,
      runArtifact: null,
      pipelineLogs: [],
    }),
  reset: () =>
    set({
      running: false,
      runJobId: null,
      reattached: false,
      pipelineLogs: [],
      runError: null,
      runArtifact: null,
      coordinatorReady: false,
    }),
}));
