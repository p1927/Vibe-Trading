import { afterEach, describe, expect, it, vi } from "vitest";

// A snapshot poller whose status requests keep failing must say so, not poll silently for hours.
describe("job pollers surface a lost server", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("recording: repeated snapshot failures end in onError", async () => {
    vi.useFakeTimers();
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls += 1;
        if (url.endsWith("/stream")) return new Response("", { status: 200, headers: { "content-type": "text/event-stream" } });
        if (calls === 2) return new Response(JSON.stringify({ job: { status: "running", logs: [] } }), { status: 200 });
        return new Response("down", { status: 503 });
      }),
    );
    const { streamRecordingJob } = await import("../api");
    const onDone = vi.fn();
    const onError = vi.fn();
    const run = streamRecordingJob("j9", { onDone, onError });
    await vi.advanceTimersByTimeAsync(5000 * 15);
    await run;
    expect(onDone).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(String(onError.mock.calls[0]![0])).toMatch(/Lost contact/);
  });
});
