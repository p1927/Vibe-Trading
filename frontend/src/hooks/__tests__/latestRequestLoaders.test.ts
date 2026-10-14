import { act, renderHook, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { useExternalPredictions } from "../useExternalPredictions";
import { useWatchersLive } from "../useWatchersLive";
import { api } from "@/lib/api";

describe("parameter-driven loaders drop answers for a superseded request", () => {
  afterEach(() => vi.restoreAllMocks());

  it("useExternalPredictions: a slow answer for the previous horizon never replaces the current snapshot", async () => {
    vi.spyOn(api, "getActiveExternalPredictionsRefresh").mockResolvedValue({ job: null } as never);
    let release14: (v: unknown) => void = () => {};
    vi.spyOn(api, "getExternalPredictions").mockImplementation(((_t: string, horizon: number) =>
      horizon === 14
        ? new Promise((resolve) => { release14 = resolve; })
        : Promise.resolve({ snapshot: { horizon: 30 } })) as never);
    const { result, rerender } = renderHook(({ h }) => useExternalPredictions(h, true), { initialProps: { h: 14 } });
    await waitFor(() => expect(api.getExternalPredictions).toHaveBeenCalledWith("NIFTY", 14));
    rerender({ h: 30 });
    await waitFor(() => expect(result.current.snapshot).toEqual({ horizon: 30 }));
    await act(async () => { release14({ snapshot: { horizon: 14 } }); });
    expect(result.current.snapshot).toEqual({ horizon: 30 });
    expect(result.current.loading).toBe(false);
  });

  it("useWatchersLive: an older poll tick that finishes after a newer one never overwrites it", async () => {
    let releaseSlow: (v: unknown) => void = () => {};
    vi.spyOn(api, "getWatchesLive")
      .mockImplementationOnce((() => Promise.resolve({ watches: [], fetched_at: "t0" })) as never)
      .mockImplementationOnce((() => new Promise((resolve) => { releaseSlow = resolve; })) as never)
      .mockImplementation((() => Promise.resolve({ watches: [{ watch_id: "w-new", label: "N", rules: [], last_fired_at: null }], fetched_at: "t2" })) as never);
    const { result } = renderHook(() => useWatchersLive({ sessionId: "a", pollMs: 0 }));
    await waitFor(() => expect(api.getWatchesLive).toHaveBeenCalledTimes(1));
    act(() => { void result.current.refresh(); }); // slow tick
    act(() => { void result.current.refresh(); }); // newer tick, answers first
    await waitFor(() => expect(result.current.watches.map((w) => w.watch_id)).toEqual(["w-new"]));
    await act(async () => { releaseSlow({ watches: [{ watch_id: "w-old", label: "O", rules: [], last_fired_at: null }], fetched_at: "t1" }); });
    expect(result.current.watches.map((w) => w.watch_id)).toEqual(["w-new"]);
    expect(result.current.fetchedAt).toBe("t2");
  });

  it("useWatchersLive: a slow poll for the previous session never lands under the new session", async () => {
    let releaseA: (v: unknown) => void = () => {};
    vi.spyOn(api, "getWatchesLive").mockImplementation(((args: { sessionId?: string }) =>
      args.sessionId === "a"
        ? new Promise((resolve) => { releaseA = resolve; })
        : Promise.resolve({ watches: [{ watch_id: "w-b", label: "B", rules: [], last_fired_at: null }], fetched_at: "t" })) as never);
    const { result, rerender } = renderHook(({ s }) => useWatchersLive({ sessionId: s, pollMs: 0 }), { initialProps: { s: "a" } });
    await waitFor(() => expect(api.getWatchesLive).toHaveBeenCalledTimes(1));
    rerender({ s: "b" });
    await waitFor(() => expect(result.current.watches.map((w) => w.watch_id)).toEqual(["w-b"]));
    await act(async () => { releaseA({ watches: [{ watch_id: "w-a", label: "A", rules: [], last_fired_at: null }], fetched_at: "old" }); });
    expect(result.current.watches.map((w) => w.watch_id)).toEqual(["w-b"]);
    expect(result.current.loading).toBe(false);
  });
});
