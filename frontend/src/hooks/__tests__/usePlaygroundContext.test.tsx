import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePlaygroundContext } from "../usePlaygroundContext";
import { api } from "@/lib/api";

const ctx = (title: string) => ({ context: { headlines: [{ id: title, title }], events: [], factor_news: {} } }) as never;
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

// Each test uses its own ticker: the cache is module-level (shared by design).
describe("usePlaygroundContext cache", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("serves a repeat mount from cache while fresh", async () => {
    const spy = vi.spyOn(api, "getIndexPlaygroundContext").mockResolvedValue(ctx("a"));
    renderHook(() => usePlaygroundContext("T1", undefined));
    await settle();
    await act(async () => { vi.advanceTimersByTime(4 * 60_000); });
    const again = renderHook(() => usePlaygroundContext("T1", undefined));
    await settle();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(again.result.current.headlines[0].title).toBe("a");
  });

  it("refetches once the cached read is older than the TTL (no as-of key never freezes the news)", async () => {
    const spy = vi.spyOn(api, "getIndexPlaygroundContext").mockResolvedValueOnce(ctx("old")).mockResolvedValueOnce(ctx("new"));
    renderHook(() => usePlaygroundContext("T2", undefined));
    await settle();
    await act(async () => { vi.advanceTimersByTime(6 * 60_000); });
    const again = renderHook(() => usePlaygroundContext("T2", undefined));
    await settle();
    expect(spy).toHaveBeenCalledTimes(2);
    expect(again.result.current.headlines[0].title).toBe("new");
  });

  it("says when the shown read was fetched", async () => {
    vi.setSystemTime(new Date("2026-10-15T04:00:00Z"));
    vi.spyOn(api, "getIndexPlaygroundContext").mockResolvedValue(ctx("a"));
    const { result } = renderHook(() => usePlaygroundContext("T3", undefined));
    expect(result.current.fetchedAt).toBeNull();
    await settle();
    expect(result.current.fetchedAt).toBe(new Date("2026-10-15T04:00:00Z").getTime());
  });

  it("a failed read is not cached: the next mount asks again", async () => {
    const spy = vi.spyOn(api, "getIndexPlaygroundContext").mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce(ctx("ok"));
    const first = renderHook(() => usePlaygroundContext("T4", undefined));
    await settle();
    expect(first.result.current.error).toBe("down");
    expect(first.result.current.fetchedAt).toBeNull();
    const second = renderHook(() => usePlaygroundContext("T4", undefined));
    await settle();
    expect(spy).toHaveBeenCalledTimes(2);
    expect(second.result.current.error).toBeNull();
  });
});
