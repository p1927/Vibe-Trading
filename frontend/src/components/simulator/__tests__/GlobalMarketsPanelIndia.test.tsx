import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GlobalMarketsPanel } from "../GlobalMarketsPanel";
import { api } from "@/lib/api";

vi.mock("recharts", () => ({
  Line: () => null,
  LineChart: () => null,
  ReferenceLine: () => null,
  ResponsiveContainer: ({ children }: { children?: unknown }) => <div>{children as never}</div>,
}));

const bars = [24000, 24050, 24100].map((close) => ({ close }));

describe("GlobalMarketsPanel India cards", () => {
  beforeEach(() => {
    vi.spyOn(api, "getMarketRegistry").mockResolvedValue({ markets: [] } as never);
    vi.spyOn(api, "getGlobalMacroUiCards").mockResolvedValue({ data: { global: [], currency: [] } } as never);
    vi.spyOn(api, "getHubIndexHistoryBars").mockResolvedValue({ bars } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  // D410: the 5-day sparkline's first bar is not "the previous close"; a missing prev_close is an unknown change.
  it("no prev_close from the spot read means an unknown change, not a change against the first sparkline bar", async () => {
    vi.spyOn(api, "getHubMarketDataSpot").mockResolvedValue({ spot: { ltp: 24500 } } as never);
    render(<GlobalMarketsPanel activeTab="IN" onTabChange={() => {}} />);
    await waitFor(() => expect(screen.getAllByText("24,500")).toHaveLength(4));
    expect(screen.queryByText(/\+500/)).toBeNull();
    expect(screen.queryByText(/\+2\.0\d%/)).toBeNull();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

  it("a real prev_close gives the day change", async () => {
    vi.spyOn(api, "getHubMarketDataSpot").mockResolvedValue({ spot: { ltp: 24500, prev_close: 24400 } } as never);
    render(<GlobalMarketsPanel activeTab="IN" onTabChange={() => {}} />);
    await waitFor(() => expect(screen.getAllByText(/^\+100 \+0\.41%$/)).toHaveLength(4));
  });

  it("a spot with no ltp and no prev_close keeps the last bar as price and the change unknown", async () => {
    vi.spyOn(api, "getHubMarketDataSpot").mockResolvedValue({ spot: {} } as never);
    render(<GlobalMarketsPanel activeTab="IN" onTabChange={() => {}} />);
    await waitFor(() => expect(screen.getAllByText("24,100")).toHaveLength(4));
    expect(screen.queryByText(/\+100/)).toBeNull();
  });
});
