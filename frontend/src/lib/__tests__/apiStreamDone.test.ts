import { afterEach, describe, expect, it, vi } from "vitest";

// A stream "done" event with no result is a failure to report, not a success with `undefined`.
function sse(body: string) {
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

describe("SSE done without a result", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("recording: reports an error and never calls onDone(undefined)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse("event: done\ndata: {}\n\n")));
    const { streamRecordingJob } = await import("../api");
    const onDone = vi.fn();
    const onError = vi.fn();
    await streamRecordingJob("j1", { onDone, onError });
    expect(onDone).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("forecast engine run: reports an error and never calls onDone(undefined)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse("event: done\ndata: {}\n\n")));
    const { streamForecastEngineRunJob } = await import("../api");
    const onDone = vi.fn();
    const onError = vi.fn();
    await streamForecastEngineRunJob("j2", { onDone, onError });
    expect(onDone).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("a done event that carries its result still completes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse('event: done\ndata: {"result":{"ok":true}}\n\n')));
    const { streamRecordingJob } = await import("../api");
    const onDone = vi.fn();
    const onError = vi.fn();
    await streamRecordingJob("j3", { onDone, onError });
    expect(onDone).toHaveBeenCalledWith({ ok: true });
    expect(onError).not.toHaveBeenCalled();
  });
});
