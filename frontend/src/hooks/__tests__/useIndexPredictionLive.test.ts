import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useIndexPredictionLive } from "../useIndexPredictionLive";

const gate = vi.hoisted(() => vi.fn());
const refresh = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  api: {
    getIndexPredictionRefreshGate: gate,
    refreshIndexPrediction: refresh,
    getPlanContext: () => Promise.resolve({ material_news_count: 0 }),
  },
}));

const mount = () => renderHook(() => useIndexPredictionLive({ pollMs: 300_000 }));
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  gate.mockReset();
  refresh.mockReset();
});

describe("useIndexPredictionLive gate", () => {
  for (const reason of ["market_closed", "replay_armed", "simulator_unavailable"]) {
    it(`never POSTs when the gate says ${reason}`, async () => {
      gate.mockResolvedValue({ ticker: "NIFTY", skip_reason: reason });
      const { result } = mount();
      await settle();
      expect(refresh).not.toHaveBeenCalled();
      expect(result.current.lastReason).toBe(`skipped: ${reason}`);
      expect(result.current.refreshing).toBe(false);
    });
  }

  it("POSTs when the gate is open and reports an ok refresh", async () => {
    gate.mockResolvedValue({ ticker: "NIFTY", skip_reason: null });
    refresh.mockResolvedValue({ status: "ok", reason: "scheduled_poll", artifact: null });
    const { result } = mount();
    await settle();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.lastReason).toBe("scheduled_poll");
  });

  it("a failing gate read is shown and sends no POST", async () => {
    gate.mockRejectedValue(new Error("gate down"));
    const { result } = mount();
    await settle();
    expect(refresh).not.toHaveBeenCalled();
    expect(result.current.lastReason).toBe("gate down");
  });
});
