import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLivePlanContext } from "../useLivePlanContext";
import { api } from "@/lib/api";

const fresh = { monitor_enabled: true, staleness: { status: "fresh" }, live_context: { spot: 100 } };
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });
const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

describe("useLivePlanContext", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("drops the previous ticker's payload the moment the ticker changes", async () => {
    const next = deferred<typeof fresh>();
    const spy = vi.spyOn(api, "getPlanContext").mockResolvedValueOnce(fresh as never).mockReturnValueOnce(next.promise as never);
    const { result, rerender } = renderHook(({ t }) => useLivePlanContext(t), { initialProps: { t: "INFY" } });
    await settle();
    expect(result.current.staleness?.status).toBe("fresh");
    rerender({ t: "TCS" });
    expect(result.current.monitorEnabled).toBe(false);
    expect(result.current.staleness).toBeNull();
    expect(spy).toHaveBeenLastCalledWith("TCS");
  });

  it("a slow answer for the previous ticker never lands on the new one", async () => {
    const slow = deferred<typeof fresh>();
    vi.spyOn(api, "getPlanContext")
      .mockReturnValueOnce(slow.promise as never)
      .mockResolvedValueOnce({ monitor_enabled: true, staleness: { status: "stale" } } as never);
    const { result, rerender } = renderHook(({ t }) => useLivePlanContext(t), { initialProps: { t: "INFY" } });
    rerender({ t: "TCS" });
    await settle();
    await act(async () => slow.resolve(fresh));
    expect(result.current.staleness?.status).toBe("stale");
  });

  it("a failed poll clears the last 'fresh' verdict and says the live check failed", async () => {
    vi.spyOn(api, "getPlanContext").mockResolvedValueOnce(fresh as never).mockRejectedValue(new Error("HTTP 503"));
    const { result } = renderHook(() => useLivePlanContext("INFY"));
    await settle();
    expect(result.current.staleness?.status).toBe("fresh");
    await act(async () => { await vi.advanceTimersByTimeAsync(60_500); });
    expect(result.current.staleness).toBeNull();
    expect(result.current.error).toBe("HTTP 503");
  });

  it("a later good poll clears the error", async () => {
    vi.spyOn(api, "getPlanContext").mockRejectedValueOnce(new Error("down")).mockResolvedValue(fresh as never);
    const { result } = renderHook(() => useLivePlanContext("INFY"));
    await settle();
    expect(result.current.error).toBe("down");
    await act(async () => { await vi.advanceTimersByTimeAsync(60_500); });
    expect(result.current.error).toBeNull();
    expect(result.current.staleness?.status).toBe("fresh");
  });

  it("no ticker means no read at all", async () => {
    const spy = vi.spyOn(api, "getPlanContext");
    const { result } = renderHook(() => useLivePlanContext("  "));
    await settle();
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.error).toBeNull();
  });
});
