import { act, render, renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useIndexPredictionLive } from "../useIndexPredictionLive";
import { NewsTriggerPanel } from "@/components/prediction/NewsTriggerPanel";

const planContext = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  api: {
    getIndexPredictionRefreshGate: () => Promise.resolve({ ticker: "NIFTY", skip_reason: "market_closed" }),
    refreshIndexPrediction: vi.fn(),
    getPlanContext: planContext,
  },
}));

const mount = () => renderHook(() => useIndexPredictionLive({ pollMs: 300_000 }));
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => planContext.mockReset());

// D410: "material headlines since last run: 0" means the monitor looked and found none. A failed or empty
// read is unknown, not zero.
describe("material news count is unknown until a read says otherwise", () => {
  it("is null while unread and when the read fails or carries no count", async () => {
    planContext.mockRejectedValue(new Error("down"));
    const failed = mount();
    expect(failed.result.current.materialNewsCount).toBeNull();
    await settle();
    expect(failed.result.current.materialNewsCount).toBeNull();

    planContext.mockResolvedValue({});
    const empty = mount();
    await settle();
    expect(empty.result.current.materialNewsCount).toBeNull();
  });

  it("a failed read after a good one stops showing the old count as current", async () => {
    let failSecond: (e: Error) => void = () => {};
    planContext
      .mockResolvedValueOnce({ material_news_count: 4 })
      .mockImplementationOnce(() => new Promise((_, reject) => { failSecond = reject; }));
    const { result, rerender } = renderHook(({ t }) => useIndexPredictionLive({ ticker: t, pollMs: 300_000 }), {
      initialProps: { t: "NIFTY" },
    });
    await settle();
    expect(result.current.materialNewsCount).toBe(4);
    rerender({ t: "BANKNIFTY" }); // a new read (ticker change / poll tick) that fails
    await act(async () => { failSecond(new Error("down")); });
    expect(result.current.materialNewsCount).toBeNull();
  });

  it("a real zero stays zero", async () => {
    planContext.mockResolvedValue({ material_news_count: 0 });
    const { result } = mount();
    await settle();
    expect(result.current.materialNewsCount).toBe(0);
  });

  it("the panel prints n/a for an unknown count and the number otherwise", () => {
    const { rerender } = render(<NewsTriggerPanel monitorEnabled materialNewsCount={null} />);
    expect(screen.getByText("n/a")).toBeInTheDocument();
    rerender(<NewsTriggerPanel monitorEnabled materialNewsCount={0} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
