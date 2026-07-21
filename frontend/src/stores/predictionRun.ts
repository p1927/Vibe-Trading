import { create } from "zustand";
import type { IndexPredictionArtifact, PipelineLogEntry } from "@/lib/api";

const LOG_FLUSH_MS = 200;
let pendingLogEntries: PipelineLogEntry[] = [];
let logFlushTimer: ReturnType<typeof setTimeout> | null = null;
let logGeneration = 0;

function resetLogBuffer(generation: number) {
  if (logFlushTimer != null) {
    clearTimeout(logFlushTimer);
    logFlushTimer = null;
  }
  pendingLogEntries = [];
  return generation;
}

function flushPendingLogs(
  set: (fn: (s: PredictionRunState) => Partial<PredictionRunState>) => void,
  generation: number,
) {
  if (generation !== logGeneration) {
    pendingLogEntries = [];
    logFlushTimer = null;
    return;
  }
  if (!pendingLogEntries.length) {
    logFlushTimer = null;
    return;
  }
  const batch = pendingLogEntries;
  pendingLogEntries = [];
  logFlushTimer = null;
  set((s) => ({ pipelineLogs: [...s.pipelineLogs, ...batch] }));
}

interface PredictionRunState {
  ticker: string;
  running: boolean;
  runJobId: string | null;
  reattached: boolean;
  streamReconnecting: boolean;
  pipelineLogs: PipelineLogEntry[];
  runError: string | null;
  runArtifact: IndexPredictionArtifact | null;
  coordinatorReady: boolean;

  setTicker: (ticker: string) => void;
  setRunning: (running: boolean) => void;
  setRunJobId: (jobId: string | null) => void;
  setReattached: (reattached: boolean) => void;
  setStreamReconnecting: (streamReconnecting: boolean) => void;
  setPipelineLogs: (logs: PipelineLogEntry[] | ((prev: PipelineLogEntry[]) => PipelineLogEntry[])) => void;
  appendPipelineLog: (entry: PipelineLogEntry) => void;
  setRunError: (error: string | null) => void;
  setRunArtifact: (artifact: IndexPredictionArtifact | null) => void;
  setCoordinatorReady: (ready: boolean) => void;
  finishRun: () => void;
  beginRun: () => void;
  reset: () => void;
  getLogCount: () => number;
}

export const usePredictionRunStore = create<PredictionRunState>((set, get) => ({
  ticker: "NIFTY",
  running: false,
  runJobId: null,
  reattached: false,
  streamReconnecting: false,
  pipelineLogs: [],
  runError: null,
  runArtifact: null,
  coordinatorReady: false,

  setTicker: (ticker) => set({ ticker: ticker.toUpperCase() }),
  setRunning: (running) => set({ running }),
  setRunJobId: (runJobId) => set({ runJobId }),
  setReattached: (reattached) => set({ reattached }),
  setStreamReconnecting: (streamReconnecting) => set({ streamReconnecting }),
  setPipelineLogs: (logs) => {
    logGeneration = resetLogBuffer(logGeneration + 1);
    set((s) => ({
      pipelineLogs: typeof logs === "function" ? logs(s.pipelineLogs) : logs,
    }));
  },
  appendPipelineLog: (entry) => {
    const generation = logGeneration;
    pendingLogEntries.push(entry);
    if (logFlushTimer != null) return;
    logFlushTimer = setTimeout(() => flushPendingLogs(set, generation), LOG_FLUSH_MS);
  },
  setRunError: (runError) => set({ runError }),
  setRunArtifact: (runArtifact) => set({ runArtifact }),
  setCoordinatorReady: (coordinatorReady) => set({ coordinatorReady }),
  getLogCount: () => get().pipelineLogs.length + pendingLogEntries.length,
  finishRun: () => {
    flushPendingLogs(set, logGeneration);
    set({
      running: false,
      runJobId: null,
      reattached: false,
      streamReconnecting: false,
    });
  },
  beginRun: () => {
    logGeneration = resetLogBuffer(logGeneration + 1);
    set({
      running: true,
      runJobId: null,
      reattached: false,
      streamReconnecting: false,
      runError: null,
      runArtifact: null,
      pipelineLogs: [],
    });
  },
  reset: () => {
    logGeneration = resetLogBuffer(logGeneration + 1);
    set({
      running: false,
      runJobId: null,
      reattached: false,
      streamReconnecting: false,
      pipelineLogs: [],
      runError: null,
      runArtifact: null,
      coordinatorReady: false,
    });
  },
}));

/** Abort in-flight SSE/poll and reset run UI state (used by Cancel). */
export function abortPredictionRunClient(message?: string) {
  logGeneration = resetLogBuffer(logGeneration + 1);
  const store = usePredictionRunStore.getState();
  if (message) store.setRunError(message);
  store.setStreamReconnecting(false);
  store.finishRun();
}
