import { usePredictionRunStore } from "../predictionRun";

beforeEach(() => {
  usePredictionRunStore.getState().reset();
});

describe("predictionRun store — initial state", () => {
  it("has correct defaults", () => {
    const s = usePredictionRunStore.getState();
    expect(s.ticker).toBe("NIFTY");
    expect(s.running).toBe(false);
    expect(s.runJobId).toBeNull();
    expect(s.reattached).toBe(false);
    expect(s.pipelineLogs).toEqual([]);
    expect(s.runError).toBeNull();
    expect(s.runArtifact).toBeNull();
    expect(s.coordinatorReady).toBe(false);
  });
});

describe("beginRun / finishRun", () => {
  it("beginRun clears prior run state and sets running", () => {
    usePredictionRunStore.getState().setRunArtifact({ ticker: "NIFTY" } as never);
    usePredictionRunStore.getState().setRunError("old");
    usePredictionRunStore.getState().beginRun();
    const s = usePredictionRunStore.getState();
    expect(s.running).toBe(true);
    expect(s.runArtifact).toBeNull();
    expect(s.runError).toBeNull();
    expect(s.pipelineLogs).toEqual([]);
  });

  it("finishRun clears running flags but keeps runArtifact until hub merge", () => {
    usePredictionRunStore.getState().setRunning(true);
    usePredictionRunStore.getState().setRunJobId("abc123");
    usePredictionRunStore.getState().setReattached(true);
    usePredictionRunStore.getState().setRunArtifact({ ticker: "NIFTY" } as never);
    usePredictionRunStore.getState().finishRun();
    const s = usePredictionRunStore.getState();
    expect(s.running).toBe(false);
    expect(s.runJobId).toBeNull();
    expect(s.reattached).toBe(false);
    expect(s.runArtifact).not.toBeNull();
  });
});

// Since 37225ee2, appendPipelineLog buffers entries and flushes them into
// pipelineLogs in one batch after LOG_FLUSH_MS (200 ms). Fake timers let the
// tests drive that flush.
describe("pipeline logs", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("appendPipelineLog adds entries", () => {
    usePredictionRunStore.getState().appendPipelineLog({
      stage: "start",
      message: "hello",
      level: "info",
      at: "2026-01-01T00:00:00Z",
    });
    // Buffered, not yet in state, but already counted.
    expect(usePredictionRunStore.getState().pipelineLogs).toHaveLength(0);
    expect(usePredictionRunStore.getState().getLogCount()).toBe(1);

    vi.advanceTimersByTime(200);
    expect(usePredictionRunStore.getState().pipelineLogs).toHaveLength(1);
  });

  it("setPipelineLogs replaces logs", () => {
    usePredictionRunStore.getState().appendPipelineLog({
      stage: "a",
      message: "one",
      level: "info",
      at: "2026-01-01T00:00:00Z",
    });
    usePredictionRunStore.getState().setPipelineLogs([]);
    expect(usePredictionRunStore.getState().pipelineLogs).toEqual([]);
    // The discarded buffered entry must not flush in later.
    vi.advanceTimersByTime(200);
    expect(usePredictionRunStore.getState().pipelineLogs).toEqual([]);
  });
});

describe("coordinatorReady", () => {
  it("tracks layout coordinator bootstrap", () => {
    usePredictionRunStore.getState().setCoordinatorReady(true);
    expect(usePredictionRunStore.getState().coordinatorReady).toBe(true);
  });
});
